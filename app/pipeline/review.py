"""Review generated deck content and repair only flagged slides."""

from dataclasses import dataclass
import json

from app.core.deck_schema import Slide
from app.llm.structured import StructuredGenerator
from app.pipeline.models import (
    AnalysisOutput,
    OutlineOutput,
    ReviewOutput,
    validate_outline_facts,
)
from app.pipeline.write_slides import rewrite_slides


REVIEW_SYSTEM_PROMPT = """You are the quality-review stage of a presentation generator.
Review the complete deck against the user's request and analyzed brief. Check
for missing required facts, duplicated points, unsupported claims or statistics,
text likely to overflow, and coherence across slides. Do not flag a claim as
unsupported when the user supplied it. For every issue, identify the affected
zero-based slide indices and specify a concise repair instruction. Set
regenerate_slide_indices to the exact slides that need rewriting. Set passed
to true only when the deck has no material issues. Do not invent facts while
reviewing. Return JSON only."""


@dataclass(frozen=True)
class ReviewRun:
    slides: list[Slide]
    review: ReviewOutput
    repair_rounds: int


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

    issue_indices = {
        index for issue in review.issues for index in issue.slide_indices
    }
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
        try:
            _validate_review_references(review, analysis, outline)
            return review
        except ValueError as exc:
            last_error = exc
            correction = (
                "\n\nCORRECTION REQUIRED: The prior review failed reference validation: "
                f"{exc}. Return corrected review JSON with valid fact and slide references."
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
        if review.passed or repair_rounds >= max_repair_rounds:
            return ReviewRun(current_slides, review, repair_rounds)

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
