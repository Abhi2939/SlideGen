"""Explicit manual asset/visual check, separate from offline automated tests."""

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.assets.resolve import prepare_assets  # noqa: E402
from app.config import Settings  # noqa: E402
from app.core.deck_schema import Deck  # noqa: E402
from app.render.builder import build_deck  # noqa: E402


def main():
    Settings.from_env()
    folder = Path("outputs/qa/client-ready")
    folder.mkdir(parents=True, exist_ok=True)
    fixture = json.loads(
        Path("tests/fixtures/decks/reference_layouts.json").read_text(encoding="utf-8")
    )
    source = "Supplied reference names: " + ", ".join(
        name
        for slide in fixture["slides"]
        if slide["layout"] == "quadrants"
        for quadrant in slide["quadrants"]
        for name in quadrant["names"]
    )
    deck = prepare_assets(Deck.model_validate(fixture), source)
    path = build_deck(deck, folder / "reference-assets.pptx", strict=True)
    (folder / "asset-sources.json").write_text(
        json.dumps([a.model_dump() for a in deck.assets], indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "path": str(path),
                "assets": len(deck.assets),
                "roles": sorted({a.role for a in deck.assets}),
            }
        )
    )
    flow = {
        "layout": "flow_steps",
        "title": "Dense eight-step workflow",
        "subtitle": "Offline stress fixture, not company evidence",
        "steps": [
            {
                "title": "Review information and confirm your preferences",
                "detail": "Consider the available choices and record the information needed to continue. Confirm the next step with the team.",
            }
            for _ in range(8)
        ],
    }
    fixture["slides"][3] = flow
    stress = prepare_assets(Deck.model_validate(fixture), source)
    print(build_deck(stress, folder / "dense-assets.pptx", strict=True))


if __name__ == "__main__":
    main()
