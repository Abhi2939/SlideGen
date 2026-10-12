from threading import Lock
from time import sleep

import pytest

from app.core.deck_schema import BulletsSlide, ClosingSlide, TitleSlide
from app.pipeline.models import AnalysisOutput, OutlineOutput
from app.pipeline.write_slides import write_slides


class FakeWriter:
    def __init__(self):
        self.calls = []
        self.lock = Lock()
        self.active = 0
        self.max_active = 0
        self.bullet_calls = 0

    def generate(self, output_model, **kwargs):
        with self.lock:
            self.calls.append((output_model, kwargs))
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            if output_model is BulletsSlide:
                self.bullet_calls += 1
                bullet_call = self.bullet_calls
            else:
                bullet_call = 0
        sleep(0.03)
        try:
            if output_model is TitleSlide:
                return TitleSlide(layout="title", title="Launch plan", subtitle="")
            if output_model is BulletsSlide:
                text = "Launch is June 1" if bullet_call > 1 else "The launch is soon"
                return BulletsSlide(
                    layout="bullets",
                    title="Launch date",
                    bullets=[{"text": text}, {"text": "Prepare the team"}],
                )
            if output_model is ClosingSlide:
                return ClosingSlide(layout="closing", title="Next steps")
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
    assert generator.bullet_calls == 2
    assert all(call[1]["stage"] == "write" for call in generator.calls)
    assert generator.max_active <= 2


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
