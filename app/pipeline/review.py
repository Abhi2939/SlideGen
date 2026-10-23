"""Review generated deck content and repair only flagged slides."""

from dataclasses import dataclass
import json
import re
from collections.abc import Callable

from app.core.deck_schema import AskAllocationSlide, Bullet, BulletsSlide, Slide, TwoColumnSlide
from app.llm.structured import StructuredGenerator
from app.pipeline.models import (
    AnalysisOutput,
    OutlineOutput,
    ReviewIssue,
    ReviewOutput,
    validate_outline_facts,
)
from app.pipeline.grounding import is_supported_paraphrase
from app.pipeline.write_slides import (
    _can_use_sparse_company_placeholder,
    _is_generic_business_fact,
    _placeholder_slide,
    _slide_text_content,
    rewrite_slides,
)


REVIEW_SYSTEM_PROMPT = """You are the quality-review stage of a presentation generator.
Review the complete deck primarily against the original user request and its
required facts. Treat the outline as planning guidance that can contain errors;
it is not an independent source of facts or requirements. Never ask a writer to
add an outline point unless it is supported by the original request or required
facts. Ignore unsupported aspirational outline points such as confidence or
time-saving claims instead of requiring them in the slides.
Apply three grounding tiers. Preserve Tier 1 user facts. Allow Tier 2 qualitative
structure such as generic problem/solution framing, conceptual workflow steps,
value-proposition headings, audience framing, competitor categories, and
timeless general-knowledge explanations for non-company topics. When
not user-supplied, these must be presented as suggestions or categories, not as
confirmed company capabilities. Never invent Tier 3 details: specific names,
market facts, statistics, percentages, prices, traction, team credentials,
funding amounts, and named competitors must come from the request or be clearly
marked as placeholders/illustrative where appropriate. Do not flag clearly
conceptual Tier 2 framing as an unsupported fact.
When the brief has no specific company facts, explicit placeholder instructions
such as "Add a verified detail" or "Replace this placeholder" are scaffolding,
not factual claims; do not ask the writer to replace them with details the user
did not provide. Chart or statistic values with illustrative provenance and a
caption that identifies them as examples are not claims about the company.
For investor decks, explicit placeholders for unsupported traction, team,
pricing, product features, workflows, and fundraising details are scaffolding;
do not treat them as unsupported claims or require invented replacements.
Check for missing required facts, duplicated points, Tier 3 grounding violations,
and supplied launch or growth plans hidden only in speaker notes. Required
facts must appear visibly on the slides; notes do not satisfy coverage. Keep
future plans distinct from achieved traction or proven results. Check for
text likely to overflow, and coherence across slides. Treat the
user's supplied product details, slogans, and claims, along with straightforward
paraphrases of them, as supported. Do not treat a broad business type as
evidence of a specific product portfolio, operation, scale, or service area.
Treat any email address, phone
number, or URL absent
from the request as unsupported. Do not require contact details, financials,
or fundraising content unless the user requested them. Do not mistake an absent
optional detail for a missing required fact. For every real
issue, identify the affected zero-based slide indices and specify a concise
repair instruction. Use only the valid slide indices and required fact IDs
listed in the review reference contract. Each generated slide has an explicit
slide_index; copy that value, not its position counted from one. Review only
the supplied slides. If content is missing, attach the finding to affected
existing slides; never reference an imagined or proposed additional slide.
When passed is false, include every affected slide in
regenerate_slide_indices. For each missing_fact_id, include every outline slide
assigned that fact. Set passed to true only when the deck has no material
issues. Do not invent facts while reviewing. Return JSON only."""


@dataclass(frozen=True)
class ReviewRun:
    slides: list[Slide]
    review: ReviewOutput
    repair_rounds: int


