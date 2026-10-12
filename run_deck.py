"""Generate a PowerPoint from a prompt using the configured LLM provider.

Example:
    python run_deck.py "Create a 5-slide product launch presentation"
    python run_deck.py "Create a launch plan" --output outputs/launch.pptx
"""

import argparse
from pathlib import Path

from app.pipeline.orchestrator import generate_deck
from app.render.builder import build_deck


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a PowerPoint deck from a prompt.")
    parser.add_argument("prompt", nargs="+", help="What the presentation should cover")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("outputs/generated.pptx"),
        help="Output .pptx path (default: outputs/generated.pptx)",
    )
    parser.add_argument("--theme", default="default", help="Deck theme name")
    args = parser.parse_args()

    deck = generate_deck(" ".join(args.prompt), theme=args.theme)
    output = build_deck(deck, args.output, strict=True)
    print(f"Created {output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
