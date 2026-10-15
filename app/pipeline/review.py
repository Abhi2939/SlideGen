"""Review generated deck content and repair only flagged slides."""

from dataclasses import dataclass
import json
import re
from collections.abc import Callable

from app.core.deck_schema import Bullet, BulletsSlide, Slide, TwoColumnSlide
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
    rewrite_slides,
)


REVIEW_SYSTEM_PROMPT = """You are the quality-review stage of a presentation generator.
Review the complete deck primarily against the original user request and its
required facts. Treat the outline as planning guidance that can contain errors;
it is not an independent source of facts or requirements. Never ask a writer to
add an outline point unless it is supported by the original request or required
facts. Ignore unsupported aspirational outline points such as confidence or
time-saving claims instead of requiring them in the slides.
When the brief has no specific company facts, explicit placeholder instructions
such as "Add a verified detail" or "Replace this placeholder" are scaffolding,
not factual claims; do not ask the writer to replace them with details the user
did not provide. Chart or statistic values with illustrative provenance and a
caption that identifies them as examples are not claims about the company.
Check for missing required facts, duplicated points, unsupported factual claims
or statistics, text likely to overflow, and coherence across slides. Treat the
user's supplied product details, slogans, and claims, along with straightforward
paraphrases of them, as supported. Flag only material additions that assert
new product offerings, product categories, company capabilities, facilities,
manufacturing or production capacity, locations, geographic reach, customer
segments, company history, market position, strategy, initiatives, roadmaps,
mission, vision, business goals, strengths, R&D, supply-chain operations,
customer support, partnership availability, or contact invitations, along with
guarantees, measurable outcomes, market facts, or evidence not present in the
request. A broad business type does not establish any particular product
portfolio, operation, scale, or service area. Treat any email address, phone
number, or URL absent
from the request as unsupported. Do not require contact details, financials,
or fundraising content unless the user requested them. Do not mistake an absent
optional detail for a missing required fact. For every real
issue, identify the affected zero-based slide indices and specify a concise
repair instruction. When passed is false, include every affected slide in
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

    if review.passed:
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

    deck_content = [slide.model_dump(mode="json") for slide in slides]
    request = (
        f"ORIGINAL USER REQUEST:\n{clean_prompt}\n\n"
        f"ANALYZED BRIEF:\n{analysis.model_dump_json(indent=2)}\n\n"
        f"SLIDE OUTLINE:\n{outline.model_dump_json(indent=2)}\n\n"
        "GENERATED DECK (slide indices are zero-based):\n"
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
        )
        review = _filter_grounded_unsupported_claims(
            review, clean_prompt, slides, analysis, outline
        )
        try:
            _validate_review_references(review, analysis, outline)
            return review
        except ValueError as exc:
            last_error = exc
            correction = (
                "\n\nCORRECTION REQUIRED: The prior review failed reference validation: "
                f"{exc}. Return corrected review JSON with valid fact and slide references. "
                "When passed is false, regenerate_slide_indices must include every slide "
                "listed in issues and every outline slide assigned a missing_fact_id."
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
