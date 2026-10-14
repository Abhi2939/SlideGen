import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.deck_schema import Deck

FIXTURES = Path(__file__).parent


def _title():
    return {"layout": "title", "title": "T"}


def _bullets():
    return {
        "layout": "bullets",
        "title": "B",
        "bullets": [{"text": "one"}, {"text": "two"}],
    }


def _closing():
    return {"layout": "closing", "title": "End"}


def _deck(*middle):
    return {"title": "D", "slides": [_title(), *middle, _closing()]}


@pytest.mark.parametrize(
    "path", sorted(FIXTURES.glob("*.json")), ids=lambda p: p.stem
)
def test_fixture_decks_validate(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    Deck.model_validate(data)


def test_minimal_deck_is_valid():
    Deck.model_validate(_deck(_bullets()))


def test_single_step_process_is_rejected():
    process = {
        "layout": "process",
        "title": "How",
        "steps": [{"title": "One", "description": "only step"}],
    }
    with pytest.raises(ValidationError):
        Deck.model_validate(_deck(process))


def test_first_slide_must_be_title():
    data = _deck(_bullets())
    data["slides"].reverse()
    with pytest.raises(ValidationError):
        Deck.model_validate(data)


def test_too_few_slides_rejected():
    with pytest.raises(ValidationError):
        Deck.model_validate({"title": "D", "slides": [_title(), _closing()]})


def test_title_over_limit_rejected():
    data = _deck(_bullets())
    data["slides"][1]["title"] = "x" * 81
    with pytest.raises(ValidationError):
        Deck.model_validate(data)


def test_unknown_layout_rejected():
    with pytest.raises(ValidationError):
        Deck.model_validate(_deck({"layout": "carousel", "title": "?"}))


def _chart(**overrides):
    chart = {
        "layout": "chart",
        "title": "C",
        "chart_type": "bar",
        "categories": ["a", "b"],
        "series": [{"name": "s", "values": [1, 2]}],
        "provenance": "user",
    }
    chart.update(overrides)
    return chart


def test_chart_value_count_must_match_categories():
    bad = _chart(series=[{"name": "s", "values": [1, 2, 3]}])
    with pytest.raises(ValidationError):
        Deck.model_validate(_deck(bad))


def test_pie_needs_exactly_one_series():
    bad = _chart(
        chart_type="pie",
        series=[
            {"name": "s1", "values": [1, 2]},
            {"name": "s2", "values": [3, 4]},
        ],
    )
    with pytest.raises(ValidationError):
        Deck.model_validate(_deck(bad))


@pytest.mark.parametrize(
    "values", [[-1, 2], [0, 0]]
)
def test_pie_values_must_be_non_negative_with_positive_total(values):
    with pytest.raises(ValidationError):
        Deck.model_validate(_deck(_chart(chart_type="pie", series=[{"name": "s", "values": values}])) )


def test_section_slide_requires_visible_subtitle():
    with pytest.raises(ValidationError):
        Deck.model_validate(_deck({"layout": "section", "title": "Overview"}))


def test_chart_requires_provenance():
    bad = _chart()
    del bad["provenance"]
    with pytest.raises(ValidationError):
        Deck.model_validate(_deck(bad))
