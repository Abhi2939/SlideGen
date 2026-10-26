"""Generate a PowerPoint from a prompt using the configured LLM provider.

Example:
    python run_deck.py "Create a 5-slide product launch presentation"
    python run_deck.py "Create a launch plan" --output outputs/launch.pptx
"""

import argparse
from pathlib import Path
import sys

from app.core.errors import DeckReviewError, SlideWriteError
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
    parser.add_argument(
        "--reviewed",
        action="store_true",
        help="Use the slower editorial and visual-review pipeline",
    )
    args = parser.parse_args()

    try:
        deck = generate_deck(
            " ".join(args.prompt),
            theme=args.theme,
            design="editorial" if args.reviewed else "draft",
        )
    except DeckReviewError as error:
        print(f"Generation failed: {error}", file=sys.stderr)
        for issue in error.review.issues:
            if hasattr(issue, "slide_index"):
                print(f"- Slide {issue.slide_index + 1}: {issue.fix}", file=sys.stderr)
                continue
            slides = ", ".join(str(index + 1) for index in issue.slide_indices)
            location = f" on slide(s) {slides}" if slides else ""
            print(
                f"- {issue.severity} {issue.category}{location}: {issue.detail}",
                file=sys.stderr,
            )
        if error.review.missing_fact_ids:
            print(
                "- Missing required fact IDs: "
                + ", ".join(str(fact_id) for fact_id in error.review.missing_fact_ids),
                file=sys.stderr,
            )
        return 1
    except SlideWriteError as error:
        print(f"Generation failed: {error}", file=sys.stderr)
        return 1
    if deck.design_version == "draft-v1":
        from app.qa.draft import build_draft

        output = build_draft(deck, args.output)
    elif deck.design_version == "gamma-v1":
        from app.qa.gate import build_checked

        output = build_checked(deck, args.output, source_prompt=" ".join(args.prompt))
    else:
        output = build_deck(deck, args.output, strict=True)
    print(f"Created {output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