def _remove_reported_duplicate_bullet(slide: Slide, detail: str) -> Slide:
    """Remove repeated exact bullet text when a review finding identifies it."""
    match = re.search(
        r"duplicate bullet(?: point)?\s+['\"]([^'\"]+)['\"]",
        detail,
        re.IGNORECASE,
    )
    if not match:
        return slide
    duplicate_text = " ".join(re.findall(r"[a-z0-9]+", match.group(1).casefold()))

    seen_reported_text = False

    def keep_first(bullets: list[Bullet]) -> list[Bullet]:
        nonlocal seen_reported_text
        kept: list[Bullet] = []
        for bullet in bullets:
            normalized = " ".join(re.findall(r"[a-z0-9]+", bullet.text.casefold()))
            if normalized == duplicate_text:
                if seen_reported_text:
                    continue
                seen_reported_text = True
            kept.append(bullet)
        return kept

    if isinstance(slide, BulletsSlide):
        bullets = keep_first(slide.bullets)
        if len(bullets) >= 2 and len(bullets) < len(slide.bullets):
            return slide.model_copy(update={"bullets": bullets})
    elif isinstance(slide, TwoColumnSlide):
        left_bullets = keep_first(slide.left.bullets)
        right_bullets = keep_first(slide.right.bullets)
        if not left_bullets or not right_bullets:
            # Keep the reported point in the column that contains no other
            # bullets, and remove its repeated copy from the other column.
            seen_reported_text = False
            right_bullets = keep_first(slide.right.bullets)
            left_bullets = keep_first(slide.left.bullets)
        if (
            len(left_bullets) >= 1
            and len(right_bullets) >= 1
            and (
                len(left_bullets) < len(slide.left.bullets)
                or len(right_bullets) < len(slide.right.bullets)
            )
        ):
            left = slide.left.model_copy(update={"bullets": left_bullets})
            right = slide.right.model_copy(update={"bullets": right_bullets})
            return slide.model_copy(update={"left": left, "right": right})
    return slide


def _repair_reported_duplicate_bullets(
    slides: list[Slide], review: ReviewOutput
) -> list[Slide] | None:
    """Apply safe exact-duplicate repairs and return None when none apply."""
    repaired = list(slides)
    changed = False
    for issue in review.issues:
        if issue.category != "duplicate":
            continue
        for index in issue.slide_indices:
            replacement = _remove_reported_duplicate_bullet(repaired[index], issue.detail)
            if replacement is not repaired[index]:
                repaired[index] = replacement
                changed = True
    return repaired if changed else None


def _validate_review_references(
    review: ReviewOutput,
    analysis: AnalysisOutput,
    outline: OutlineOutput,
    *,
    require_repair_targets: bool = True,
) -> None:
    slide_count = len(outline.slides)
    known_facts = {fact.id for fact in analysis.required_facts}
    unknown_facts = set(review.missing_fact_ids) - known_facts
    if unknown_facts:
        raise ValueError(f"review references unknown fact IDs: {sorted(unknown_facts)}")

    issue_indices = {index for issue in review.issues for index in issue.slide_indices}
    all_indices = issue_indices | set(review.regenerate_slide_indices)
    invalid_indices = sorted(index for index in all_indices if index < 0 or index >= slide_count)
    if invalid_indices:
        raise ValueError(f"review references out-of-range slide indices: {invalid_indices}")

    if review.passed or not require_repair_targets:
        return
    if not review.regenerate_slide_indices:
        raise ValueError("failed review must identify at least one slide to regenerate")

    assigned_slides = {
        slide.index
        for slide in outline.slides
        if set(slide.required_fact_ids) & set(review.missing_fact_ids)
    }
    required_repairs = issue_indices | assigned_slides
    omitted_repairs = required_repairs - set(review.regenerate_slide_indices)
    if omitted_repairs:
        raise ValueError(
            f"review did not request regeneration for affected slides: {sorted(omitted_repairs)}"
        )


def _complete_regenerate_targets(
    review: ReviewOutput,
    outline: OutlineOutput,
) -> ReviewOutput:
    """Add any slides implied by review findings to the repair list."""
    if review.passed:
        return review
    required = {index for issue in review.issues for index in issue.slide_indices}
    missing_ids = set(review.missing_fact_ids)
    required.update(
        slide.index for slide in outline.slides if missing_ids & set(slide.required_fact_ids)
    )
    targets = sorted(required | set(review.regenerate_slide_indices))
    if targets == review.regenerate_slide_indices:
        return review
    return review.model_copy(update={"regenerate_slide_indices": targets})


