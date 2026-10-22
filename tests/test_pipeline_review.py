import json

import pytest

from app.core.deck_schema import AskAllocationSlide, BulletsSlide, ClosingSlide, TitleSlide
from app.core.errors import ProviderLimitError
from app.pipeline.models import AnalysisOutput, OutlineOutput, RequiredFact, ReviewOutput
from app.pipeline.review import review_and_repair_deck, review_deck


class FakeReviewGenerator:
    def __init__(self, reviews):
        self.reviews = list(reviews)
        self.calls = []

    def generate(self, output_model, **kwargs):
        self.calls.append((output_model, kwargs))
        if output_model is ReviewOutput:
            response = self.reviews.pop(0)
            if isinstance(response, Exception):
                raise response
            return response
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


def test_four_slide_review_corrects_phantom_slides_then_repairs_real_slide():
    analysis, outline, slides = _inputs()
    analysis = analysis.model_copy(update={"slide_count": 4})
    outline = outline.model_copy(
        update={
            "slides": [
                *outline.slides[:2],
                outline.slides[1].model_copy(
                    update={"index": 2, "title": "Preparation", "required_fact_ids": []}
                ),
                outline.slides[2].model_copy(update={"index": 3}),
            ]
        }
    )
    slides.insert(
        2,
        BulletsSlide(
            layout="bullets",
            title="Preparation",
            bullets=[{"text": "Confirm the launch activities"}, {"text": "Plan next actions"}],
        ),
    )
    rejected = ReviewOutput(
        passed=False,
        issues=[
            {
                "category": "overflow",
                "severity": "high",
                "detail": "Shorten launch copy",
                "slide_indices": [4, 5],
            }
        ],
        regenerate_slide_indices=[4, 5],
    )
    corrected = ReviewOutput(
        passed=False,
        issues=[rejected.issues[0].model_copy(update={"slide_indices": [1]})],
        regenerate_slide_indices=[1],
    )
    generator = FakeReviewGenerator([rejected, corrected, ReviewOutput(passed=True)])

    result = review_and_repair_deck(
        "Make a four-slide launch deck. Launch is June 1.",
        analysis,
        outline,
        slides,
        generator,
        "review-model",
        "write-model",
    )

    assert result.review.passed
    assert result.repair_rounds == 1
    assert result.slides[1] is not slides[1]
    assert result.slides[2] is slides[2]
    assert [kwargs["stage"] for _, kwargs in generator.calls] == [
        "review",
        "review",
        "repair",
        "review",
    ]
    first_request = generator.calls[0][1]["user_prompt"]
    assert "exactly 4 slides" in first_request
    assert "Valid slide indices: [0, 1, 2, 3]" in first_request
    assert "Valid required fact IDs: [0]" in first_request
    schema = generator.calls[0][1]["response_schema"]
    assert schema["properties"]["regenerate_slide_indices"]["items"]["enum"] == [0, 1, 2, 3]
    assert schema["$defs"]["ReviewIssue"]["properties"]["slide_indices"]["items"]["enum"] == [
        0,
        1,
        2,
        3,
    ]
    assert schema["properties"]["missing_fact_ids"]["items"]["enum"] == [0]
    content = json.loads(
        first_request.split(
            "GENERATED DECK (each slide has an explicit zero-based slide_index):\n"
        )[1]
    )
    assert [slide["slide_index"] for slide in content] == [0, 1, 2, 3]
    assert [slide["content"] for slide in content] == [s.model_dump(mode="json") for s in slides]
    retry = generator.calls[1][1]["user_prompt"]
    assert "Valid slide indices: [0, 1, 2, 3]" in retry.split("CORRECTION REQUIRED:")[1]
    assert json.loads(retry.split("REJECTED REVIEW JSON:\n")[1]) == rejected.model_dump()


