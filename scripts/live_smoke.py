"""Explicitly invoked, sequential Groq smoke run. Tests inject fake generation."""

import argparse
import json
from pathlib import Path
import sys
import time

# Support both `python scripts/live_smoke.py` and `python -m scripts.live_smoke`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.core.errors import DeckReviewError, ProviderLimitError  # noqa: E402
from app.pipeline.orchestrator import generate_deck  # noqa: E402
from app.render.builder import build_deck  # noqa: E402


PROMPTS = (
    (
        "acme-pay",
        "Acme Pay: fintech for SMEs. Raising Rs. 14 Cr. Use of funds: Contingency 85.30%, Other indirect 6.94%, Team 3.02%, Marketing 2.57%, Direct 1.95%, Capex 0.22%. Steps: signs up, verifies KYC, gets funded. Include market, how it works, competitive landscape.",
    ),
    ("product-launch", "Create a 6-slide product launch presentation for small business owners"),
    (
        "blush",
        "My startup makes custom foundation, lipstick, and skincare products matched to skin tone, undertone, and style. Make a short presentation.",
    ),
    ("dogs", "Make a deck about dogs"),
    (
        "seed-investor",
        "Create an 8-slide investor pitch for a B2B SaaS startup that automates invoice reconciliation for accounting firms. Audience: seed investors.",
    ),
)


def run(settings, output_dir, *, generate=generate_deck, build=build_deck):
    if not settings.api_key:
        print("GROQ_API_KEY is required. No prompts completed.")
        return 1
    output_dir.mkdir(parents=True, exist_ok=True)
    completed = []
    try:
        for index, (name, prompt) in enumerate(PROMPTS, 1):
            counts = {}
            reviews = []
            print(f"Starting {index}/{len(PROMPTS)}: {name}", flush=True)
            try:
                deck = generate(
                    prompt,
                    settings=settings,
                    max_workers=1,
                    deadline=time.monotonic() + 900,
                    usage_callback=counts.update,
                    review_callback=reviews.append,
                )
                build(deck, output_dir / f"{index:02d}-{name}.pptx", strict=True)
            except Exception as exc:
                print(f"Stopped at {name}.")
                print("Token usage (reported calls only): " + json.dumps(counts))
                print("Review findings: " + json.dumps(reviews))
                if isinstance(exc, ProviderLimitError):
                    print(str(exc))
                elif isinstance(exc, DeckReviewError):
                    print("Final review: " + exc.review.model_dump_json())
                else:
                    # Provider messages/tracebacks can contain private request data.
                    print("Generation or rendering failed; inspect local job logs.")
                return 1
            completed.append(name)
            print(
                f"Completed {name}: {len(deck.slides)} slides; layouts: "
                + ", ".join(slide.layout for slide in deck.slides)
            )
            print("Token usage (reported calls only): " + json.dumps(counts))
            print("Review findings: " + json.dumps(reviews))
        return 0
    except KeyboardInterrupt:
        print("Stopped by owner.")
        return 130
    finally:
        print("Prompts completed: " + (", ".join(completed) or "none"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/live-smoke"))
    args = parser.parse_args()
    return run(Settings.from_env(), args.output_dir)


if __name__ == "__main__":
    raise SystemExit(main())