def _filter_missing_facts_present_in_deck(
    review: ReviewOutput,
    analysis: AnalysisOutput,
    slides: list[Slide],
) -> ReviewOutput:
    """Ignore missing-fact reports for facts already visible elsewhere in the deck."""
    if not review.missing_fact_ids:
        return review
    visible_text = _normalized_review_text(
        "\n".join(_slide_text_content(slide) for slide in slides)
    )
    facts_by_id = {fact.id: fact for fact in analysis.required_facts}
    present_ids = {
        fact_id
        for fact_id in review.missing_fact_ids
        if fact_id in facts_by_id
        if _normalized_review_text(facts_by_id[fact_id].text) in visible_text
    }
    remaining_ids = [fact_id for fact_id in review.missing_fact_ids if fact_id not in present_ids]
    remaining_issues = []
    for issue in review.issues:
        if issue.category == "missing_fact":
            issue_text = _normalized_review_text(issue.detail)
            reported_present = [
                fact_id
                for fact_id in present_ids
                if _normalized_review_text(facts_by_id[fact_id].text) in issue_text
            ]
            if reported_present:
                continue
        remaining_issues.append(issue)
    if not remaining_ids and not remaining_issues:
        return ReviewOutput(passed=True)
    if remaining_ids == review.missing_fact_ids and remaining_issues == review.issues:
        return review
    return review.model_copy(update={"missing_fact_ids": remaining_ids, "issues": remaining_issues})


def _normalized_review_text(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.casefold()))


def _filter_investor_placeholder_findings(
    review: ReviewOutput,
    prompt: str,
    analysis: AnalysisOutput,
    outline: OutlineOutput,
    slides: list[Slide],
) -> ReviewOutput:
    """Accept explicit investor placeholders when the missing source data was not supplied."""
    if not re.search(r"\b(?:investor|fundraising|seed round|pitch deck)\b", prompt, re.I):
        return review

    facts_by_id = {fact.id: fact for fact in analysis.required_facts}
    currency_amount = re.compile(
        r"(?:Rs\.?|INR|₹|USD|US\$|\$|EUR|€|GBP|£)\s*\d|\b\d[\d,.]*\s*(?:cr|crore|lakh|million|billion)\b",
        re.IGNORECASE,
    )
    missing_ask = not currency_amount.search(prompt) and not any(
        currency_amount.search(fact.text) for fact in analysis.required_facts
    )
    kept = []
    for issue in review.issues:
        valid_issue_indices = bool(issue.slide_indices) and all(
            0 <= index < len(slides) for index in issue.slide_indices
        )
        filter_placeholder_claim = (
            issue.category == "unsupported_claim"
            and valid_issue_indices
            and all(
                any(
                    marker in _slide_text_content(slides[index]).casefold()
                    for marker in ("add a verified", "replace this placeholder", "[add ", "tbd")
                )
                and all(
                    _normalized_review_text(facts_by_id[fact_id].text)
                    in _normalized_review_text(_slide_text_content(slides[index]))
                    for fact_id in outline.slides[index].required_fact_ids
                )
                for index in issue.slide_indices
            )
        )
        filter_missing_ask = (
            issue.category == "other"
            and missing_ask
            and valid_issue_indices
            and any(
                isinstance(slides[index], AskAllocationSlide) and slides[index].ask is None
                for index in issue.slide_indices
            )
        )
        if not (filter_placeholder_claim or filter_missing_ask):
            kept.append(issue)

    if len(kept) == len(review.issues):
        return review
    if not kept and not review.missing_fact_ids:
        return ReviewOutput(passed=True)
    return review.model_copy(update={"issues": kept})


