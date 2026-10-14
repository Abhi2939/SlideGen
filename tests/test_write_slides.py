from threading import Lock
from time import sleep

import pytest

from app.core.deck_schema import BulletsSlide, ClosingSlide, TitleSlide
from app.pipeline.grounding import unsupported_claim_categories
from app.pipeline.models import AnalysisOutput, OutlineOutput, RequiredFact
from app.pipeline.write_slides import (
    rewrite_slides,
    write_slides,
)


class FakeWriter:
    def __init__(
        self,
        *,
        invent_contact_once=False,
        invent_offer_once=False,
        repeat_unassigned_fact_once=False,
        duplicate_bullet_once=False,
    ):
        self.calls = []
        self.lock = Lock()
        self.active = 0
        self.max_active = 0
        self.bullet_calls = 0
        self.closing_calls = 0
        self.invent_contact_once = invent_contact_once
        self.invent_offer_once = invent_offer_once
        self.repeat_unassigned_fact_once = repeat_unassigned_fact_once
        self.duplicate_bullet_once = duplicate_bullet_once

    def generate(self, output_model, **kwargs):
        with self.lock:
            self.calls.append((output_model, kwargs))
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            if output_model is BulletsSlide:
                self.bullet_calls += 1
                bullet_call = self.bullet_calls
                invented_offer = self.invent_offer_once and bullet_call == 1
                repeated_fact = self.repeat_unassigned_fact_once and bullet_call == 1
                duplicate_bullet = self.duplicate_bullet_once and bullet_call == 1
            else:
                bullet_call = 0
            if output_model is ClosingSlide:
                self.closing_calls += 1
                contact = (
                    "info@example.com"
                    if self.invent_contact_once and self.closing_calls == 1
                    else None
                )
        sleep(0.03)
        try:
            if output_model is TitleSlide:
                return TitleSlide(layout="title", title="Launch plan", subtitle="")
            if output_model is BulletsSlide:
                text = "Launch is June 1" if bullet_call > 1 else "The launch is soon"
                second_bullet = "Prepare the team"
                if invented_offer:
                    second_bullet = "Visit our website for a free sample kit"
                elif repeated_fact:
                    second_bullet = "Makeup made only for YOU."
                elif duplicate_bullet:
                    second_bullet = text
                return BulletsSlide(
                    layout="bullets",
                    title="Launch date",
                    bullets=[{"text": text}, {"text": second_bullet}],
                )
            if output_model is ClosingSlide:
                return ClosingSlide(layout="closing", title="Next steps", contact=contact)
            raise AssertionError(f"unexpected output model: {output_model}")
        finally:
            with self.lock:
                self.active -= 1


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
    return analysis, outline


def test_write_slides_preserves_order_and_retries_missing_verbatim_fact():
    analysis, outline = _inputs()
    generator = FakeWriter()

    result = write_slides("Make a launch deck", analysis, outline, generator, "test-model", max_workers=2)

    assert [slide.title for slide in result] == ["Launch plan", "Launch date", "Next steps"]
    assert [slide.layout for slide in result] == ["title", "bullets", "closing"]
    assert generator.bullet_calls == 1
    assert any(bullet.text == "Launch is June 1" for bullet in result[1].bullets)
    assert all(call[1]["stage"] == "write" for call in generator.calls)
    assert all("Do not add claims about performance" in call[1]["system_prompt"] for call in generator.calls)
    assert all("repeat metadata labels such as 'Startup name'" in call[1]["system_prompt"] for call in generator.calls)
    assert all("Make each bullet" in call[1]["system_prompt"] for call in generator.calls)
    assert all("organizational guidance, not a source of facts" in call[1]["system_prompt"] for call in generator.calls)
    assert all("does not establish in-house blending" in call[1]["system_prompt"] for call in generator.calls)
    assert all(call[1]["max_tokens"] == 1600 for call in generator.calls)
    assert generator.max_active <= 2


def test_write_slides_defaults_to_single_worker():
    analysis, outline = _inputs()
    generator = FakeWriter()

    write_slides("Make a launch deck", analysis, outline, generator, "test-model")

    assert generator.max_active == 1


def test_title_slide_includes_assigned_verbatim_fact_in_subtitle():
    analysis, outline = _inputs()
    plans = list(outline.slides)
    plans[0] = plans[0].model_copy(update={"required_fact_ids": [0]})
    outline = outline.model_copy(update={"slides": plans})
    generator = FakeWriter()

    result = write_slides("Make a launch deck", analysis, outline, generator, "test-model")

    assert result[0].subtitle == "Launch is June 1"


def test_write_slides_retries_when_verbatim_fact_is_repeated_on_wrong_slide():
    analysis, outline = _inputs()
    analysis = analysis.model_copy(
        update={
            "required_facts": [
                *analysis.required_facts,
                RequiredFact(id=1, text="Makeup made only for YOU.", verbatim=True),
            ]
        }
    )
    plans = list(outline.slides)
    plans[0] = plans[0].model_copy(update={"required_fact_ids": [1]})
    outline = outline.model_copy(update={"slides": plans})
    generator = FakeWriter(repeat_unassigned_fact_once=True)

    result = write_slides(
        "Make a launch deck. Makeup made only for YOU.",
        analysis,
        outline,
        generator,
        "test-model",
    )

    assert generator.bullet_calls == 2
    assert all(
        "Makeup made only for YOU." not in bullet.text
        for bullet in result[1].bullets
    )
    bullet_calls = [kwargs for model, kwargs in generator.calls if model is BulletsSlide]
    assert "repeated verbatim facts assigned to other slides" in bullet_calls[1]["user_prompt"]


