"""One content request, local source checks, and deterministic draft layouts."""

import json
import logging
import re
import time
from decimal import Decimal, InvalidOperation
from typing import Annotated

from pydantic import BaseModel, Field, create_model, model_validator

from app.config import Settings
from app.core.deck_schema import ClosingSlide, Deck, DraftSlide, TitleSlide
from app.core.errors import DraftContentError, StructuredOutputError
from app.llm.checkpoints import reject_checkpoint
from app.llm.factory import create_llm_client
from app.llm.structured import StructuredGenerator
from app.pipeline.analyze import MAX_PROMPT_CHARS
from app.pipeline.briefs import beauty_brand
from app.pipeline.grounding import (
    is_supported_paraphrase,
    unsupported_claim_categories,
    unsupported_specific_claim_categories,
)
from app.render.theme import theme_for_topic

log = logging.getLogger(__name__)


class DraftSection(BaseModel):
    title: str = Field(min_length=1, max_length=55)
    points: list[Annotated[str, Field(min_length=1, max_length=160)]] = Field(
        min_length=1, max_length=4
    )
    source_fact_ids: list[int] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def point_lengths(self):
        if any(
            not point.strip() or len(point) > 160 or len(point.split()) > 30
            for point in self.points
        ):
            raise ValueError("Each point must contain 1-160 characters and at most 30 words.")
        return self


class DraftContent(BaseModel):
    title: str = Field(min_length=1, max_length=60)
    subtitle: str = Field(min_length=1, max_length=100)
    facts: list[Annotated[str, Field(min_length=1, max_length=400)]] = Field(
        default_factory=list, max_length=12
    )
    sections: list[DraftSection] = Field(min_length=1, max_length=8)
    closing_title: str = Field(min_length=1, max_length=55)
    closing_text: str = Field(min_length=1, max_length=120)


DRAFT_PROMPT = """Write a complete, concise PowerPoint draft in one response.
Use the requested total slide count: cover, substantive content sections, closing.
Each content section needs 1-4 short points; each point <=160 characters and <=30 words.
Use topic-specific copy. If no company name is supplied, use a descriptive title.
Extract up to 12 facts as exact contiguous excerpts from the brief. Instructions are
not facts. For a company brief, all supplied product, launch, growth and financial
details must appear visibly in the title, points or closing, not merely in facts.
Assign facts to sections using zero-based source_fact_ids. For company briefs,
the application inserts those source excerpts as the factual copy. Spread facts
across the sections without repetition; unassigned sections may contain explicitly
proposed next steps. Keep facts concise, complete excerpts, not entire paragraphs.
Use facts=[] when the brief supplies only a general topic or presentation directions.
For general topics, explain basic concepts without invented statistics or citations.
Use only the brief as evidence for company capabilities. Do not invent features,
algorithms, assessments, benefits, availability, competitors, contacts, credentials,
numbers, dates, traction or funding. Do not add early access or exclusive privileges
to a supplied waitlist. Do not explain an unspecified production or matching mechanism.
Qualitative next steps can be explicitly proposed; do not present them as current facts.
Missing hard business data should be a concise instruction to add verified information.
Do not invent illustrative numbers or quarterly assignments. Preserve supplied lists
and percentages. Never include internal instructions, verification markers or design notes.
Closing: a relevant proposed next step without invented contact information.
No markdown. Return the entire JSON object, not an outline."""


def _normalized(text):
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def _numbers(text):
    numbers = set()
    for value in re.findall(r"(?<![\w])\d[\d,]*(?:\.\d+)?%?", text):
        try:
            numbers.add((Decimal(value.rstrip("%").replace(",", "")), value.endswith("%")))
        except InvalidOperation:
            continue
    return numbers


def requested_slide_count(prompt):
    match = re.search(r"\b(\d{1,2})[ -]slides?\b", prompt, re.I)
    if match:
        count = int(match.group(1))
        if not 3 <= count <= 10:
            raise ValueError("Drafts support 3-10 slides. Please request a count in that range.")
        return count
    return 6


def _source_points(fact):
    points, words = [], []
    for word in fact.split():
        if words and (len(" ".join([*words, word])) > 150 or len(words) >= 28):
            points.append(" ".join(words))
            words = []
        words.append(word)
    if words:
        points.append(" ".join(words))
    return points


def _ground_company_copy(content, prompt):
    """Company facts come from verified excerpts, not model-written promises."""
    company = bool(
        re.search(
            r"\b(?:brand|startup|company|offering|raising|our launch|use of funds)\b", prompt, re.I
        )
    )
    if not company:
        return
    if not content.facts:
        raise ValueError("Extract the supplied company facts as verbatim excerpts.")
    grounded = [[] for _ in content.sections]
    used = set()
    for index, section in enumerate(content.sections):
        ids = section.source_fact_ids or ([index] if index < len(content.facts) else [])
        for fact_id in ids:
            if not 0 <= fact_id < len(content.facts):
                raise ValueError("Source fact references must point to an extracted fact.")
            points = _source_points(content.facts[fact_id])
            if fact_id not in used and len(grounded[index]) + len(points) <= 4:
                grounded[index].extend(points)
                used.add(fact_id)
    for fact_id, fact in enumerate(content.facts):
        if fact_id in used:
            continue
        points = _source_points(fact)
        target = next((p for p in grounded if len(p) + len(points) <= 4), None)
        if target is None:
            raise ValueError("Request more slides to preserve all the supplied details.")
        target.extend(points)
    for section, points in zip(content.sections, grounded):
        if not points:
            points = [
                p
                for p in section.points
                if re.match(r"(?i)^proposed\b|^add verified\b", p)
                and not unsupported_claim_categories(p, prompt)
                and not unsupported_specific_claim_categories(p, prompt)
                and not (_numbers(p) - _numbers(prompt))
            ]
        section.points = points or [
            "Proposed next step: discuss the supplied details with your team.",
            "Add verified information to develop this part of the presentation.",
        ]
    for name in ("title", "subtitle", "closing_title", "closing_text"):
        value = getattr(content, name)
        if unsupported_claim_categories(
            value, prompt, allow_structural_inference=True, allow_problem_framing=True
        ) or unsupported_specific_claim_categories(value, prompt):
            setattr(
                content,
                name,
                {
                    "title": "Presentation draft",
                    "subtitle": "A draft for discussion",
                    "closing_title": "Next steps",
                    "closing_text": "Review the supplied details and agree on the next steps.",
                }[name],
            )


