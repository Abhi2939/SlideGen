import pytest

from app.core.deck_schema import BulletsSlide, ClosingSlide, TitleSlide
from app.pipeline.models import AnalysisOutput, OutlineOutput, ReviewOutput
from app.pipeline.review import review_and_repair_deck, review_deck


class FakeReviewGenerator:
    def __init__(self, reviews):
        self.reviews = list(reviews)
        self.calls = []

    def generate(self, output_model, **kwargs):
        self.calls.append((output_model, kwargs))
        if output_model is ReviewOutput:
            return self.reviews.pop(0)
        if output_model is BulletsSlide:
            return BulletsSlide(
                layout="bullets",
                title="Launch date",
                bullets=[{"text": "Launch is June 1"}, {"text": "Prepare the team"}],
            )
        raise AssertionError(f"unexpected output model: {output_model}")


def _inputs():
    analysis = AnalysisOutput(
        deck_title="Launch plan",
        deck_type="product launch",
        slide_count=3,
        required_facts=[{"id": 0, "text": "Launch is June 1", "verbatim": True}],
    )
    outline = OutlineOutput(
        deck_title="Launch plan",
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
    slides = [
        TitleSlide(layout="title", title="Launch plan"),
        BulletsSlide(
            layout="bullets",
            title="Launch date",
            bullets=[{"text": "Old wording"}, {"text": "Prepare the team"}],
        ),
        ClosingSlide(layout="closing", title="Next steps"),
    ]
    return analysis, outline, slides


def test_review_retries_invalid_slide_references():
    analysis, outline, slides = _inputs()
    generator = FakeReviewGenerator(
        [
            ReviewOutput(
                passed=False,
                issues=[
                    {
                        "category": "overflow",
                        "severity": "high",
                        "detail": "Slide too long",
                        "slide_indices": [5],
                    }
                ],
                regenerate_slide_indices=[5],
            ),
            ReviewOutput(passed=True),
        ]
    )

    result = review_deck("Make a launch deck", analysis, outline, slides, generator, "review-model")

    assert result.passed
    assert len(generator.calls) == 2
    assert "out-of-range slide indices" in generator.calls[1][1]["user_prompt"]


def test_review_rejects_missing_repair_target_and_retries():
    analysis, outline, slides = _inputs()
    generator = FakeReviewGenerator(
        [
            ReviewOutput(
                passed=False,
                issues=[
                    {
                        "category": "overflow",
                        "severity": "high",
                        "detail": "Slide too long",
                        "slide_indices": [1],
                    }
                ],
                regenerate_slide_indices=[],
            ),
            ReviewOutput(
                passed=False,
                issues=[
                    {
                        "category": "overflow",
                        "severity": "high",
                        "detail": "Slide too long",
                        "slide_indices": [1],
                    }
                ],
                regenerate_slide_indices=[1],
            ),
        ]
    )

    result = review_deck(
        "Make a launch deck", analysis, outline, slides, generator, "review-model"
    )

    assert not result.passed
    assert len(generator.calls) == 2
    assert "regenerate_slide_indices must include every slide" in generator.calls[1][1]["user_prompt"]


def test_review_and_repair_rewrites_only_flagged_slide_then_rechecks():
    analysis, outline, slides = _inputs()
    issue = {
        "category": "overflow",
        "severity": "high",
        "detail": "Shorten this slide",
        "slide_indices": [1],
        "repair_instruction": "Keep the launch fact and shorten other text.",
    }
    generator = FakeReviewGenerator(
        [
            ReviewOutput(passed=False, issues=[issue], regenerate_slide_indices=[1]),
            ReviewOutput(passed=True),
        ]
    )

    result = review_and_repair_deck(
        "Make a launch deck",
        analysis,
        outline,
        slides,
        generator,
        "review-model",
        "writing-model",
    )

    assert result.review.passed
    assert result.repair_rounds == 1
    assert result.slides[0] is slides[0]
    assert result.slides[2] is slides[2]
    assert result.slides[1] is not slides[1]
    stages = [kwargs["stage"] for _, kwargs in generator.calls]
    assert stages == ["review", "write", "review"]
    assert "straightforward" in generator.calls[0][1]["system_prompt"]
    assert "Do not require contact details" in generator.calls[0][1]["system_prompt"]
    assert "outline as planning guidance that can contain errors" in generator.calls[0][1]["system_prompt"]
    write_call = next(kwargs for _, kwargs in generator.calls if kwargs["stage"] == "write")
    assert "Keep the launch fact and shorten other text." in write_call["user_prompt"]


def test_review_rejects_empty_prompt_without_calling_model():
    analysis, outline, slides = _inputs()
    generator = FakeReviewGenerator([])
    with pytest.raises(ValueError, match="must not be empty"):
        review_deck(" ", analysis, outline, slides, generator, "review-model")
    assert generator.calls == []
