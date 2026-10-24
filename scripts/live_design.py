"""Explicit live story check. Stops safely and records the actual outcome, never fakes a pass."""

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.errors import ProviderLimitError  # noqa: E402
from app.pipeline.orchestrator import generate_deck  # noqa: E402
from app.render.builder import build_deck  # noqa: E402
from app.qa.lint import lint_pptx  # noqa: E402
from app.config import Settings  # noqa: E402
from app.llm.factory import create_llm_client  # noqa: E402
from app.llm.structured import StructuredGenerator  # noqa: E402

BLUSH = "Blush is a custom beauty brand offering foundation, lipstick, and skincare blended exclusively for your skin tone, undertone, and style, so you never get the wrong shade. Our launch will focus on a shade-matching quiz, waitlist, and creator-led demos, followed by quarterly growth through UGC, subscriptions, new product lines, pop-ups, and festive scaling."
PROMPTS = {
    "blush": BLUSH,
    "fintech": "Acme Pay is a fintech service for SMEs that helps organize invoice payments. Create a pitch about the problem, the proposed service and a launch plan. Use explicit placeholders for unprovided hard facts.",
    "edtech": "LearnLoop is an edtech product for students and teachers. It combines lesson planning, practice activities and progress reflection. Create a product introduction and proposed launch plan.",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("topic", choices=PROMPTS)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse identical successful requests in this local QA run",
    )
    parser.add_argument(
        "--checked", action="store_true", help="Run the actual render/vision/repair gate"
    )
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    counts, reviews = {}, []
    settings = Settings.from_env()
    base = StructuredGenerator(
        create_llm_client(settings), max_attempts=settings.structured_attempts
    )

    class RecordingGenerator:
        def __init__(self):
            object.__setattr__(self, "counter", 0)
            object.__setattr__(self, "cache_hits", 0)

        def __getattr__(self, key):
            return getattr(base, key)

        def __setattr__(self, key, value):
            setattr(base, key, value)

        def generate(self, output_model, **kwargs):
            identity = hashlib.sha256(
                json.dumps(
                    {"schema": output_model.model_json_schema(), **kwargs}, sort_keys=True
                ).encode()
            ).hexdigest()
            cached = args.output_dir / "requests" / (identity + ".json")
            if args.resume and cached.is_file():
                object.__setattr__(self, "cache_hits", self.cache_hits + 1)
                return output_model.model_validate_json(cached.read_text(encoding="utf-8"))
            result = base.generate(output_model, **kwargs)
            cached.parent.mkdir(exist_ok=True)
            cached.write_text(result.model_dump_json(indent=2), encoding="utf-8")
            object.__setattr__(self, "counter", self.counter + 1)
            (
                args.output_dir
                / f"{self.counter:02d}-{kwargs['stage']}-{output_model.__name__}.json"
            ).write_text(result.model_dump_json(indent=2), encoding="utf-8")
            return result

    record = {
        "topic": args.topic,
        "scope": "real provider generation; identical successful requests may be reused"
        if args.resume
        else "fresh all-stage real provider generation",
        "result": "started",
    }
    recorder = RecordingGenerator()
    try:
        deck = generate_deck(
            PROMPTS[args.topic],
            settings=settings,
            generator=recorder,
            deadline=time.monotonic() + 900,
            usage_callback=counts.update,
            review_callback=reviews.append,
            progress_callback=lambda stage: print(stage, flush=True),
        )
        (args.output_dir / "deck.json").write_text(deck.model_dump_json(indent=2), encoding="utf-8")
        path = build_deck(deck, args.output_dir / "deck.pptx", strict=True)
        report = lint_pptx(path)
        (args.output_dir / "lint.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        record.update(
            result="saved",
            slides=len(deck.slides),
            theme=deck.theme,
            lint_passed=report["passed"],
            lint_errors=len(report["errors"]),
        )
        if args.checked:
            from app.qa.gate import build_checked

            build_checked(
                deck,
                args.output_dir / "approved.pptx",
                source_prompt=PROMPTS[args.topic],
                progress_callback=lambda stage: print(stage, flush=True),
            )
            record["visual_gate_passed"] = True
    except Exception as exc:
        record.update(result="failed", failure_type=type(exc).__name__)
        if isinstance(exc, ProviderLimitError):
            record["quota"] = exc.diagnostics
        elif isinstance(exc, ValueError):
            record["validation"] = str(exc)[:240]
        print("Generation stopped: " + type(exc).__name__, flush=True)
        return 1
    finally:
        record["provider_cache_hits"] = recorder.cache_hits
        record["provider_requests_executed"] = recorder.counter
        record["tokens"], record["reviews"] = counts, reviews
        (args.output_dir / "check.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
        print(json.dumps({k: v for k, v in record.items() if k != "reviews"}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
