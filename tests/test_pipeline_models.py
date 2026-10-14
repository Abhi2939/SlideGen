import pytest
from pydantic import ValidationError

from app.pipeline.models import (
    AnalysisOutput,
    OutlineOutput,
    ReviewIssue,
    ReviewOutput,
    validate_outline_facts,
)


def _analysis(**overrides):
    values = {
        "deck_title": "Plan",
        "deck_type": "overview",
        "slide_count": 3,
        "required_facts": [{"id": 0, "text": "Launch date is June 1", "verbatim": True}],
    }
    values.update(overrides)
    return AnalysisOutput.model_validate(values)


def _outline(**overrides):
    values = {
        "deck_title": "Plan",
        "slides": [
            {"index": 0, "title": "Plan", "purpose": "Introduce the deck", "layout": "title"},
            {
                "index": 1,
                "title": "Launch",
                "purpose": "Explain the launch timing",
                "layout": "bullets",
                "required_fact_ids": [0],
            },
            {"index": 2, "title": "Next steps", "purpose": "Close", "layout": "closing"},
        ],
    }
    values.update(overrides)
    return OutlineOutput.model_validate(values)


def test_outline_covers_all_required_facts():
    validate_outline_facts(_analysis(), _outline())


def test_outline_deck_title_must_match_analysis():
    outline = _outline(
        deck_title="Different plan",
        slides=[
            {"index": 0, "title": "Different plan", "purpose": "Introduce", "layout": "title"},
            {"index": 1, "title": "Launch", "purpose": "Explain timing", "layout": "bullets"},
            {"index": 2, "title": "Next steps", "purpose": "Close", "layout": "closing"},
        ],
    )
    with pytest.raises(ValueError, match="does not match"):
        validate_outline_facts(_analysis(), outline)


def test_title_slide_title_must_match_outline_title():
    with pytest.raises(ValidationError, match="title slide title must match"):
        _outline(
            slides=[
                {"index": 0, "title": "Different", "purpose": "Introduce", "layout": "title"},
                {"index": 1, "title": "Launch", "purpose": "Explain timing", "layout": "bullets"},
                {"index": 2, "title": "Next steps", "purpose": "Close", "layout": "closing"},
            ]
        )


def test_outline_rejects_unknown_fact_ids():
    outline = _outline(
        slides=[
            {"index": 0, "title": "Plan", "purpose": "Introduce", "layout": "title"},
            {
                "index": 1,
                "title": "Launch",
                "purpose": "Explain timing",
                "layout": "bullets",
                "required_fact_ids": [7],
            },
            {"index": 2, "title": "Next steps", "purpose": "Close", "layout": "closing"},
        ]
    )
    with pytest.raises(ValueError, match="unknown fact IDs"):
        validate_outline_facts(_analysis(), outline)


def test_outline_rejects_omitted_required_facts():
    outline = _outline(
        slides=[
            {"index": 0, "title": "Plan", "purpose": "Introduce", "layout": "title"},
            {"index": 1, "title": "Launch", "purpose": "Explain timing", "layout": "bullets"},
            {"index": 2, "title": "Next steps", "purpose": "Close", "layout": "closing"},
        ]
    )
    with pytest.raises(ValueError, match="omits required fact IDs"):
        validate_outline_facts(_analysis(), outline)


def test_outline_slide_indices_must_be_consecutive():
    with pytest.raises(ValidationError, match="consecutive"):
        _outline(
            slides=[
                {"index": 0, "title": "Plan", "purpose": "Introduce", "layout": "title"},
                {"index": 2, "title": "Launch", "purpose": "Explain timing", "layout": "bullets"},
                {"index": 3, "title": "Next steps", "purpose": "Close", "layout": "closing"},
            ]
        )


def test_failed_review_identifies_slides_to_regenerate():
    review = ReviewOutput.model_validate(
        {
            "passed": False,
            "issues": [
                {
                    "category": "overflow",
                    "severity": "high",
                    "detail": "Slide content is too long.",
                    "slide_indices": [1],
                    "repair_instruction": "Shorten the second slide.",
                }
            ],
            "regenerate_slide_indices": [1],
        }
    )
    assert review.regenerate_slide_indices == [1]


def test_passed_review_cannot_contain_findings():
    with pytest.raises(ValidationError, match="cannot include findings"):
        ReviewOutput(
            passed=True,
            issues=[ReviewIssue(category="duplicate", severity="low", detail="Repeated point")],
        )