def planned_draft_model(prompt):
    count = requested_slide_count(prompt)

    def source_rules(self):
        source = _normalized(prompt)
        for fact in self.facts:
            if not _normalized(fact) or _normalized(fact) not in source:
                raise ValueError("Facts must be exact excerpts from the original brief.")
        _ground_company_copy(self, prompt)
        visible = " ".join(
            [
                self.title,
                self.subtitle,
                *(part for section in self.sections for part in (section.title, *section.points)),
                self.closing_title,
                self.closing_text,
            ]
        )
        for fact in self.facts:
            if _normalized(fact) not in _normalized(visible) and not is_supported_paraphrase(
                fact, visible
            ):
                raise ValueError(
                    "Every extracted source fact must also appear visibly in the slides."
                )
        if _numbers(visible) - _numbers(prompt):
            raise ValueError("Remove numbers, dates or percentages not supplied in the brief.")
        factual_prompt = re.sub(r"\b\d{1,2}[ -]slides?\b", "", prompt, flags=re.I)
        if _numbers(factual_prompt) - _numbers(visible):
            raise ValueError("Preserve the numbers and percentages supplied in the brief visibly.")
        for contact in re.findall(r"https?://\S+|[\w.+-]+@[\w.-]+\.\w+", visible):
            if contact not in prompt:
                raise ValueError("Remove contact information not supplied in the brief.")
        if unsupported_specific_claim_categories(visible, prompt):
            raise ValueError(
                "Remove unsupported pricing, traction, credentials, competitors or funding terms."
            )
        unsupported = unsupported_claim_categories(
            visible, prompt, allow_structural_inference=True, allow_problem_framing=True
        )
        # A neutral product-range heading is structure; detailed claims still count.
        if "unsupported company scope or operations" in unsupported:
            detail = " ".join(point for section in self.sections for point in section.points)
            if "unsupported company scope or operations" not in unsupported_claim_categories(
                detail, prompt
            ):
                unsupported.remove("unsupported company scope or operations")
        if unsupported:
            raise ValueError("Remove unsupported claims: " + ", ".join(unsupported))
        return self

    return create_model(
        "CompleteDraft",
        __base__=DraftContent,
        __validators__={"source_rules": model_validator(mode="after")(source_rules)},
        sections=(list[DraftSection], Field(..., min_length=count - 2, max_length=count - 2)),
    )


def generate_draft_deck(
    prompt,
    *,
    settings=None,
    generator=None,
    theme="default",
    deadline=None,
    progress_callback=None,
    usage_callback=None,
    **_kwargs,
):
    prompt = prompt.strip()
    if not prompt or len(prompt) > MAX_PROMPT_CHARS:
        raise ValueError("prompt must be nonempty and within the request limit")
    settings = settings or Settings.from_env()
    generator = generator or StructuredGenerator(
        create_llm_client(settings), max_attempts=min(2, settings.structured_attempts)
    )

    def announce(stage):
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError("deck generation exceeded its time limit")
        if progress_callback:
            progress_callback(stage)

    output_model = planned_draft_model(prompt)
    count = requested_slide_count(prompt)
    generator.deadline = deadline
    generator.on_provider_wait = lambda stage, seconds: announce(
        f"Drafting {count} slides: waiting for AI capacity (~{seconds}s)..."
        if seconds
        else f"Drafting {count} slides"
    )
    try:
        announce(f"Drafting {count} slides")
        content = generator.generate(
            output_model,
            stage="write",
            model=settings.model_for("write"),
            system_prompt=DRAFT_PROMPT,
            user_prompt=json.dumps({"brief": prompt, "total_slides": count}),
            max_tokens=3600,
            compact_corrections=True,
        )
        announce("Checking content against your brief")
        try:
            content = output_model.model_validate(content.model_dump())
        except ValueError:
            reject_checkpoint(content)
            raise
        title = beauty_brand(prompt) or content.title
        notes = "AI-generated draft. Review details before sharing. AI visual review has not run."
        slides = [
            TitleSlide(title=title, subtitle=content.subtitle, layout="title", speaker_notes=notes)
        ]
        slides += [
            DraftSlide(
                title=section.title,
                points=section.points,
                speaker_notes=notes,
            )
            for section in content.sections
        ]
        slides.append(
            ClosingSlide(
                title=content.closing_title,
                subtitle=content.closing_text,
                layout="closing",
                speaker_notes=notes,
            )
        )
        return Deck(
            title=title,
            theme=theme_for_topic(prompt) if theme == "default" else theme,
            slides=slides,
            design_version="draft-v1",
        )
    except StructuredOutputError as exc:
        if exc.detail == "response did not match the required schema":
            raise DraftContentError(exc.attempts) from exc
        raise
    finally:
        if hasattr(generator, "usage") and usage_callback:
            usage_callback(generator.usage.snapshot())
