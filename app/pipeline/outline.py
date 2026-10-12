"""Plan slide-by-slide structure from an analyzed deck brief."""

from app.llm.structured import StructuredGenerator
from app.pipeline.models import AnalysisOutput, OutlineOutput, validate_outline_facts


OUTLINE_SYSTEM_PROMPT = """You are the outline stage of a presentation generator.
Create a clear sequence of slides for the supplied deck brief. Return exactly
the requested number of slides, indexed from 0. Slide 0 must use the title
layout and its title must equal deck_title. Use only supported layouts:
title, section, bullets, two_column, stat, process, comparison, chart, quote,
closing. Assign every required fact ID to one or more relevant slides; do not
omit or invent IDs. Use key_points to guide later slide writing. Choose layouts
that fit each slide's purpose, avoid repeating points, and do not invent data
or quotations. Use closing for the final slide when suitable. Return JSON only."""


def create_outline(
    prompt: str,
    analysis: AnalysisOutput,
    generator: StructuredGenerator,
    model: str,
    *,
    max_tokens: int | None = 3000,
    semantic_attempts: int = 2,
) -> OutlineOutput:
    """Generate an outline and retry semantic contract failures at this stage."""
    clean_prompt = prompt.strip()
    if not clean_prompt:
        raise ValueError("prompt must not be empty")
    if semantic_attempts < 1:
        raise ValueError("semantic_attempts must be at least 1")

    base_request = (
        "Create an outline using this original request and analyzed brief. "
        "The outline deck_title and title-slide title must match the brief's deck_title.\n\n"
        f"USER REQUEST:\n{clean_prompt}\n\n"
        "ANALYZED BRIEF:\n"
        f"{analysis.model_dump_json(indent=2)}"
    )
    correction = ""
    last_error: ValueError | None = None
    for attempt in range(semantic_attempts):
        outline = generator.generate(
            OutlineOutput,
            stage="outline",
            model=model,
            system_prompt=OUTLINE_SYSTEM_PROMPT,
            user_prompt=base_request + correction,
            temperature=0.2,
            max_tokens=max_tokens,
        )
        try:
            if outline.deck_title != analysis.deck_title:
                raise ValueError("outline deck title does not match the analyzed deck title")
            validate_outline_facts(analysis, outline)
            return outline
        except ValueError as exc:
            last_error = exc
            correction = (
                "\n\nCORRECTION REQUIRED: The prior outline failed a contract check: "
                f"{exc}. Regenerate the complete outline satisfying every contract."
            )

    assert last_error is not None
    raise ValueError(
        f"outline failed semantic validation after {semantic_attempts} attempt(s): {last_error}"
    ) from last_error
