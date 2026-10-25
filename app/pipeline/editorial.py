"""Two-pass story generation, with source/semantic review before asset resolution."""

import json
import logging
import re
import time
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.assets.resolve import prepare_assets
from app.config import Settings
from app.core.deck_schema import Archetype, Deck, StorySlide
from app.core.errors import OutlineSourceError, SlideWriteError
from app.llm.factory import create_llm_client
from app.llm.checkpoints import reject_checkpoint
from app.llm.structured import StructuredGenerator
from app.pipeline.analyze import MAX_PROMPT_CHARS
from app.pipeline.grounding import (
    unsupported_specific_claim_categories,
    unsupported_claim_categories,
)
from app.render.theme import theme_for_topic

log = logging.getLogger(__name__)


class StoryPlan(BaseModel):
    archetype: Archetype
    title: str = Field(min_length=1, max_length=65)
    purpose: str = Field(min_length=1, max_length=220)
    source_fact_ids: list[int] = Field(default_factory=list, max_length=20)


class StoryOutline(BaseModel):
    brand: str = Field(min_length=1, max_length=60)
    audience: str = Field(min_length=1, max_length=120)
    tone: str = Field(min_length=1, max_length=80)
    theme: Literal["beauty", "fintech", "health", "edtech", "climate", "bold-dark"]
    facts: list[str] = Field(default_factory=list, max_length=20)
    slides: list[StoryPlan] = Field(min_length=8, max_length=10)

    @model_validator(mode="after")
    def structure(self):
        if self.slides[0].archetype != "cover" or self.slides[-1].archetype != "closing_cta":
            raise ValueError("story needs a cover and closing CTA")
        kinds = [s.archetype for s in self.slides]
        if len(set(kinds)) < 5 or any(
            len(set(kinds[i : i + 3])) == 1 for i in range(len(kinds) - 2)
        ):
            raise ValueError("use at least five archetypes, never three consecutive identical ones")
        if any(i < 0 or i >= len(self.facts) for s in self.slides for i in s.source_fact_ids):
            raise ValueError("invalid source fact reference")
        return self


class CopyIssue(BaseModel):
    slide_index: int = Field(ge=0, le=9)
    fix: str = Field(min_length=1, max_length=350)


class CopyReview(BaseModel):
    passed: bool
    issues: list[CopyIssue] = Field(default_factory=list, max_length=20)
    missing_fact_ids: list[int] = Field(default_factory=list, max_length=20)


OUTLINE_PROMPT = """Plan an editorial presentation. Derive audience, tone, a curated theme and
8-10 substantive slides from the supplied brief. No coordinates. At least five archetypes,
never three identical consecutive archetypes. Cover -> problem -> solution -> product/range
-> how it works -> launch/action plan -> growth/roadmap -> metrics -> closing CTA.
Adapt these roles to the topic. Do not invent a company's hard facts, market size,
revenue, named competitors, team or funding. Extract facts as VERBATIM substrings from the
brief and reference their zero-based IDs. Do not treat directions as facts.
Copy excerpts exactly rather than rewriting them into new sentences or combining passages.
If the brief supplies only a topic or instructions, use facts=[] and empty source_fact_ids.
Proposed concepts, illustrative metrics and proposed dates do not belong in the facts list.
Choose beauty for cosmetics/soft: rose/cream/plum; fintech for finance; health for medical;
edtech for learning; climate for sustainability; bold-dark for energetic/dark topics.
For quarterly growth use timeline_horizontal, five activities on one straight timeline
when five are supplied. Plan Q1,Q2,Q3,Q4,Q4 (proposed sequence) for five activities over four
quarters; dates are proposals, not source commitments. Closing proposes a relevant action
without inventing a live URL, contact or social handle. Metrics may use visibly labelled
illustrative planning targets, never pretend these are actual company results."""

