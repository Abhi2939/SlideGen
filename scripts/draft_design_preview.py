"""Export the source-only Blush design fixture without making provider requests."""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.deck_schema import Deck  # noqa: E402
from app.qa.draft import build_draft  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/qa/design-refresh"))
    parser.add_argument(
        "--deck", type=Path, default=Path("tests/fixtures/decks/blush_reference_draft.json")
    )
    args = parser.parse_args()
    deck = Deck.model_validate_json(args.deck.read_text(encoding="utf-8"))
    print(build_draft(deck, args.output_dir / "blush-redesigned.pptx"))
    print("Source-only authored design preview; no live content generation or AI visual scoring.")


if __name__ == "__main__":
    main()
