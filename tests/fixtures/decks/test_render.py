import json
from pathlib import Path

import pytest
from pptx import Presentation

from app.core.deck_schema import Deck
from app.render.builder import build_deck

FIXTURES = Path(__file__).parent


@pytest.mark.parametrize("path", sorted(FIXTURES.glob("*.json")), ids=lambda p: p.stem)
def test_fixture_renders_every_slide(path, tmp_path):
    deck = Deck.model_validate(json.loads(path.read_text(encoding="utf-8")))
    out = build_deck(deck, tmp_path / "out.pptx")
    assert len(Presentation(str(out)).slides) == len(deck.slides)