def _repair_instructions(
    review: ReviewOutput,
    analysis: AnalysisOutput,
    outline: OutlineOutput,
) -> dict[int, str]:
    instructions: dict[int, list[str]] = {}
    for issue in review.issues:
        for index in issue.slide_indices:
            parts = [f"{issue.severity} {issue.category}: {issue.detail}"]
            if issue.repair_instruction:
                parts.append(issue.repair_instruction)
            instructions.setdefault(index, []).extend(parts)

    missing_ids = set(review.missing_fact_ids)
    facts_by_id = {fact.id: fact for fact in analysis.required_facts}
    if missing_ids:
        for slide in outline.slides:
            assigned = missing_ids & set(slide.required_fact_ids)
            if assigned:
                slide_missing_text = "; ".join(
                    facts_by_id[fact_id].text for fact_id in sorted(assigned)
                )
                instructions.setdefault(slide.index, []).append(
                    "Include the missing required fact(s) exactly as supplied: "
                    f"{slide_missing_text}"
                )
    return {index: "\n".join(parts) for index, parts in instructions.items()}


def _filter_grounded_unsupported_claims(
    review: ReviewOutput,
    prompt: str,
    slides: list[Slide],
    analysis: AnalysisOutput,
    outline: OutlineOutput,
) -> ReviewOutput:
    """Ignore only quoted unsupported-claim findings proven present and grounded."""
    dismissed = False
    remaining: list[ReviewIssue] = []
    for issue in review.issues:
        quoted_claims = re.findall(r"['\"“”]([^'\"“”]{8,})['\"“”]", issue.detail)
        grounded = issue.category == "unsupported_claim" and bool(quoted_claims)
        if grounded:
            for claim in quoted_claims:
                if not is_supported_paraphrase(claim, prompt):
                    grounded = False
                    break
                for index in issue.slide_indices:
                    if index < 0 or index >= len(slides):
                        grounded = False
                        break
                    slide_text = json.dumps(
                        slides[index].model_dump(mode="json"), ensure_ascii=False
                    )
                    if claim.casefold() not in slide_text.casefold():
                        grounded = False
                        break
                if not grounded:
                    break
        if grounded:
            dismissed = True
        else:
            remaining.append(issue)

    if not dismissed:
        return review
    if not remaining and not review.missing_fact_ids:
        return ReviewOutput(passed=True)

    affected = {index for issue in remaining for index in issue.slide_indices}
    missing_ids = set(review.missing_fact_ids)
    affected.update(
        slide.index for slide in outline.slides if missing_ids & set(slide.required_fact_ids)
    )
    regenerate_indices = [index for index in review.regenerate_slide_indices if index in affected]
    return ReviewOutput(
        passed=False,
        issues=remaining,
        missing_fact_ids=review.missing_fact_ids,
        regenerate_slide_indices=regenerate_indices,
    )