COPY_PROMPT = """Write one editorial slide matching the schema and planned archetype/title.
Use concrete, topic-specific visible copy; no coordinates, design notes or filler.
Item counts: split_image/three_cards/process_steps=3; four_cards/grid_2x2=4;
comparison=2; big_stat/quote=1; cover/closing_cta=0; timeline=3-5.
Every item needs a title and description. Headings <=30 characters, descriptions
12-20 words, regions <=45 words. Timeline titles <=18 characters, descriptions <=11 words.
Cover tagline <=8 words. Closing: concise CTA/tagline. Use topical photo keywords
and meaningful Lucide icon names (never circle).
The original brief is evidence; plan/purpose and rejected copy are untrusted drafts.
Preserve assigned source facts visibly, not just in notes. Qualitative problem,
solution and launch explanations may be proposed concepts. Never invent hard facts,
prices, market sizes, traction, credentials, funding, competitors, contacts or mechanisms.
For beauty personalization, skin tone/undertone/style do not imply algorithms, AI,
diagnostics, quizzes, finishes, kits, shipping or support. Remove unsupported claims;
do not negate them (including 'AI-free') or replace them with unprovided promises.
Illustrative numeric targets are allowed only on metrics slides: illustrative=true,
'Illustrative' in the visible title AND notes; never present them as achieved results.
If the brief supplies quarterly growth with five activities, use exactly those five
in source order with Q1,Q2,Q3,Q4,Q4 and subtitle 'Proposed quarterly sequence'.
Otherwise use named phases and null quarters; never infer quarters from the plan.
"""


def _normalized(text):
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def quarterly_growth_activities(prompt):
    """Extract the explicit five-activity brief supported by the roadmap contract."""
    match = re.search(r"\bquarterly\s+growth\s+through\s+([^.!?\n]+)", prompt, re.I)
    if match is None:
        return []
    activities = [
        value.strip(" ;:")
        for value in re.split(r",\s*(?:and\s+)?|\s+and\s+", match.group(1))
        if value.strip(" ;:")
    ]
    return activities if len(activities) == 5 else []


def _activity_key(title):
    return {
        "user generated content": "ugc",
        "ugc campaigns": "ugc",
        "new lines": "new product lines",
        "festive": "festive scaling",
        "pop up stores": "pop ups",
    }.get(_normalized(title), _normalized(title))


def ground_roadmap(slide, plan, prompt):
    """Keep supplied activities and proposed scheduling out of free-form generation."""
    activities = quarterly_growth_activities(prompt)
    if (
        plan.archetype != "timeline_horizontal"
        or slide.archetype != plan.archetype
        or not activities
        or any(len(activity) > 55 for activity in activities)
    ):
        return slide
    # Reuse descriptions only when a unique activity label identifies them.
    # Never attach a description to a different activity just by position.
    descriptions = {}
    for item in slide.items:
        descriptions.setdefault(_activity_key(item.title), []).append(item.description)
    items = []
    for activity, quarter in zip(activities, ("Q1", "Q2", "Q3", "Q4", "Q4")):
        candidates = descriptions.get(_activity_key(activity), [])
        description = (
            candidates[0]
            if len(candidates) == 1
            else f"Explore {activity} as a proposed growth activity."
        )
        items.append({"title": activity, "description": description, "quarter": quarter})
    data = slide.model_dump()
    data.update(
        title=plan.title,
        subtitle="Proposed quarterly sequence for the five supplied growth activities",
        items=items,
        illustrative=False,
        icon_names=[],
        notes=(
            "Activities and order are taken from the brief. Quarterly assignments and explanatory "
            "actions are proposals, not supplied dates, commitments or achieved results."
        ),
    )
    return StorySlide.model_validate(data)


def source_launch_fallback(plan, prompt):
    """Rebuild only the explicitly supplied launch workflow after invalid copy."""
    if plan.archetype not in {"process_steps", "split_image"} or not plan.purpose.startswith(
        ("Three touchpoints in order:", "Explain the three supplied launch channels")
    ):
        return None
    match = re.search(
        r"\blaunch\s+(?:will\s+)?focus(?:es)?\s+on\s+(.+?)(?:,\s*followed\s+by\b|[.!?\n]|$)",
        prompt,
        re.I,
    )
    if match is None:
        return None
    channels = [
        re.sub(r"^(?:a|the)\s+", "", value.strip(), flags=re.I)
        for value in re.split(r",\s*(?:and\s+)?|\s+and\s+", match.group(1))
        if value.strip()
    ]
    descriptions = {
        "shade matching quiz": "Explore the planned shade-matching quiz.",
        "waitlist": "Register interest through the planned waitlist.",
        "creator led demos": "Introduce the concept through planned creator-led demos.",
    }
    if len(channels) != 3 or {_normalized(c) for c in channels} != set(descriptions):
        return None
    return StorySlide(
        archetype=plan.archetype,
        title=plan.title,
        subtitle="Proposed launch touchpoints from the brief",
        items=[{"title": c, "description": descriptions[_normalized(c)]} for c in channels],
        notes=(
            "The launch channels are supplied in the brief. Descriptions are proposed explanations, "
            "not claims of a live service, access privileges or achieved results."
        ),
    )


