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


def test_rendered_chart_shows_values_and_unit(tmp_path):
    path = FIXTURES / "all_layouts.json"
    deck = Deck.model_validate(json.loads(path.read_text(encoding="utf-8")))
    out = build_deck(deck, tmp_path / "charts.pptx", strict=True)
    presentation = Presentation(str(out))
    chart = next(
        shape.chart for slide in presentation.slides for shape in slide.shapes if shape.has_chart
    )
    assert chart.plots[0].has_data_labels
    assert chart.plots[0].data_labels.show_value


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


def test_reference_layout_fixture_renders_charts_tables_and_groups(tmp_path):
    deck = Deck.model_validate(
        json.loads((FIXTURES / "reference_layouts.json").read_text(encoding="utf-8"))
    )
    out = build_deck(deck, tmp_path / "reference-layouts.pptx", strict=True)
    presentation = Presentation(str(out))

    assert len(presentation.slides) == 6
    allocation = presentation.slides[1]
    assert any(shape.has_chart for shape in allocation.shapes)
    assert any("85.30%" in shape.text for shape in allocation.shapes if shape.has_text_frame)
    market = presentation.slides[2]
    assert any(shape.has_table for shape in market.shapes)
    text = "\n".join(
        shape.text
        for slide in presentation.slides
        for shape in slide.shapes
        if shape.has_text_frame
    )
    assert "How it works" in text
    assert "Mobile wallets" in text
    assert "Visa" in text
    for slide in presentation.slides:
        for shape in slide.shapes:
            assert shape.left >= 0
            assert shape.top >= 0
            assert shape.left + shape.width <= presentation.slide_width
            assert shape.top + shape.height <= presentation.slide_height
