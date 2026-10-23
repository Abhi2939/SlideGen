"""Explicit live check for the owner's full launch/growth prompt."""

import json
import argparse
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.core.errors import DeckReviewError, ProviderLimitError, SlideWriteError  # noqa: E402
from app.pipeline.orchestrator import generate_deck  # noqa: E402
from app.render.builder import build_deck  # noqa: E402

PROMPT = (
    "Blush is a custom beauty brand offering foundation, lipstick, and skincare blended "
    "exclusively for your skin tone, undertone, and style, so you never get the wrong shade. "
    "Our launch will focus on a shade-matching quiz, waitlist, and creator-led demos, "
    "followed by quarterly growth through UGC, subscriptions, new product lines, pop-ups, "
    "and festive scaling."
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/live-smoke"))
    args = parser.parse_args()
    settings = Settings.from_env()
    if not settings.api_key:
        print("No configured Groq key; full Blush check skipped.")
        return 1
    counts, reviews = {}, []
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    try:
        deck = generate_deck(
            PROMPT,
            settings=settings,
            deadline=time.monotonic() + 900,
            usage_callback=counts.update,
            review_callback=reviews.append,
            progress_callback=lambda stage: print(stage, flush=True),
        )
        build_deck(deck, output / "full-blush.pptx", strict=True)
        (output / "full-blush.json").write_text(deck.model_dump_json(indent=2), encoding="utf-8")
        print(
            json.dumps(
                {
                    "result": "completed",
                    "slides": len(deck.slides),
                    "layouts": [s.layout for s in deck.slides],
                }
            )
        )
        return 0
    except Exception as exc:
        print(json.dumps({"result": "failed", "error_type": type(exc).__name__}))
        if isinstance(exc, ProviderLimitError):
            print(json.dumps(exc.diagnostics))
        elif isinstance(exc, DeckReviewError):
            print("Final review: " + exc.review.model_dump_json())
        else:
            if isinstance(exc, SlideWriteError):
                print(json.dumps({"reason": exc.reason, "slide_index": exc.slide_index}))
            cause = exc
            while cause is not None:
                if type(getattr(cause, "status_code", None)) is int:
                    print(json.dumps({"http_status": cause.status_code}))
                cause = cause.__cause__
        return 1
    finally:
        (output / "full-blush-check.json").write_text(
            json.dumps({"tokens": counts, "reviews": reviews}, indent=2), encoding="utf-8"
        )
        print("Token usage: " + json.dumps(counts))


if __name__ == "__main__":
    raise SystemExit(main())