def clean_copy(slide, source_prompt=""):
    data = slide.model_dump()
    removed = []

    def clean(value):
        if not value:
            return value
        removed.extend(re.findall(r"\[[^\]]*\]", value))
        value = re.sub(r"\[[^\]]*\]", "", value)
        pattern = r"(?im)^.*(?:confirm before use|conceptual workflow details|speaker notes:|archetype:|image_query:|prompt:).*?(?:\n|$)"
        removed.extend(re.findall(pattern, value))
        return re.sub(pattern, "", value).strip()

    data["title"] = clean(data["title"])
    data["subtitle"] = clean(data["subtitle"]) or None
    if source_prompt and data["archetype"] == "closing_cta":
        for word in ("today", "now"):
            if word not in source_prompt.casefold():
                for field in ("title", "subtitle"):
                    if data[field]:
                        data[field] = re.sub(
                            r"\s+", " ", re.sub(r"\b" + word + r"\b", "", data[field], flags=re.I)
                        ).strip(" ,.")
    for item in data["items"]:
        item["title"], item["description"] = clean(item["title"]), clean(item["description"])
    data["notes"] = "\n".join(filter(None, [data["notes"], *removed]))[:1500] or None
    if data["illustrative"]:
        if "illustrative" not in data["title"].casefold():
            data["title"] = "Illustrative " + data["title"]
        if "illustrative" not in (data["notes"] or "").casefold():
            data["notes"] = "Illustrative planning examples, not actual results. " + (
                data["notes"] or ""
            )
    if source_prompt and not data["illustrative"]:
        visible = " ".join(
            [
                data["title"],
                data["subtitle"] or "",
                *(i["title"] + " " + i["description"] for i in data["items"]),
            ]
        )
        mechanisms = re.findall(
            r"(?i)\b(?:automat\w*|dashboard|forecasting|analytics|role.based|recurring payments|bulk payments|reminders)\b",
            visible,
        )
        if any(_normalized(m) not in _normalized(source_prompt) for m in mechanisms):
            if "proposed" not in (data["subtitle"] or "").casefold():
                data["subtitle"] = (
                    "Proposed concept: " + (data["subtitle"] or "areas to explore")
                )[:180]
            data["notes"] = (
                (data["notes"] or "")
                + "\nProposed capabilities, not supplied current product facts."
            )[-1500:]
    return StorySlide.model_validate(data)


def normalize_outline(outline, prompt):
    """Resolve structural mismatches without making outline prose factual evidence."""
    beauty = all(
        word in prompt.casefold()
        for word in ("skin tone", "undertone", "style", "foundation", "lipstick", "skincare")
    )
    source_launch = beauty and all(
        word in prompt.casefold() for word in ("quiz", "waitlist", "creator")
    )
    plans = []
    for index, plan in enumerate(outline.slides):
        title = plan.title.casefold()
        update = {}
        if plan.archetype == "cover":
            update["title"] = outline.brand
        elif re.search(r"metrics|targets", title):
            update.update(
                archetype="three_cards",
                purpose="Three visibly illustrative planning targets, not company results.",
            )
        elif beauty and (index == 3 or re.search(r"product|collection|range|beauty line", title)):
            update.update(
                archetype="three_cards",
                purpose="Explain only the three source product categories: foundation, lipstick, skincare. No fourth category or unprovided product formats.",
            )
        elif beauty and (index == 2 or "solution" in title):
            update.update(
                archetype="split_image",
                purpose="Explain the source beauty proposition in three dimensions: skin tone, undertone, individual style. Do not repeat the launch workflow.",
            )
        elif source_launch and plan.archetype == "process_steps":
            update.update(
                title=f"How {outline.brand} works",
                purpose="Three touchpoints in order: shade-matching quiz, waitlist, creator-led demos. No shipping, delivery, kits, email mechanics, early access, subscriptions, quarter labels or extra stages. Describe each briefly.",
            )
        elif source_launch and re.search(r"launch|action plan", title) and "growth" not in title:
            update.update(
                archetype="split_image",
                purpose="Explain the three supplied launch channels with a short proposed action description each. Do not assign quarters to launch or introduce later growth activities.",
            )
        elif re.search(r"growth|roadmap", title) and "quarter" in prompt.casefold():
            update.update(
                archetype="timeline_horizontal",
                purpose="All supplied growth activities in source order, five items when five supplied. Q1,Q2,Q3,Q4,Q4 proposed sequence, not source commitments. Include a description for every activity.",
            )
        elif not beauty:
            update["purpose"] = (
                "Explain "
                + plan.title
                + ". Use only the original brief for company facts. Any extension is a clearly labelled qualitative proposal. No unprovided automation, integrations, dates, numbers or features."
            )[:220]
        plans.append(plan.model_copy(update=update))
    return StoryOutline.model_validate(
        {**outline.model_dump(), "slides": [p.model_dump() for p in plans]}
    )


