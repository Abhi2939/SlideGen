import pytest

from app.pipeline.analyze import MAX_PROMPT_CHARS, analyze_prompt
from app.pipeline.grounding import unsupported_claim_categories
from app.pipeline.models import AnalysisOutput, OutlineOutput
from app.pipeline.outline import (
    _ensure_explicit_funding_layout,
    _ground_outline,
    _validate_requested_layouts,
    create_outline,
)


class FakeGenerator:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def generate(self, output_model, **kwargs):
        self.calls.append((output_model, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _analysis():
    return AnalysisOutput(
        deck_title="Launch plan",
        deck_type="product launch",
        audience="customers",
        tone="clear",
        slide_count=3,
        required_facts=[{"id": 0, "text": "Launch is June 1", "verbatim": True}],
    )


def _outline(*, title="Launch plan", fact_ids=(0,)):
    return OutlineOutput(
        deck_title=title,
        slides=[
            {"index": 0, "title": title, "purpose": "Introduce the plan", "layout": "title"},
            {
                "index": 1,
                "title": "Launch date",
                "purpose": "Explain the timing",
                "layout": "bullets",
                "required_fact_ids": list(fact_ids),
            },
            {"index": 2, "title": "Next steps", "purpose": "Close", "layout": "closing"},
        ],
    )


def test_analyze_prompt_calls_structured_generator_with_analysis_contract():
    expected = _analysis()
    generator = FakeGenerator([expected])

    result = analyze_prompt("  Make a product launch deck.  ", generator, "small-model")

    assert result is expected
    output_model, kwargs = generator.calls[0]
    assert output_model is AnalysisOutput
    assert kwargs["stage"] == "analyze"
    assert kwargs["model"] == "small-model"
    assert "Make a product launch deck." in kwargs["user_prompt"]
    assert "Never infer an investor pitch" in kwargs["system_prompt"]
    assert "Treat labels such as 'Startup name'" in kwargs["system_prompt"]
    assert "Separate factual source material from" in kwargs["system_prompt"]
    assert "presentation instructions" in kwargs["system_prompt"]


def test_analyze_prompt_preserves_explicit_funding_amount_as_verbatim_fact():
    generator = FakeGenerator([_analysis()])

    result = analyze_prompt(
        "Acme Pay is raising Rs. 14 Cr. Use of funds: Team 3.02%.",
        generator,
        "small-model",
    )

    assert [(fact.id, fact.text, fact.verbatim) for fact in result.required_facts] == [
        (0, "Launch is June 1", True),
        (1, "Rs. 14 Cr", True),
    ]


def test_analysis_reserves_space_for_each_requested_reference_layout():
    generator = FakeGenerator([_analysis()])

    result = analyze_prompt(
        "Acme Pay is raising Rs. 14 Cr. Use of funds. Include market, how it works, "
        "and competitive landscape.",
        generator,
        "small-model",
    )

    assert result.slide_count == 5


def test_analyze_short_branded_beauty_brief_keeps_brand_and_exact_facts():
    generator = FakeGenerator([_analysis()])
    prompt = (
        "Startup name blush, Makeup made only for YOU. Custom-blended foundation, "
        "lipstick and skincare matched to your skin tone, undertone & style. "
        "No more wrong shades."
    )

    result = analyze_prompt(prompt, generator, "small-model")

    assert result.deck_title == "Blush"
    assert result.slide_count == 4
    assert [fact.text for fact in result.required_facts] == [
        "Blush",
        "Makeup made only for YOU.",
        "Custom-blended foundation, lipstick and skincare matched to your skin tone, undertone & style.",
        "No more wrong shades.",
    ]
    assert all(fact.verbatim for fact in result.required_facts)


@pytest.mark.parametrize("prompt", ["", "  "])
def test_analyze_rejects_empty_prompt_without_calling_model(prompt):
    generator = FakeGenerator([])
    with pytest.raises(ValueError, match="must not be empty"):
        analyze_prompt(prompt, generator, "small-model")
    assert generator.calls == []


def test_analyze_rejects_oversized_prompt_without_calling_model():
    generator = FakeGenerator([])
    with pytest.raises(ValueError, match="at most"):
        analyze_prompt("x" * (MAX_PROMPT_CHARS + 1), generator, "small-model")
    assert generator.calls == []


def test_outline_retries_semantic_failure_and_includes_correction():
    generator = FakeGenerator([_outline(fact_ids=()), _outline()])

    result = create_outline("Make a launch deck", _analysis(), generator, "small-model")

    assert result.deck_title == "Launch plan"
    assert len(generator.calls) == 2
    assert "omits required fact IDs" in generator.calls[1][1]["user_prompt"]
    assert "Do not add contact details" in generator.calls[0][1]["system_prompt"]
    assert (
        "Every specific factual key point must be supported"
        in generator.calls[0][1]["system_prompt"]
    )
    assert "planning aids, not evidence" in generator.calls[0][1]["system_prompt"]
    assert "customer behavior" in generator.calls[0][1]["system_prompt"]


def test_outline_fails_after_semantic_retries():
    generator = FakeGenerator([_outline(fact_ids=()), _outline(fact_ids=())])

    with pytest.raises(ValueError, match="failed semantic validation after 2 attempt"):
        create_outline("Make a launch deck", _analysis(), generator, "small-model")


def test_outline_requires_two_visual_slides_when_prompt_requests_charts():
    with pytest.raises(ValueError, match="asks for a chart slide"):
        _validate_requested_layouts("Create a deck and include charts and figures", _outline())
    with pytest.raises(ValueError, match="at least two visual slides"):
        _validate_requested_layouts("Create a deck with figures", _outline())


def test_outline_honors_explicit_layout_requests():
    outline = OutlineOutput(
        deck_title="Launch plan",
        slides=[
            {"index": 0, "title": "Launch plan", "purpose": "Introduce", "layout": "title"},
            {"index": 1, "title": "Launch date", "purpose": "Explain", "layout": "bullets"},
            {"index": 2, "title": "Next steps", "purpose": "Close", "layout": "closing"},
        ],
    )
    with pytest.raises(ValueError, match="process slide"):
        _validate_requested_layouts("Add a process slide", outline)


def test_outline_requires_reference_layouts_when_explicitly_requested():
    outline = _outline()
    with pytest.raises(ValueError, match="ask_allocation"):
        _validate_requested_layouts("Include a fund allocation slide", outline)
    with pytest.raises(ValueError, match="quadrants"):
        _validate_requested_layouts("Include a competitive landscape", outline)


def test_reference_prompt_matrix_routes_content_to_reference_layouts():
    acme = OutlineOutput(
        deck_title="Acme Pay",
        slides=[
            {"index": 0, "title": "Acme Pay", "purpose": "Introduce", "layout": "title"},
            {
                "index": 1,
                "title": "The Ask",
                "purpose": "Funding split",
                "layout": "ask_allocation",
            },
            {
                "index": 2,
                "title": "Market",
                "purpose": "Market metrics",
                "layout": "market_snapshot",
            },
            {"index": 3, "title": "Journey", "purpose": "Conceptual steps", "layout": "flow_steps"},
            {"index": 4, "title": "Landscape", "purpose": "Categories", "layout": "quadrants"},
            {"index": 5, "title": "Close", "purpose": "Close", "layout": "closing"},
        ],
    )
    _validate_requested_layouts(
        "Acme Pay raising Rs 14 Cr. Use of funds and include market, how it works, "
        "and competitive landscape.",
        acme,
    )

    fintech = _outline()
    with pytest.raises(ValueError, match="market_snapshot"):
        _validate_requested_layouts("FinFlow market size, CAGR and growth drivers", fintech)

    blush = _outline()
    with pytest.raises(ValueError, match="workflow"):
        _validate_requested_layouts("Blush: explain how it works", blush)

    dogs = _outline()
    _validate_requested_layouts("Make a deck about dogs", dogs)


def test_explicit_funding_layout_is_restored_without_replacing_other_reference_slides():
    prompt = (
        "Acme Pay raising Rs. 14 Cr. Use of funds: Team 3.02%. Include market, "
        "how it works, and competitive landscape."
    )
    analysis = AnalysisOutput(
        deck_title="Acme Pay",
        deck_type="fundraising",
        slide_count=6,
        required_facts=[
            {"id": 0, "text": "Rs. 14 Cr", "verbatim": True},
            {"id": 1, "text": "Team 3.02%", "verbatim": True},
        ],
    )
    outline = OutlineOutput(
        deck_title="Acme Pay",
        slides=[
            {"index": 0, "title": "Acme Pay", "purpose": "Introduce", "layout": "title"},
            {
                "index": 1,
                "title": "Funding",
                "purpose": "Present the funding plan",
                "layout": "bullets",
            },
            {"index": 2, "title": "Market", "purpose": "Market facts", "layout": "market_snapshot"},
            {"index": 3, "title": "Journey", "purpose": "Workflow", "layout": "flow_steps"},
            {"index": 4, "title": "Landscape", "purpose": "Competitors", "layout": "quadrants"},
            {"index": 5, "title": "Close", "purpose": "Close", "layout": "closing"},
        ],
    )

    result = _ensure_explicit_funding_layout(prompt, analysis, outline)

    assert result.slides[1].layout == "ask_allocation"
    assert result.slides[1].required_fact_ids == [0, 1]
    assert {slide.layout for slide in result.slides} >= {
        "ask_allocation",
        "market_snapshot",
        "flow_steps",
        "quadrants",
    }
    validate_ids = [fact_id for slide in result.slides for fact_id in slide.required_fact_ids]
    assert set(validate_ids) == {0, 1}


@pytest.mark.parametrize(
    "prompt,claim",
    [
        (
            "Startup name: SwiftCrate. We ship refrigerated groceries to apartments in Bangalore within 30 minutes. Customers order through our website and app.",
            "SwiftCrate delivers refrigerated groceries through its website.",
        ),
        (
            "Create a deck introducing our AI-powered resume screening tool to HR managers.",
            "The AI-powered resume screening tool supports HR managers.",
        ),
        (
            "Explain why customers often struggle to find affordable housing for a city council briefing.",
            "Customers often struggle to find affordable housing.",
        ),
    ],
)
def test_grounding_allows_claim_categories_already_in_source(prompt, claim):
    assert unsupported_claim_categories(claim, prompt) == []


def test_tier_two_problem_framing_is_allowed_but_market_figures_remain_guarded():
    assert (
        unsupported_claim_categories(
            "Customers often struggle to compare available options.",
            "Create a product introduction.",
            allow_problem_framing=True,
        )
        == []
    )
    assert unsupported_claim_categories(
        "Market growth is accelerating.",
        "Create a market overview.",
        allow_problem_framing=True,
    ) == ["unsupported market or consumer behavior"]


def test_grounded_outline_does_not_use_full_request_as_fallback_key_point():
    analysis = _analysis().model_copy(update={"required_facts": []})
    outline = OutlineOutput(
        deck_title="Launch plan",
        slides=[
            {"index": 0, "title": "Launch plan", "purpose": "Introduce", "layout": "title"},
            {
                "index": 1,
                "title": "How It Works",
                "purpose": "Explain a process",
                "layout": "process",
                "key_points": ["AI analysis", "We assess customers", "Products ship next day"],
            },
            {"index": 2, "title": "Next steps", "purpose": "Close", "layout": "closing"},
        ],
    )

    result = _ground_outline("Make a launch deck", analysis, outline)

    assert result.slides[1].key_points == []
    assert result.slides[1].layout == "bullets"


def test_outline_replaces_unsupported_process_points_with_assigned_facts():
    analysis = _analysis()
    outline = OutlineOutput(
        deck_title="Launch plan",
        slides=[
            {"index": 0, "title": "Launch plan", "purpose": "Introduce", "layout": "title"},
            {
                "index": 1,
                "title": "How It Works",
                "purpose": "Explain the process",
                "layout": "process",
                "key_points": ["AI analysis", "We assess the customer", "Products ship next day"],
                "required_fact_ids": [0],
            },
            {"index": 2, "title": "Next steps", "purpose": "Close", "layout": "closing"},
        ],
    )
    generator = FakeGenerator([outline])

    result = create_outline("Make a launch deck", analysis, generator, "small-model")

    process_replacement = result.slides[1]
    assert process_replacement.layout == "bullets"
    assert process_replacement.title == "Product Details"
    assert process_replacement.key_points == ["Launch is June 1"]


def test_flow_outline_allows_conceptual_assessment_and_selection_steps():
    analysis = _analysis()
    outline = OutlineOutput(
        deck_title="Launch plan",
        slides=[
            {"index": 0, "title": "Launch plan", "purpose": "Introduce", "layout": "title"},
            {
                "index": 1,
                "title": "Suggested journey",
                "purpose": "Explain conceptual steps",
                "layout": "flow_steps",
                "key_points": ["Assess needs", "Select an option"],
                "required_fact_ids": [0],
            },
            {"index": 2, "title": "Next steps", "purpose": "Close", "layout": "closing"},
        ],
    )

    result = _ground_outline("Make a launch deck", analysis, outline)

    assert result.slides[1].layout == "flow_steps"
    assert result.slides[1].key_points == ["Assess needs", "Select an option"]


def test_outline_uses_compact_branded_beauty_structure_without_more_llm_calls():
    prompt = (
        "Startup name blush, Makeup made only for YOU. Custom-blended foundation, "
        "lipstick and skincare matched to your skin tone, undertone & style. "
        "No more wrong shades."
    )
    analysis = AnalysisOutput(
        deck_title="Blush",
        deck_type="branded beauty product introduction",
        slide_count=4,
        required_facts=[
            {"id": 0, "text": "Blush", "verbatim": True},
            {"id": 1, "text": "Makeup made only for YOU.", "verbatim": True},
            {
                "id": 2,
                "text": "Custom-blended foundation, lipstick and skincare matched to your skin tone, undertone & style.",
                "verbatim": True,
            },
            {"id": 3, "text": "No more wrong shades.", "verbatim": True},
        ],
    )
    generator = FakeGenerator([])

    result = create_outline(prompt, analysis, generator, "small-model")

    assert len(result.slides) == 4
    assert [slide.layout for slide in result.slides] == [
        "title",
        "bullets",
        "two_column",
        "closing",
    ]
    assert result.slides[0].title == "Blush"
    assert not generator.calls


def test_outline_rejects_empty_prompt_before_model_call():
    generator = FakeGenerator([])
    with pytest.raises(ValueError, match="must not be empty"):
        create_outline("  ", _analysis(), generator, "small-model")
    assert generator.calls == []
