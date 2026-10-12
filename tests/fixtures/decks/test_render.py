import json
from pathlib import Path

import pytest
from pptx import Presentation

from app.core.deck_schema import Deck
from app.core.deck_schema import LAYOUTS
from app.render.builder import build_deck
from app.render.layout import LAYOUT_RENDERERS

FIXTURES = Path(__file__).parent


@pytest.mark.parametrize("path", sorted(FIXTURES.glob("*.json")), ids=lambda p: p.stem)
def test_fixture_renders_every_slide(path, tmp_path):
    deck = Deck.model_validate(json.loads(path.read_text(encoding="utf-8")))
    out = build_deck(deck, tmp_path / "out.pptx")
    assert len(Presentation(str(out)).slides) == len(deck.slides)


def test_each_schema_layout_has_a_renderer():
    assert set(LAYOUT_RENDERERS) == set(LAYOUTS)


def test_fixture_uses_no_placeholder_layouts(tmp_path):
    path = FIXTURES / "all_layouts.json"
    deck = Deck.model_validate(json.loads(path.read_text(encoding="utf-8")))
    out = build_deck(deck, tmp_path / "all-layouts.pptx", strict=True)
    presentation = Presentation(str(out))
    slide_text = [
        shape.text
        for slide in presentation.slides
        for shape in slide.shapes
        if shape.has_text_frame
    ]
    assert not any("layout not implemented yet" in text for text in slide_text)