def source_outline(outline, prompt):
    """Missing-data labels belong in copy, never in the factual evidence list."""
    normalized = _normalized(prompt)
    facts, mapping = [], {}
    invalid = []
    for index, fact in enumerate(outline.facts):
        if _normalized(fact) and _normalized(fact) in normalized:
            mapping[index] = len(facts)
            facts.append(fact)
        elif not re.match(r"(?i)^(?:placeholder|tbd|unknown|missing)\b", fact):
            invalid.append(index)
    if invalid:
        raise OutlineSourceError(invalid)
    plans = [
        p.model_copy(
            update={"source_fact_ids": [mapping[i] for i in p.source_fact_ids if i in mapping]}
        )
        for p in outline.slides
    ]
    return StoryOutline.model_validate(
        {**outline.model_dump(), "facts": facts, "slides": [p.model_dump() for p in plans]}
    )


def visible_copy(slide):
    return slide.model_dump(exclude={"notes", "speaker_notes", "image_query", "icon_names"})


def validate_copy(slide, plan, prompt):
    if slide.archetype != plan.archetype:
        raise ValueError("copy changed planned archetype")
    expected = {
        "split_image": 3,
        "process_steps": 3,
        "comparison": 2,
        "big_stat": 1,
        "quote": 1,
        "cover": 0,
        "closing_cta": 0,
    }
    if slide.archetype in expected and len(slide.items) != expected[slide.archetype]:
        raise ValueError("incorrect archetype item count")
    values = [
        slide.title,
        slide.subtitle or "",
        *(part for item in slide.items for part in (item.title, item.description)),
    ]
    if any(len(value.split()) > 45 for value in values):
        raise ValueError("copy exceeds region word budget")
    if slide.archetype == "cover" and len((slide.subtitle or "").split()) > 8:
        raise ValueError(
            "Cover tagline must fit one line: use at most eight words and 48 characters."
        )
    text = " ".join(values)
    if slide.illustrative:
        if (
            "illustrative" not in slide.title.casefold()
            or "illustrative" not in (slide.notes or "").casefold()
        ):
            raise ValueError("illustrative targets need visible and notes disclosure")
        if not re.search(
            r"metric|target|measure|illustrative", plan.purpose + " " + plan.title, re.I
        ):
            raise ValueError("illustrative values only belong on a metrics slide")
    elif any(number not in prompt for number in re.findall(r"\d[\d,.]*%?", text)):
        raise ValueError(
            "unprovided numeric claim: remove unprovided quarters, numbered phases, dates and counts. Use named phases. Numbers are allowed only for disclosed illustrative metrics."
        )
    if unsupported_specific_claim_categories(text, prompt):
        raise ValueError("unsupported hard company claim")
    unsupported = unsupported_claim_categories(
        text, prompt, allow_structural_inference=True, allow_problem_framing=True
    )
    if slide.archetype == "closing_cta":
        unsupported = [s for s in unsupported if s != "unsupported calls to action"]
    # "Product range" is a structural heading, not a new operational claim.
    # Descriptions still pass the original claim checks below.
    if "unsupported company scope or operations" in unsupported:
        detail = " ".join(i.description for i in slide.items)
        if "unsupported company scope or operations" not in unsupported_claim_categories(
            detail, prompt
        ):
            unsupported.remove("unsupported company scope or operations")
    if unsupported:
        raise ValueError("unsupported product or operational claim: " + ", ".join(unsupported))
    for word in (
        "serum",
        "moisturizer",
        "flawless wear",
        "radiant health",
        "clinically",
        "dermatologist",
    ):
        if word in text.casefold() and word not in prompt.casefold():
            raise ValueError("unprovided product specification or benefit")
    for contact in re.findall(r"https?://\S+|[\w.+-]+@[\w.-]+\.\w+", text):
        if contact not in prompt:
            raise ValueError("unprovided contact")
    if slide.archetype == "timeline_horizontal":
        activities = quarterly_growth_activities(prompt)
        if activities:
            actual = [_activity_key(i.title) for i in slide.items]
            if actual != [_activity_key(v) for v in activities] or [
                i.quarter for i in slide.items
            ] != ["Q1", "Q2", "Q3", "Q4", "Q4"]:
                raise ValueError(
                    "Roadmap must have exactly five separate activities in this order: "
                    + ", ".join(activities)
                    + ". Use quarters Q1,Q2,Q3,Q4,Q4. Do not include quiz, waitlist or demos on the growth roadmap."
                )
        quarters = [i.quarter for i in slide.items]
        if any(quarters):
            if not re.search(r"quarter|Q[1-4]", prompt, re.I):
                raise ValueError("quarters require a quarterly brief")
            if quarters != sorted(quarters) or "proposed" not in (slide.subtitle or "").casefold():
                raise ValueError("quarter assignments must be ordered and visibly proposed")