@pytest.mark.parametrize("invalid_field", ["issue", "regenerate"])
def test_invalid_references_cannot_be_hidden_by_missing_fact_filter(invalid_field):
    analysis, outline, slides = _inputs()
    slides[1] = BulletsSlide(
        layout="bullets",
        title="Launch date",
        bullets=[{"text": "Launch is June 1"}, {"text": "Prepare the team"}],
    )
    rejected = ReviewOutput(
        passed=False,
        missing_fact_ids=[0],
        issues=[
            {
                "category": "missing_fact",
                "severity": "high",
                "detail": "Missing 'Launch is June 1'",
                "slide_indices": [4] if invalid_field == "issue" else [1],
            }
        ],
        regenerate_slide_indices=[4] if invalid_field == "regenerate" else [1],
    )
    generator = FakeReviewGenerator([rejected, ReviewOutput(passed=True)])

    result = review_deck("Make a launch deck", analysis, outline, slides, generator, "fake")

    assert result.passed
    assert len(generator.calls) == 2
    assert "out-of-range slide indices: [4]" in generator.calls[1][1]["user_prompt"]


@pytest.mark.parametrize("indices", [[4, 5], [-1], [3]])
def test_repeated_invalid_review_indices_fail_with_bounded_calls(indices):
    analysis, outline, slides = _inputs()
    rejected = ReviewOutput(
        passed=False,
        issues=[
            {
                "category": "unsupported_claim",
                "severity": "high",
                "detail": "Unsupported claims",
                "slide_indices": indices,
            }
        ],
        regenerate_slide_indices=indices,
    )
    generator = FakeReviewGenerator([rejected, rejected])

    with pytest.raises(ValueError, match="review failed semantic validation after 2 attempt"):
        review_deck("Make a launch deck", analysis, outline, slides, generator, "fake")

    assert len(generator.calls) == 2


def test_review_retry_distinguishes_fact_ids_from_slide_indices():
    analysis, outline, slides = _inputs()
    analysis = analysis.model_copy(
        update={"required_facts": [analysis.required_facts[0].model_copy(update={"id": 7})]}
    )
    outline = outline.model_copy(
        update={
            "slides": [
                p.model_copy(update={"required_fact_ids": [7]}) if p.index == 1 else p
                for p in outline.slides
            ]
        }
    )
    generator = FakeReviewGenerator(
        [
            ReviewOutput(passed=False, missing_fact_ids=[1], regenerate_slide_indices=[1]),
            ReviewOutput(passed=False, missing_fact_ids=[7]),
        ]
    )

    review = review_deck("Make a launch deck", analysis, outline, slides, generator, "fake")

    assert not review.passed
    assert review.missing_fact_ids == [7]
    assert review.regenerate_slide_indices == [1]
    assert "Valid required fact IDs: [7]" in generator.calls[1][1]["user_prompt"]


def test_provider_limit_during_review_correction_stops_immediately():
    analysis, outline, slides = _inputs()
    rejected = ReviewOutput(passed=False, regenerate_slide_indices=[4, 5])
    generator = FakeReviewGenerator([rejected, ProviderLimitError(), ReviewOutput(passed=True)])

    with pytest.raises(ProviderLimitError):
        review_deck(
            "Make a launch deck", analysis, outline, slides, generator, "fake", semantic_attempts=3
        )

    assert len(generator.calls) == 2


def test_review_schema_with_no_facts_requires_empty_missing_ids_and_is_request_local():
    analysis, outline, slides = _inputs()
    analysis = analysis.model_copy(update={"required_facts": []})
    outline = outline.model_copy(
        update={"slides": [p.model_copy(update={"required_fact_ids": []}) for p in outline.slides]}
    )
    generator = FakeReviewGenerator([ReviewOutput(passed=True)])

    review_deck("Make a launch deck", analysis, outline, slides, generator, "fake")

    schema = generator.calls[0][1]["response_schema"]
    assert schema["properties"]["missing_fact_ids"]["maxItems"] == 0
    assert schema["properties"]["regenerate_slide_indices"]["items"]["enum"] == [0, 1, 2]
    # Neither a previous deck size nor the empty-fact restriction can leak into
    # a subsequent request through the shared contract class.
    assert (
        "enum"
        not in ReviewOutput.model_json_schema()["properties"]["regenerate_slide_indices"]["items"]
    )
    assert ReviewOutput.model_json_schema()["properties"]["missing_fact_ids"]["maxItems"] == 80


