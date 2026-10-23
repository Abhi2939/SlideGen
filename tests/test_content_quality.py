import pytest

from app.pipeline.analyze import analyze_prompt
from app.pipeline.grounding import unsupported_claim_categories
from app.pipeline.models import AnalysisOutput, OutlineOutput
from app.pipeline.outline import create_outline


@pytest.mark.parametrize(
    "claim",
    [
        "Follow us on socials",
        "Confidence in every purchase",
        "Book a demo",
        "Join our community",
        "Shop now",
        "Guaranteed results",
    ],
)
def test_closing_claims_and_ctas_need_source_evidence(claim):
    assert unsupported_claim_categories(claim, "Blush offers custom foundation")
    assert unsupported_claim_categories(claim, claim) == []


def test_one_supplied_cta_does_not_authorize_other_ctas():
    assert unsupported_claim_categories("Follow us on socials", "Visit our website")


class Generator:
    def __init__(self, result):
        self.result = result

    def generate(self, output_model, **kwargs):
        return output_model.model_validate(self.result)


def test_sparse_educational_prompt_targets_six_substantive_slides():
    analysis = analyze_prompt(
        "Make a deck about dogs",
        Generator(
            {
                "deck_title": "Dogs",
                "deck_type": "education",
                "slide_count": 3,
            }
        ),
        "fake",
    )
    assert analysis.slide_count == 6


def test_outline_can_use_fewer_fuller_slides_without_losing_facts():
    analysis = AnalysisOutput(
        deck_title="Blush",
        deck_type="product",
        slide_count=8,
        required_facts=[
            {"id": 0, "text": "Blush makes custom foundation"},
            {"id": 1, "text": "Plans: UGC, subscriptions and pop-ups"},
        ],
    )
    result = {
        "deck_title": "Blush",
        "slides": [
            {"index": 0, "title": "Blush", "purpose": "Introduce", "layout": "title"},
            {
                "index": 1,
                "title": "Products",
                "purpose": "Describe",
                "layout": "bullets",
                "required_fact_ids": [0],
            },
            {
                "index": 2,
                "title": "Growth Roadmap",
                "purpose": "Future activities",
                "layout": "bullets",
                "required_fact_ids": [1],
            },
        ],
    }
    outline = create_outline(
        "Blush makes custom foundation. Growth through UGC, subscriptions and pop-ups.",
        analysis,
        Generator(result),
        "fake",
    )
    assert len(outline.slides) == 3
    assert outline.slides[2].layout == "flow_steps"
    assert set(fact for slide in outline.slides for fact in slide.required_fact_ids) == {0, 1}
    with pytest.raises(ValueError, match="analysis requested 8"):
        create_outline("Make an 8-slide Blush deck", analysis, Generator(result), "fake")


def test_fewer_slides_never_allows_dropping_source_facts():
    analysis = AnalysisOutput(
        deck_title="Blush",
        deck_type="product",
        slide_count=8,
        required_facts=[{"id": 0, "text": "Creator-led demos"}],
    )
    outline = OutlineOutput(
        deck_title="Blush",
        slides=[
            {
                "index": i,
                "title": "Blush" if i == 0 else "Products",
                "purpose": "Describe",
                "layout": "title" if i == 0 else "bullets",
            }
            for i in range(3)
        ],
    )
    with pytest.raises(ValueError, match="omits required fact"):
        create_outline("Blush creator-led demos", analysis, Generator(outline.model_dump()), "fake")
