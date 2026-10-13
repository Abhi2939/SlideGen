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
    assert "Do not add investor contact" in generator.calls[0][1]["system_prompt"]


def test_outline_fails_after_semantic_retries():
    generator = FakeGenerator([_outline(fact_ids=()), _outline(fact_ids=())])

    with pytest.raises(ValueError, match="failed semantic validation after 2 attempt"):
        create_outline("Make a launch deck", _analysis(), generator, "small-model")


def test_outline_rejects_empty_prompt_before_model_call():
    generator = FakeGenerator([])
    with pytest.raises(ValueError, match="must not be empty"):
        create_outline("  ", _analysis(), generator, "small-model")
    assert generator.calls == []