def review_deck(
    prompt: str,
    analysis: AnalysisOutput,
    outline: OutlineOutput,
    slides: list[Slide],
    generator: StructuredGenerator,
    model: str,
    *,
    max_tokens: int | None = 3000,
    semantic_attempts: int = 2,
) -> ReviewOutput:
    """Review one deck snapshot and validate fact/slide references."""
    clean_prompt = prompt.strip()
    if not clean_prompt:
        raise ValueError("prompt must not be empty")
    if len(slides) != len(outline.slides):
        raise ValueError("slide count does not match the outline")
    if semantic_attempts < 1:
        raise ValueError("semantic_attempts must be at least 1")
    if outline.deck_title != analysis.deck_title:
        raise ValueError("outline deck title does not match the analyzed deck title")
    validate_outline_facts(analysis, outline)

    review_plans = []
    facts_by_id = {fact.id: fact for fact in analysis.required_facts}
    for plan, slide in zip(outline.slides, slides):
        facts = [facts_by_id[i] for i in plan.required_fact_ids]
        try:
            placeholder = _placeholder_slide(plan, facts)
        except ValueError:
            review_plans.append(plan)
            continue
        if slide.model_dump(exclude={"speaker_notes"}) == placeholder.model_dump(
            exclude={"speaker_notes"}
        ):
            # The writer deliberately substituted a deterministic safe scaffold.
            # Align review's planning aid with that actual scaffold. Required fact
            # IDs and raw reference validation still apply; no findings are dropped.
            plan = plan.model_copy(
                update={
                    "title": slide.title,
                    "key_points": [fact.text for fact in facts],
                    "purpose": "Explicit placeholders for source details not supplied; do not invent replacements.",
                }
            )
        review_plans.append(plan)
    review_outline = outline.model_copy(update={"slides": review_plans})

    deck_content = [
        {
            "slide_index": index,
            "content": slide.model_dump(mode="json", exclude={"speaker_notes"}),
        }
        for index, slide in enumerate(slides)
    ]
    for item, slide in zip(deck_content, slides):
        if getattr(slide, "placeholder", False):
            item["content"].pop("series", None)
    reference_contract = (
        f"This deck contains exactly {len(slides)} slides. "
        f"Valid slide indices: {list(range(len(slides)))}. "
        f"Valid required fact IDs: {sorted(fact.id for fact in analysis.required_facts)}. "
        "issues[].slide_indices and regenerate_slide_indices must use valid slide indices. "
        "missing_fact_ids must use valid required fact IDs. An empty fact-ID list means no fact IDs "
        "may be reported missing. Do not infer additional slides or renumber slides."
    )
    # First attempts use prompt constraints; bounded recovery may also send
    # the native schema to Groq. Semantic validation remains authoritative.
    response_schema = ReviewOutput.model_json_schema()
    slide_ids = {"type": "integer", "enum": list(range(len(slides)))}
    response_schema["properties"]["regenerate_slide_indices"]["items"] = slide_ids
    response_schema["$defs"]["ReviewIssue"]["properties"]["slide_indices"]["items"] = slide_ids
    missing_ids = response_schema["properties"]["missing_fact_ids"]
    if analysis.required_facts:
        missing_ids["items"] = {
            "type": "integer",
            "enum": sorted(fact.id for fact in analysis.required_facts),
        }
    else:
        missing_ids["maxItems"] = 0
    request = (
        f"REVIEW REFERENCE CONTRACT:\n{reference_contract}\n\n"
        f"ORIGINAL USER REQUEST:\n{clean_prompt}\n\n"
        f"ANALYZED BRIEF:\n{analysis.model_dump_json(indent=2)}\n\n"
        f"SLIDE OUTLINE:\n{review_outline.model_dump_json(indent=2)}\n\n"
        "GENERATED DECK (each slide has an explicit zero-based slide_index):\n"
        f"{json.dumps(deck_content, ensure_ascii=False, indent=2)}"
    )
    correction = ""
    last_error: ValueError | None = None
    for _ in range(semantic_attempts):
        review = generator.generate(
            ReviewOutput,
            stage="review",
            model=model,
            system_prompt=REVIEW_SYSTEM_PROMPT,
            user_prompt=request + correction,
            temperature=0.1,
            max_tokens=max_tokens,
            response_schema=response_schema,
        )
        try:
            # Reject malformed references before any finding can be dismissed.
            # Missing repair targets can still be completed from valid findings.
            _validate_review_references(review, analysis, outline, require_repair_targets=False)
            validated_review = _filter_grounded_unsupported_claims(
                review, clean_prompt, slides, analysis, outline
            )
            validated_review = _filter_missing_facts_present_in_deck(
                validated_review, analysis, slides
            )
            validated_review = _filter_investor_placeholder_findings(
                validated_review, clean_prompt, analysis, outline, slides
            )
            validated_review = _complete_regenerate_targets(validated_review, outline)
            _validate_review_references(validated_review, analysis, outline)
            return validated_review
        except ValueError as exc:
            last_error = exc
            correction = (
                "\n\nCORRECTION REQUIRED: The prior review failed reference validation: "
                f"{exc}.\n{reference_contract}\n"
                "Recheck the supplied slides and return the complete corrected review JSON. "
                "Keep real findings and attach them to the actual affected slide_index. "
                "When passed is false, regenerate_slide_indices must include every slide "
                "listed in issues and every outline slide assigned a missing_fact_id.\n"
                f"REJECTED REVIEW JSON:\n{review.model_dump_json()}"
            )

    assert last_error is not None
    raise ValueError(
        f"review failed semantic validation after {semantic_attempts} attempt(s): {last_error}"
    ) from last_error


