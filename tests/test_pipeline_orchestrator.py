import pytest
import time

from app.config import Settings
from app.core.deck_schema import BulletsSlide, ClosingSlide, TitleSlide
from app.core.errors import DeckReviewError
from app.pipeline.models import AnalysisOutput, OutlineOutput, ReviewOutput
from app.pipeline.orchestrator import generate_deck


class FakePipelineGenerator:
    def __init__(self, *, review_passed=True):
        self.review_passed = review_passed
        self.calls = []

    def generate(self, output_model, **kwargs):
        self.calls.append((output_model, kwargs))
        if output_model is AnalysisOutput:
            return AnalysisOutput(
                deck_title="Launch plan",
                deck_type="product launch",
                audience="customers",
                tone="clear",
                slide_count=3,
                required_facts=[{"id": 0, "text": "Launch is June 1", "verbatim": True}],
            )
        if output_model is OutlineOutput:
            return OutlineOutput(
                deck_title="Launch plan",
                subtitle="A short plan",
                slides=[
                    {"index": 0, "title": "Launch plan", "purpose": "Introduce", "layout": "title"},
                    {
                        "index": 1,
                        "title": "Launch date",
                        "purpose": "Explain timing",
                        "layout": "bullets",
                        "required_fact_ids": [0],
                    },
                    {"index": 2, "title": "Next steps", "purpose": "Close", "layout": "closing"},
                ],
            )
        if output_model is TitleSlide:
            return TitleSlide(layout="title", title="Launch plan", subtitle="A short plan")
        if output_model is BulletsSlide:
            return BulletsSlide(
                layout="bullets",
                title="Launch date",
                bullets=[{"text": "Launch is June 1"}, {"text": "Prepare the team"}],
            )
        if output_model is ClosingSlide:
            return ClosingSlide(layout="closing", title="Next steps")
        if output_model is ReviewOutput:
            if self.review_passed:
                return ReviewOutput(passed=True)
            return ReviewOutput(
                passed=False,
                issues=[
                    {
                        "category": "overflow",
                        "severity": "high",
                        "detail": "Shorten slide copy",
                        "slide_indices": [1],
                    }
                ],
                regenerate_slide_indices=[1],
            )
        raise AssertionError(f"unexpected model: {output_model}")


def _settings():
    return Settings(
        llm_provider="groq",
        api_key=None,
        default_model=None,
        analysis_model="analysis-model",
        outline_model="outline-model",
        writing_model="writing-model",
        review_model="review-model",
        structured_attempts=2,
    )


def test_orchestrator_runs_all_stages_and_returns_validated_deck():
    generator = FakePipelineGenerator()
    stages = []

    deck = generate_deck(
        "Make a launch presentation",
        settings=_settings(),
        generator=generator,
        theme="default",
        progress_callback=stages.append,
    )

    assert deck.title == "Launch plan"
    assert deck.subtitle == "A short plan"
    assert len(deck.slides) == 3
    assert [kwargs["stage"] for _, kwargs in generator.calls] == [
        "analyze",
        "outline",
        "write",
        "write",
        "write",
        "review",
    ]
    assert [kwargs["model"] for _, kwargs in generator.calls] == [
        "analysis-model",
        "outline-model",
        "writing-model",
        "writing-model",
        "writing-model",
        "review-model",
    ]
    assert stages == [
        "Analyzing request",
        "Planning slide sequence",
        "Writing slide content",
        "Reviewing slide content",
        "Reviewing generated slides",
        "Validating presentation",
    ]


def test_orchestrator_rejects_a_deck_that_fails_final_review():
    generator = FakePipelineGenerator(review_passed=False)

    with pytest.raises(DeckReviewError) as exc_info:
        generate_deck(
            "Make a launch presentation",
            settings=_settings(),
            generator=generator,
            max_repair_rounds=0,
        )

    assert exc_info.value.repair_rounds == 0
    assert exc_info.value.review.passed is False


def test_orchestrator_validates_limits_before_calling_generator():
    generator = FakePipelineGenerator()
    with pytest.raises(ValueError, match="max_workers"):
        generate_deck(
            "Make a launch presentation",
            settings=_settings(),
            generator=generator,
            max_workers=0,
        )
    assert generator.calls == []


def test_orchestrator_rejects_empty_prompt_before_calling_generator():
    generator = FakePipelineGenerator()
    with pytest.raises(ValueError, match="must not be empty"):
        generate_deck("  ", settings=_settings(), generator=generator)
    assert generator.calls == []


def test_orchestrator_checks_job_deadline_before_starting_generation():
    generator = FakePipelineGenerator()
    with pytest.raises(TimeoutError, match="exceeded its time limit"):
        generate_deck(
            "Make a launch presentation",
            settings=_settings(),
            generator=generator,
            deadline=time.monotonic() - 1,
        )
    assert generator.calls == []