def test_review_adds_missing_repair_target_from_issue_references():
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

    result = review_deck("Make a launch deck", analysis, outline, slides, generator, "review-model")

    assert not result.passed
    assert len(generator.calls) == 1
    assert result.regenerate_slide_indices == [1]


def test_review_ignores_missing_fact_already_visible_on_another_slide():
    analysis, outline, slides = _inputs()
    slides[1] = BulletsSlide(
        layout="bullets",
        title="Launch date",
        bullets=[{"text": "Launch is June 1"}, {"text": "Prepare the team"}],
    )
    generator = FakeReviewGenerator(
        [
            ReviewOutput(
                passed=False,
                issues=[
                    {
                        "category": "missing_fact",
                        "severity": "medium",
                        "detail": "Missing required fact 'Launch is June 1' on slide 0.",
                        "slide_indices": [0],
                    }
                ],
                missing_fact_ids=[0],
                regenerate_slide_indices=[0],
            )
        ]
    )

    result = review_deck("Make a launch deck", analysis, outline, slides, generator, "review-model")

    assert result.passed
    assert result.issues == []
    assert result.missing_fact_ids == []


def test_investor_review_accepts_placeholder_problem_slide():
    analysis, outline, slides = _inputs()
    analysis = analysis.model_copy(update={"deck_type": "investor pitch", "required_facts": []})
    plans = list(outline.slides)
    plans[1] = plans[1].model_copy(
        update={
            "title": "The Problem",
            "purpose": "Add verified problem points",
            "required_fact_ids": [],
        }
    )
    outline = outline.model_copy(update={"slides": plans})
    slides[1] = BulletsSlide(
        layout="bullets",
        title="The Problem",
        bullets=[
            {"text": "Add a verified company detail from the source material."},
            {"text": "Replace this placeholder with information you provide."},
        ],
    )
    generator = FakeReviewGenerator(
        [
            ReviewOutput(
                passed=False,
                issues=[
                    {
                        "category": "unsupported_claim",
                        "severity": "high",
                        "detail": "Slide contains placeholder text and lacks key points.",
                        "slide_indices": [1],
                    }
                ],
                regenerate_slide_indices=[1],
            )
        ]
    )

    result = review_deck(
        "Create a seed investor pitch.", analysis, outline, slides, generator, "review-model"
    )

    assert result.passed


def test_investor_review_accepts_missing_funding_ask_placeholder():
    analysis, outline, _ = _inputs()
    analysis = analysis.model_copy(update={"deck_type": "investor pitch", "required_facts": []})
    plans = list(outline.slides)
    plans[1] = plans[1].model_copy(
        update={"title": "Funding Ask", "layout": "ask_allocation", "required_fact_ids": []}
    )
    outline = outline.model_copy(update={"slides": plans})
    slides = [
        TitleSlide(layout="title", title="Launch plan"),
        AskAllocationSlide(
            layout="ask_allocation",
            title="Funding Ask",
            ask=None,
            items=[{"label": "Category 1"}, {"label": "Category 2"}],
        ),
        ClosingSlide(layout="closing", title="Next steps"),
    ]
    generator = FakeReviewGenerator(
        [
            ReviewOutput(
                passed=False,
                issues=[
                    {
                        "category": "other",
                        "severity": "low",
                        "detail": "Funding Ask slide missing ask details.",
                        "slide_indices": [1],
                    }
                ],
                regenerate_slide_indices=[1],
            )
        ]
    )

    result = review_deck(
        "Create a seed investor pitch.", analysis, outline, slides, generator, "review-model"
    )

    assert result.passed


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
    assert stages == ["review", "repair", "review"]
    assert "straightforward" in generator.calls[0][1]["system_prompt"]
    assert "Do not require contact details" in generator.calls[0][1]["system_prompt"]
    assert (
        "outline as planning guidance that can contain errors"
        in generator.calls[0][1]["system_prompt"]
    )
    write_call = next(kwargs for _, kwargs in generator.calls if kwargs["stage"] == "repair")
    assert "Keep the launch fact and shorten other text." in write_call["user_prompt"]


