"""Offline design input, real cached assets, exported geometry lint; never an LLM pass."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.assets.resolve import prepare_assets  # noqa: E402
from app.core.deck_schema import Deck  # noqa: E402
from app.qa.lint import lint_pptx  # noqa: E402
from app.render.builder import build_deck  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/qa/gamma/archetypes"))
    args = parser.parse_args()
    deck = Deck.model_validate_json(Path("tests/fixtures/decks/blush_story.json").read_text())
    deck = prepare_assets(deck, "Blush custom beauty foundation lipstick skincare")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "blush.json").write_text(deck.model_dump_json(indent=2))
    path = build_deck(deck, args.output_dir / "blush.pptx", strict=True)
    report = lint_pptx(path)
    (args.output_dir / "lint.json").write_text(json.dumps(report, indent=2))
    print(
        json.dumps(
            {
                "scope": "offline authored fixture, not LLM generation",
                "slides": len(deck.slides),
                "assets": len(deck.assets),
                "lint_errors": len(report["errors"]),
                "codes": sorted({e["code"] for e in report["errors"]}),
                "path": str(path),
            }
        )
    )


if __name__ == "__main__":
    main()