def test_write_slides_retries_duplicate_bullet_points():
    analysis, outline = _inputs()
    generator = FakeWriter(duplicate_bullet_once=True)

    result = write_slides("Make a launch deck", analysis, outline, generator, "test-model")

    assert generator.bullet_calls == 2
    assert len({bullet.text for bullet in result[1].bullets}) == 2
    bullet_calls = [kwargs for model, kwargs in generator.calls if model is BulletsSlide]
    assert "contains duplicate bullet points" in bullet_calls[1]["user_prompt"]


@pytest.mark.parametrize(
    "kwargs,match",
    [({"max_workers": 0}, "max_workers"), ({"semantic_attempts": 0}, "semantic_attempts")],
)
def test_write_slides_rejects_invalid_limits(kwargs, match):
    analysis, outline = _inputs()
    generator = FakeWriter()
    with pytest.raises(ValueError, match=match):
        write_slides("Make a launch deck", analysis, outline, generator, "test-model", **kwargs)
    assert generator.calls == []


def test_write_slides_rejects_empty_prompt():
    analysis, outline = _inputs()
    generator = FakeWriter()
    with pytest.raises(ValueError, match="must not be empty"):
        write_slides("  ", analysis, outline, generator, "test-model")
    assert generator.calls == []


def test_rewrite_slides_changes_only_requested_indices():
    analysis, outline = _inputs()
    current = [
        TitleSlide(layout="title", title="Launch plan"),
        BulletsSlide(
            layout="bullets",
            title="Launch date",
            bullets=[{"text": "Old wording"}, {"text": "Prepare the team"}],
        ),
        ClosingSlide(layout="closing", title="Next steps"),
    ]
    generator = FakeWriter()

    result = rewrite_slides(
        "Make a launch deck", analysis, outline, current, [1], generator, "test-model"
    )

    assert result[0] is current[0]
    assert result[2] is current[2]
    assert result[1] is not current[1]
    assert any(bullet.text == "Launch is June 1" for bullet in result[1].bullets)


def test_write_slides_rejects_invented_contact_details_and_retries():
    analysis, outline = _inputs()
    generator = FakeWriter(invent_contact_once=True)

    result = write_slides(
        "Make a launch deck", analysis, outline, generator, "test-model", max_workers=2
    )

    assert result[2].contact is None
    assert generator.closing_calls == 2
    closing_calls = [kwargs for model, kwargs in generator.calls if model is ClosingSlide]
    assert "contact information not supplied" in closing_calls[1]["user_prompt"]


def test_write_slides_rejects_unsupported_site_and_sample_offer_then_retries():
    analysis, outline = _inputs()
    generator = FakeWriter(invent_offer_once=True)

    result = write_slides("Make a launch deck", analysis, outline, generator, "test-model")

    assert result[1].bullets[1].text == "Prepare the team"
    bullet_calls = [kwargs for model, kwargs in generator.calls if model is BulletsSlide]
    assert len(bullet_calls) == 2
    assert "website or sample offers" in bullet_calls[1]["user_prompt"]


def test_unsupported_claim_categories_only_allow_source_supported_categories():
    claims = (
        "AI shade analysis uses an algorithm; we produce it in-house and ship it. "
        "Visit our website for a free sample kit after we assess your skin. "
        "Products are blended and packaged; try our personalized line today. "
        "Customers frequently choose a shade that does not fit. "
        "Standard options often fail to align, and finding a match is difficult."
    )

    categories = unsupported_claim_categories(claims, "Custom-blended makeup for your style.")

    assert categories == [
        "AI or algorithm claims",
        "assessment or diagnostic processes",
        "manufacturing or production processes",
        "shipping or delivery claims",
        "website or sample offers",
        "availability or purchase claims",
        "shade selection processes",
        "unsupported market or consumer behavior",
    ]


def test_unsupported_claim_guard_allows_categories_present_in_source():
    source = (
        "We use AI analysis and an algorithm, assess your skin, blend in-house, "
        "ship orders, and offer a free sample kit through our website."
    )

    assert unsupported_claim_categories(source, source) == []


def test_rewrite_slides_rejects_out_of_range_index_without_model_call():
    analysis, outline = _inputs()
    current = [
        TitleSlide(layout="title", title="Launch plan"),
        BulletsSlide(
            layout="bullets",
            title="Launch date",
            bullets=[{"text": "Launch is June 1"}, {"text": "Prepare the team"}],
        ),
        ClosingSlide(layout="closing", title="Next steps"),
    ]
    generator = FakeWriter()

    with pytest.raises(ValueError, match="out of range"):
        rewrite_slides(
            "Make a launch deck", analysis, outline, current, [3], generator, "test-model"
        )
    assert generator.calls == []