def test_sparse_company_review_replaces_unsupported_copy_with_placeholders():
    analysis, outline, slides = _inputs()
    analysis = analysis.model_copy(
        update={"required_facts": [RequiredFact(id=0, text="Electronics company", verbatim=True)]}
    )
    plans = list(outline.slides)
    plans[0] = plans[0].model_copy(update={"required_fact_ids": []})
    plans[1] = plans[1].model_copy(update={"required_fact_ids": [0]})
    outline = outline.model_copy(update={"slides": plans})
    slides[1] = BulletsSlide(
        layout="bullets",
        title="Launch date",
        bullets=[{"text": "Global reach"}, {"text": "Customer support"}],
    )
    generator = FakeReviewGenerator(
        [
            ReviewOutput(
                passed=False,
                issues=[
                    {
                        "category": "unsupported_claim",
                        "severity": "high",
                        "detail": "These company details are unsupported.",
                        "slide_indices": [1],
                    }
                ],
                regenerate_slide_indices=[1],
            ),
            ReviewOutput(passed=True),
        ]
    )

    result = review_and_repair_deck(
        "Create a presentation for my electronics company.",
        analysis,
        outline,
        slides,
        generator,
        "review-model",
        "writing-model",
    )

    assert result.review.passed
    assert result.repair_rounds == 1
    assert result.slides[1].title == "Details to supply 2"
    assert any(bullet.text == "Electronics company" for bullet in result.slides[1].bullets)
    assert "Global reach" not in str(result.slides[1].model_dump())


def test_review_ignores_unsupported_claim_finding_for_grounded_paraphrase():
    analysis, outline, slides = _inputs()
    source = (
        "Makeup made only for YOU. Custom-blended foundation, lipstick and skincare "
        "matched to your skin tone, undertone & style."
    )
    claim = "Personalized formulas that complement your unique skin tone, undertone, and style."
    slides[1] = BulletsSlide(
        layout="bullets",
        title="A match made for you",
        bullets=[{"text": claim}, {"text": "Explore the collection"}],
    )
    generator = FakeReviewGenerator(
        [
            ReviewOutput(
                passed=False,
                issues=[
                    {
                        "category": "unsupported_claim",
                        "severity": "medium",
                        "detail": f"Slide 1 contains an unsupported claim: '{claim}'",
                        "slide_indices": [1],
                        "repair_instruction": "Remove the claim.",
                    }
                ],
                regenerate_slide_indices=[1],
            )
        ]
    )

    result = review_deck(source, analysis, outline, slides, generator, "review-model")

    assert result.passed


def test_review_keeps_unsupported_claim_that_adds_a_guarantee():
    analysis, outline, slides = _inputs()
    source = "Custom-blended foundation matched to your skin tone and style."
    claim = "Clinically proven to last 24 hours."
    slides[1] = BulletsSlide(
        layout="bullets",
        title="Long-lasting wear",
        bullets=[{"text": claim}, {"text": "Explore the collection"}],
    )
    generator = FakeReviewGenerator(
        [
            ReviewOutput(
                passed=False,
                issues=[
                    {
                        "category": "unsupported_claim",
                        "severity": "high",
                        "detail": f"Slide 1 makes an unsupported claim: '{claim}'",
                        "slide_indices": [1],
                    }
                ],
                regenerate_slide_indices=[1],
            )
        ]
    )

    result = review_deck(source, analysis, outline, slides, generator, "review-model")

    assert not result.passed
    assert result.issues[0].category == "unsupported_claim"


def test_review_rejects_empty_prompt_without_calling_model():
    analysis, outline, slides = _inputs()
    generator = FakeReviewGenerator([])
    with pytest.raises(ValueError, match="must not be empty"):
        review_deck(" ", analysis, outline, slides, generator, "review-model")
    assert generator.calls == []
