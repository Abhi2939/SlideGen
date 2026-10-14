import pytest

from app.pipeline.analyze import MAX_PROMPT_CHARS, analyze_prompt
from app.pipeline.models import AnalysisOutput, OutlineOutput
from app.pipeline.outline import create_outline


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
    assert "Every key point must be supported" in generator.calls[0][1]["system_prompt"]
    assert "planning aids, not evidence" in generator.calls[0][1]["system_prompt"]
    assert "customer behavior" in generator.calls[0][1]["system_prompt"]


def test_outline_fails_after_semantic_retries():
    generator = FakeGenerator([_outline(fact_ids=()), _outline(fact_ids=())])

    with pytest.raises(ValueError, match="failed semantic validation after 2 attempt"):
        create_outline("Make a launch deck", _analysis(), generator, "small-model")


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
        "title", "bullets", "two_column", "closing"
    ]
    assert result.slides[0].title == "Blush"
    assert not generator.calls


def test_outline_rejects_empty_prompt_before_model_call():
    generator = FakeGenerator([])
    with pytest.raises(ValueError, match="must not be empty"):
        create_outline("  ", _analysis(), generator, "small-model")
    assert generator.calls == []
