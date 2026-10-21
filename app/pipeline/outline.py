"""Plan slide-by-slide structure from an analyzed deck brief."""

import re

from app.pipeline.grounding import unsupported_claim_categories
from app.pipeline.briefs import short_beauty_brief
from app.llm.structured import StructuredGenerator
from app.pipeline.models import AnalysisOutput, OutlineOutput, validate_outline_facts


OUTLINE_SYSTEM_PROMPT = """You are the outline stage of a presentation generator.
Create a clear sequence of slides for the supplied deck brief. Return exactly
the requested number of slides, indexed from 0. Slide 0 must use the title
layout and its title must equal deck_title. Use only supported layouts:
title, section, bullets, two_column, stat, process, comparison, chart, quote,
closing, ask_allocation, market_snapshot, flow_steps, quadrants. Route a funding
ask or budget split to ask_allocation; market size, growth, segments, and drivers
to market_snapshot; a conceptual user journey or ordered workflow to flow_steps;
and a competitor or ecosystem landscape to quadrants with exactly four groups.
Choose layouts by content, not slide order. Assign every required fact ID to one or more relevant slides; do not
omit or invent IDs. Use key_points to guide later slide writing. Choose layouts
that fit each slide's purpose, avoid repeating points, and do not invent data
or quotations. When the request asks for charts or figures, include at least
two visual slides using chart, stat, process, flow_steps, or market_snapshot
layouts. Explicit requests for process, stats, comparisons, quotations, market
snapshots, or competitive landscapes must be reflected in the outline.
Use section only for a brief divider with a visible subtitle. Do not add contact details, email addresses, phone numbers, or
URLs unless the user supplied them. Do not add traction, market-size, financial,
or fundraising slides unless the user explicitly requests an investor deck or
supplies those details. For a sparse product description, build a complete,
concise introduction using qualitative structure and clearly marked conceptual
framing, without inventing company proof points. For broad non-company topics,
use useful general knowledge and qualitative explanations; do not invent
precise numbers, named sources, or current claims. Use the extracted brand name as the deck title
and a supplied slogan as its subtitle when appropriate; do not repeat metadata
labels as presentation copy. Every specific factual key point must be supported
by the original request or an analyzed required fact. Qualitative structure,
generic workflow steps, value-proposition headings, audience framing, and
competitor categories may be inferred; mark inferred workflows as conceptual.
For broad non-company topics, timeless general-knowledge explanations are
acceptable as qualitative content, without fabricated statistics or sources.
The analyzed brief and outline are planning aids, not evidence. Do not infer technology,
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
        allow_structure = plan.layout == "flow_steps"
        unsupported = unsupported_claim_categories(
            "\n".join([plan.title, plan.purpose, *plan.key_points]),
            prompt,
            allow_structural_inference=allow_structure,
            allow_problem_framing=True,
        )
        safe_points = [
            point
            for point in plan.key_points
            if not unsupported_claim_categories(
                point,
                prompt,
                allow_structural_inference=allow_structure,
                allow_problem_framing=True,
            )
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
            safe_points = assigned_facts

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


def _validate_requested_layouts(prompt: str, outline: OutlineOutput) -> None:
    """Ensure explicit visual/layout requests make it into the outline."""
    requested = prompt.casefold()
    layouts = [slide.layout for slide in outline.slides]
    if re.search(r"\bcharts?\b", requested) and "chart" not in layouts:
        raise ValueError("the request explicitly asks for a chart slide")
    if re.search(r"\b(?:charts?|figures?|graphs?|visualizations?)\b", requested):
        visual_count = sum(
            layout in {"chart", "stat", "process", "flow_steps", "market_snapshot"}
            for layout in layouts
        )
        if visual_count < 2:
            raise ValueError("a request for charts or figures needs at least two visual slides")
    for keyword, layout in (
        (r"\bprocess\b", "process"),
        (r"\bstats?\b", "stat"),
        (r"\bcomparisons?\b", "comparison"),
        (r"\bquotes?\b", "quote"),
    ):
        if re.search(keyword, requested) and layout not in layouts:
            raise ValueError(f"the request explicitly asks for a {layout} slide")
    for keyword, layout in (
        (
            r"\b(?:fund(?:ing)? ask|fund allocation|use of funds|budget split|fundraising ask)\b",
            "ask_allocation",
        ),
        (r"\bmarket snapshot\b", "market_snapshot"),
        (r"\b(?:competitive landscape|competitor landscape)\b", "quadrants"),
        (r"\bflow steps\b", "flow_steps"),
    ):
        if re.search(keyword, requested) and layout not in layouts:
            raise ValueError(f"the request explicitly asks for a {layout} slide")
    if re.search(r"\b(?:workflow|user journey|how it works)\b", requested) and not (
        {"flow_steps", "process"} & set(layouts)
    ):
        raise ValueError("the requested workflow needs a flow_steps or process slide")
    if (
        re.search(
            r"\b(?:market size|market growth|market drivers|market segments|market analysis|cagr)\b|"
            r"\b(?:include|add|cover|analyze)\s+market\b",
            requested,
        )
        and "market_snapshot" not in layouts
    ):
        raise ValueError("the requested market analysis needs a market_snapshot slide")
    if (
        re.search(r"\b(?:competitors?|alternatives|ecosystem)\b", requested)
        and "quadrants" not in layouts
    ):
        raise ValueError("the requested competitor or ecosystem view needs a quadrants slide")
    if re.search(r"\bclosing slide\b", requested) and layouts[-1] != "closing":
        raise ValueError("the request asks for a closing slide, which must be last")


def _ensure_explicit_funding_layout(
    prompt: str, analysis: AnalysisOutput, outline: OutlineOutput
) -> OutlineOutput:
    """Route explicit funding asks and allocation facts to their dedicated slide."""
    ask_requested = re.search(
        r"\b(?:fund(?:ing)? ask|fund allocation|use of funds|budget split|fundraising ask)\b",
        prompt,
        re.IGNORECASE,
    )
    if not ask_requested:
        return outline

    plans = list(outline.slides)
    protected_layouts = {"ask_allocation"}
    requested = prompt.casefold()
    if re.search(r"\bmarket(?: snapshot| size| share| growth| CAGR)?\b", requested):
        protected_layouts.add("market_snapshot")
    if re.search(r"\b(?:workflow|user journey|how it works)\b", requested):
        protected_layouts.add("flow_steps")
    if re.search(r"\b(?:competitive landscape|competitor landscape)\b", requested):
        protected_layouts.add("quadrants")
    ask_indices = [index for index, plan in enumerate(plans) if plan.layout == "ask_allocation"]
    if ask_indices:
        target_index = ask_indices[0]
    else:
        candidates = [
            index
            for index, plan in enumerate(plans[1:], start=1)
            if plan.layout not in protected_layouts and plan.layout != "closing"
        ]
        if not candidates:
            candidates = [
                index for index, plan in enumerate(plans[1:], start=1) if plan.layout == "closing"
            ]
        if not candidates:
            raise ValueError(
                "the requested funding slide does not fit in the requested slide count"
            )
        funding_words = re.compile(
            r"\b(?:fund|funding|ask|raising|raise|allocation|budget|capital)\b",
            re.IGNORECASE,
        )
        target_index = next(
            (
                index
                for index in candidates
                if funding_words.search(
                    " ".join([plans[index].title, plans[index].purpose, *plans[index].key_points])
                )
            ),
            candidates[0],
        )
        plans[target_index] = plans[target_index].model_copy(
            update={
                "title": "Funding Ask & Allocation",
                "purpose": "Present the supplied funding ask and use-of-funds allocation.",
                "layout": "ask_allocation",
            }
        )

    funding_fact_pattern = re.compile(
        r"%|\b(?:Rs\.?|INR|USD|US\$|EUR|GBP|fund|funding|raising|allocation|"
        r"contingency|marketing|capex|team)\b|₹|\$|€|£",
        re.IGNORECASE,
    )
    funding_fact_ids = [
        fact.id for fact in analysis.required_facts if funding_fact_pattern.search(fact.text)
    ]
    for index, plan in enumerate(plans):
        if index == target_index:
            continue
        remaining_ids = [
            fact_id for fact_id in plan.required_fact_ids if fact_id not in funding_fact_ids
        ]
        if remaining_ids != plan.required_fact_ids:
            plans[index] = plan.model_copy(update={"required_fact_ids": remaining_ids})
    target = plans[target_index]
    plans[target_index] = target.model_copy(
        update={
            "required_fact_ids": list(dict.fromkeys([*target.required_fact_ids, *funding_fact_ids]))
        }
    )
    return outline.model_copy(update={"slides": plans})


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

    brief = short_beauty_brief(clean_prompt)
    if brief is not None:
        facts = {fact.id: fact for fact in analysis.required_facts}
        brand_ids = [
            fact.id
            for fact in analysis.required_facts
            if fact.text.casefold() == brief.brand.casefold()
        ]
        slogan_ids = [
            fact.id
            for fact in analysis.required_facts
            if "made only for you" in fact.text.casefold()
        ]
        product_ids = [
            fact.id
            for fact in analysis.required_facts
            if any(name in fact.text.casefold() for name in ("foundation", "lipstick", "skincare"))
        ]
        dimension_ids = [
            fact.id
            for fact in analysis.required_facts
            if any(name in fact.text.casefold() for name in ("skin tone", "undertone", "style"))
        ]
        promise_ids = [
            fact.id
            for fact in analysis.required_facts
            if "no more wrong shades" in fact.text.casefold()
        ]
        assigned = set(brand_ids + slogan_ids + product_ids + dimension_ids + promise_ids)
        leftovers = [fact.id for fact in analysis.required_facts if fact.id not in assigned]
        slides = [
            {
                "index": 0,
                "title": brief.brand,
                "purpose": "Introduce the brand using its supplied wording.",
                "layout": "title",
                "key_points": [facts[index].text for index in slogan_ids],
                "required_fact_ids": [*brand_ids, *slogan_ids],
            },
            {
                "index": 1,
                "title": f"The {brief.brand} collection",
                "purpose": "Present the named products and their supplied personalization detail.",
                "layout": "bullets",
                "key_points": [facts[index].text for index in product_ids],
                "required_fact_ids": product_ids,
            },
            {
                "index": 2,
                "title": "Matched to you",
                "purpose": "Show the stated matching dimensions beside the named products without inventing one-to-one pairings.",
                "layout": "two_column",
                "key_points": [
                    "Skin tone",
                    "Undertone",
                    "Style",
                    "Foundation",
                    "Lipstick",
                    "Skincare",
                ],
                "required_fact_ids": [],
            },
            {
                "index": 3,
                "title": "No more wrong shades",
                "purpose": "Close with the brand's supplied promise.",
                "layout": "closing",
                "key_points": [facts[index].text for index in promise_ids],
                "required_fact_ids": [*promise_ids, *leftovers],
            },
        ]
        return OutlineOutput(
            deck_title=brief.brand,
            subtitle=next(
                (
                    fact.text
                    for fact in analysis.required_facts
                    if "made only for you" in fact.text.casefold()
                ),
                None,
            ),
            slides=slides,
        )

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
            grounded_outline = _ground_outline(clean_prompt, analysis, outline)
            grounded_outline = _ensure_explicit_funding_layout(
                clean_prompt, analysis, grounded_outline
            )
            validate_outline_facts(analysis, grounded_outline)
            _validate_requested_layouts(clean_prompt, grounded_outline)
            return grounded_outline
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
