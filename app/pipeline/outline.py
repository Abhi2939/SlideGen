"""Plan slide-by-slide structure from an analyzed deck brief."""

from app.pipeline.grounding import unsupported_claim_categories
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
or quotations. Do not add contact details, email addresses, phone numbers, or
URLs unless the user supplied them. Do not add traction, market-size, financial,
or fundraising slides unless the user explicitly requests an investor deck or
supplies those details. For a sparse product description, build a concise
product introduction around the supplied details rather than inventing a
company story or proof points. Use the extracted brand name as the deck title
and a supplied slogan as its subtitle when appropriate; do not repeat metadata
labels as presentation copy. Every key point must be supported by the original
request or an analyzed required fact. The analyzed brief and outline are
planning aids, not evidence. Do not infer product mechanisms, technology,
manufacturing methods, fulfillment or shipping, customer behavior, market
facts, or outcomes from a product description. For example, a custom-blended
product description does not establish that blending happens in-house, that
AI or an algorithm is used, or that products are shipped. Do not add
aspirational outcomes such as confidence, savings, or performance unless the
user supplied them. For sparse product descriptions, make a complete deck by
organizing and clearly restating the supplied facts; do not fill space with
industry assumptions. Give each slide a distinct purpose and avoid assigning
the same required fact to multiple slides unless necessary for clarity. Use
closing for the final slide when suitable. Return JSON only."""


def _ground_outline(prompt: str, analysis: AnalysisOutput, outline: OutlineOutput) -> OutlineOutput:
    """Remove common unsupported claim categories before they reach slide writing."""
    facts_by_id = {fact.id: fact for fact in analysis.required_facts}
    grounded_slides = []

    for plan in outline.slides:
        unsupported = unsupported_claim_categories(
            "\n".join([plan.title, plan.purpose, *plan.key_points]), prompt
        )
        safe_points = [
            point
            for point in plan.key_points
            if not unsupported_claim_categories(point, prompt)
        ]
        changes = bool(unsupported) or len(safe_points) != len(plan.key_points)
        title = plan.title
        purpose = plan.purpose
        layout = plan.layout

        if changes and layout == "process":
            layout = "bullets"
            title = "Product Details"
            purpose = "Present the supplied product details without implying a process."
        elif unsupported:
            if unsupported_claim_categories(title, prompt):
                title = "Supplied Details"
            if unsupported_claim_categories(purpose, prompt):
                purpose = "Present only the details supplied in the original request."

        if changes and not safe_points:
            assigned_facts = [
                facts_by_id[fact_id].text
                for fact_id in plan.required_fact_ids
                if fact_id in facts_by_id
                and not unsupported_claim_categories(facts_by_id[fact_id].text, prompt)
            ]
            safe_points = assigned_facts or [prompt.strip()]

        grounded_slides.append(
            plan.model_copy(
                update={
                    "title": title,
                    "purpose": purpose,
                    "layout": layout,
                    "key_points": safe_points,
                }
            )
        )

    return outline.model_copy(update={"slides": grounded_slides})


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
            return _ground_outline(clean_prompt, analysis, outline)
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