def review_and_repair_deck(
    prompt: str,
    analysis: AnalysisOutput,
    outline: OutlineOutput,
    slides: list[Slide],
    generator: StructuredGenerator,
    review_model: str,
    writing_model: str,
    *,
    max_repair_rounds: int = 2,
    max_workers: int = 4,
    max_tokens: int | None = 3000,
    semantic_attempts: int = 2,
    progress_callback: Callable[[str], None] | None = None,
    review_callback: Callable[[dict], None] | None = None,
) -> ReviewRun:
    """Review, rewrite flagged slides only, and re-review up to the round limit."""
    if max_repair_rounds < 0:
        raise ValueError("max_repair_rounds cannot be negative")
    if max_workers < 1:
        raise ValueError("max_workers must be at least 1")
    if semantic_attempts < 1:
        raise ValueError("semantic_attempts must be at least 1")
    current_slides = list(slides)
    repair_rounds = 0
    unsupported_placeholder_repair_attempted = False

    while True:
        if progress_callback is not None:
            progress_callback("Reviewing generated slides")
        review = review_deck(
            prompt,
            analysis,
            outline,
            current_slides,
            generator,
            review_model,
            max_tokens=max_tokens,
            semantic_attempts=semantic_attempts,
        )
        if review_callback is not None:
            review_callback(review.model_dump(mode="json"))
        if review.passed:
            return ReviewRun(current_slides, review, repair_rounds)

        duplicate_repairs = _repair_reported_duplicate_bullets(current_slides, review)
        if duplicate_repairs is not None and repair_rounds < max_repair_rounds:
            if progress_callback is not None:
                progress_callback("Removing repeated bullet text")
            current_slides = duplicate_repairs
            repair_rounds += 1
            continue

        if (
            repair_rounds < max_repair_rounds
            and not review.missing_fact_ids
            and _can_use_sparse_company_placeholder(prompt, analysis)
        ):
            fact_by_id = {fact.id: fact for fact in analysis.required_facts}
            placeholder_indices = {
                index
                for issue in review.issues
                if issue.category in {"unsupported_claim", "duplicate"}
                for index in issue.slide_indices
                if all(
                    _is_generic_business_fact(fact_by_id[fact_id])
                    for fact_id in outline.slides[index].required_fact_ids
                )
            }
            if placeholder_indices:
                if progress_callback is not None:
                    progress_callback("Replacing unsupported details with placeholders")
                current_slides = list(current_slides)
                for index in placeholder_indices:
                    assigned_facts = [
                        fact_by_id[fact_id] for fact_id in outline.slides[index].required_fact_ids
                    ]
                    current_slides[index] = _placeholder_slide(
                        outline.slides[index], assigned_facts
                    )
                repair_rounds += 1
                continue

        if repair_rounds >= max_repair_rounds:
            if not unsupported_placeholder_repair_attempted and not review.missing_fact_ids:
                unsupported_indices = {
                    index
                    for issue in review.issues
                    if issue.category == "unsupported_claim" and issue.severity == "high"
                    for index in issue.slide_indices
                }
                if unsupported_indices:
                    if progress_callback is not None:
                        progress_callback("Replacing unsupported claims with placeholders")
                    facts_by_id = {fact.id: fact for fact in analysis.required_facts}
                    for index in unsupported_indices:
                        assigned_facts = [
                            facts_by_id[fact_id]
                            for fact_id in outline.slides[index].required_fact_ids
                        ]
                        current_slides[index] = _placeholder_slide(
                            outline.slides[index], assigned_facts
                        )
                    unsupported_placeholder_repair_attempted = True
                    continue
            return ReviewRun(current_slides, review, repair_rounds)

        if progress_callback is not None:
            progress_callback("Repairing flagged slides")
        current_slides = rewrite_slides(
            prompt,
            analysis,
            outline,
            current_slides,
            review.regenerate_slide_indices,
            generator,
            writing_model,
            max_workers=max_workers,
            semantic_attempts=semantic_attempts,
            repair_instructions=_repair_instructions(review, analysis, outline),
        )
        repair_rounds += 1
