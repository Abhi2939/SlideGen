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


def test_blush_brand_theme_renders_editable_cosmetic_art(tmp_path):
    deck = Deck.model_validate(
        {
            "title": "Blush",
            "theme": "blush",
            "slides": [
                {"layout": "title", "title": "Blush", "subtitle": "Makeup made only for YOU."},
                {
                    "layout": "bullets",
                    "title": "The collection",
                    "bullets": [
                        {"text": "Custom-blended foundation"},
                        {"text": "Custom-blended lipstick and skincare"},
                    ],
                },
                {"layout": "closing", "title": "No more wrong shades"},
            ],
        }
    )

    out = build_deck(deck, tmp_path / "blush.pptx", strict=True)

    presentation = Presentation(str(out))
    assert len(presentation.slides) == 3
    for slide in (presentation.slides[0], presentation.slides[2]):
        texts = [shape.text for shape in slide.shapes if shape.has_text_frame]
        assert {"FOUNDATION", "LIPSTICK", "SKINCARE"} <= set(texts)