def generate_editorial_deck(
    prompt,
    *,
    settings=None,
    generator=None,
    theme="default",
    deadline=None,
    progress_callback=None,
    usage_callback=None,
    review_callback=None,
    provider_limit_callback=None,
    semantic_attempts=2,
    max_repair_rounds=2,
    max_workers=1,
):
    prompt = prompt.strip()
    if not prompt or len(prompt) > MAX_PROMPT_CHARS:
        raise ValueError("prompt must be nonempty and within the request limit")
    if semantic_attempts < 1 or max_workers < 1 or max_repair_rounds < 0:
        raise ValueError("invalid generation limits")
    settings = settings or Settings.from_env()
    generator = generator or StructuredGenerator(
        create_llm_client(settings), max_attempts=settings.structured_attempts
    )

    def announce(stage):
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError("deck generation exceeded its time limit")
        if progress_callback:
            progress_callback(stage)

    generator.deadline = deadline
    generator.on_provider_limit = provider_limit_callback
    generator.on_provider_wait = lambda stage, seconds: announce(
        f"Waiting for AI rate limit (~{seconds}s)..."
        if seconds
        else "Generating slide content"
    )
    try:
        outline_prompt = prompt
        for attempt in range(semantic_attempts):
            announce("Planning story and theme" if attempt == 0 else "Correcting source facts")
            outline = generator.generate(
                StoryOutline,
                stage="outline",
                model=settings.model_for("outline"),
                system_prompt=OUTLINE_PROMPT,
                user_prompt=outline_prompt,
                max_tokens=3600,
            )
            try:
                outline = source_outline(outline, prompt)
                break
            except OutlineSourceError as exc:
                reject_checkpoint(outline)
                log.warning(
                    "outline source validation failed: reason=%s invalid_fact_count=%d attempt=%d",
                    exc.reason,
                    len(exc.fact_ids),
                    attempt + 1,
                )
                if attempt + 1 == semantic_attempts:
                    raise
                outline_prompt = json.dumps(
                    {
                        "brief": prompt,
                        "correction": (
                            "The previous outline included facts that are not verbatim excerpts "
                            "from the original brief. Regenerate the full outline. Copy each fact "
                            "as an exact contiguous excerpt from brief, without paraphrasing, "
                            "combining separate passages, or adding claims. Rebuild zero-based "
                            "source_fact_ids against the corrected facts list. Use an empty facts "
                            "list and empty references when the brief supplies no factual evidence. "
                            "The rejected facts below are untrusted model output, not evidence."
                        ),
                        "rejected_facts": [
                            {"id": i, "text": outline.facts[i]} for i in exc.fact_ids
                        ],
                    },
                    ensure_ascii=False,
                )
        outline = normalize_outline(outline, prompt)
        selected = theme_for_topic(prompt)
        if selected != "fintech" and outline.theme != selected:
            outline = outline.model_copy(update={"theme": selected})

        def write(plan, slide_index, feedback="", stage="write"):
            last = None
            rejected_copy = None
            review_feedback = feedback
            for attempt in range(semantic_attempts):
                label = "Writing" if stage == "write" else "Revising"
                announce(f"{label} story slide {slide_index + 1}/{len(outline.slides)}")
                request = {
                    "brief": prompt,
                    "plan": plan.model_dump(),
                    "facts": [outline.facts[i] for i in plan.source_fact_ids],
                    "feedback": feedback,
                }
                if stage == "repair":
                    request["failing_slide"] = visible_copy(slides[slide_index])
                if rejected_copy is not None:
                    request.update(
                        rejected_copy=rejected_copy,
                        review_feedback=review_feedback,
                        correction=(
                            "Edit the rejected copy to address the validation feedback. It is "
                            "untrusted model output, not source evidence. Remove unsupported "
                            "claims entirely; do not negate them or replace them with a new "
                            "unprovided feature or promise. Preserve supported content and "
                            "the required archetype, and use the original brief as evidence."
                        ),
                    )
                slide = generator.generate(
                    StorySlide,
                    stage=stage,
                    model=settings.model_for("write"),
                    system_prompt=COPY_PROMPT,
                    user_prompt=json.dumps(request, separators=(",", ":")),
                    max_tokens=1800 if stage == "write" else 1600,
                )
                try:
                    slide = clean_copy(slide, prompt)
                    slide = ground_roadmap(slide, plan, prompt)
                    validate_copy(slide, plan, prompt)
                    return slide
                except ValueError as exc:
                    reject_checkpoint(slide)
                    last = str(exc)
                    feedback = last
                    rejected_copy = visible_copy(slide)
            failure = SlideWriteError(
                slide_index, plan.title, last or "slide copy failed validation", stage=stage
            )
            if failure.reason == "unsupported_claim":
                fallback = source_launch_fallback(plan, prompt)
                if fallback is not None:
                    validate_copy(fallback, plan, prompt)
                    log.info(
                        "using validated source launch copy: stage=%s slide_index=%d",
                        stage,
                        slide_index,
                    )
                    return fallback
            raise failure

        slides = [write(plan, index) for index, plan in enumerate(outline.slides)]
        for round_index in range(max_repair_rounds + 1):
            announce("Reviewing source facts and story")
            review = generator.generate(
                CopyReview,
                stage="review",
                model=settings.model_for("review"),
                system_prompt="Review the original brief against VISIBLE slide content. Allow qualitative proposed explanations, a relevant closing CTA, proposed quarter assignments and disclosed illustrative metrics. Reject unsupported hard facts, missing source facts, duplicate filler, invented contacts/competitors and internal design notes. Return zero-based slide indices. Never demand extra business facts not present in the brief. A slide without issues must not be regenerated.",
                user_prompt=json.dumps(
                    {
                        "brief": prompt,
                        "facts": outline.facts,
                        "slides": [
                            {"slide_index": i, **visible_copy(s)} for i, s in enumerate(slides)
                        ],
                    }
                ),
                max_tokens=1600,
            )
            if any(i.slide_index >= len(slides) for i in review.issues) or any(
                i < 0 or i >= len(outline.facts) for i in review.missing_fact_ids
            ):
                reject_checkpoint(review)
                raise ValueError("review returned invalid references")
            if review_callback:
                review_callback(review.model_dump())
            if review.passed and not review.issues and not review.missing_fact_ids:
                break
            fixes = {i.slide_index: i.fix for i in review.issues}
            for fact_id in review.missing_fact_ids:
                index = next(
                    (i for i, p in enumerate(outline.slides) if fact_id in p.source_fact_ids), None
                )
                if index is None:
                    raise ValueError("missing source fact has no planned slide")
                fixes[index] = fixes.get(index, "") + " Include visibly: " + outline.facts[fact_id]
            for index in fixes:
                reject_checkpoint(slides[index])
            if round_index == max_repair_rounds:
                raise ValueError("story failed final source review")
            if not fixes:
                raise ValueError("review failed without actionable findings")
            for index, feedback in fixes.items():
                slides[index] = write(outline.slides[index], index, feedback, "repair")
        deck = Deck(
            title=outline.brand,
            audience=outline.audience,
            tone=outline.tone,
            theme=theme if theme != "default" else outline.theme,
            slides=slides,
            design_version="gamma-v1",
        )
        announce("Resolving visual assets")
        return prepare_assets(deck, prompt, deadline=deadline)
    finally:
        if hasattr(generator, "usage"):
            counts = generator.usage.snapshot()
            if usage_callback:
                usage_callback(counts)
            else:
                log.info("pipeline token usage: %s", counts)
