"""Run all generation stages and return a validated deck model."""

import logging
import time
from collections.abc import Callable

from app.config import Settings
from app.assets.resolve import prepare_assets
from app.core.deck_schema import Deck
from app.core.errors import DeckReviewError
from app.llm.client import LLMClient
from app.llm.factory import create_llm_client
from app.llm.structured import StructuredGenerator
from app.pipeline.analyze import MAX_PROMPT_CHARS, analyze_prompt
from app.pipeline.briefs import beauty_brand
from app.pipeline.outline import create_outline
from app.pipeline.review import review_and_repair_deck
from app.pipeline.write_slides import write_slides


log = logging.getLogger(__name__)


def generate_deck(
    prompt: str,
    *,
    settings: Settings | None = None,
    generator: StructuredGenerator | None = None,
    theme: str = "default",
    max_workers: int = 1,
    max_repair_rounds: int = 2,
    semantic_attempts: int = 2,
    deadline: float | None = None,
    progress_callback: Callable[[str], None] | None = None,
    usage_callback: Callable[[dict], None] | None = None,
    review_callback: Callable[[dict], None] | None = None,
    provider_limit_callback: Callable[[], None] | None = None,
) -> Deck:
    """Analyze, outline, write, review and validate one complete presentation."""
    clean_prompt = prompt.strip()
    if not clean_prompt:
        raise ValueError("prompt must not be empty")
    if len(clean_prompt) > MAX_PROMPT_CHARS:
        raise ValueError(f"prompt must be at most {MAX_PROMPT_CHARS} characters")
    if max_workers < 1:
        raise ValueError("max_workers must be at least 1")
    if max_repair_rounds < 0:
        raise ValueError("max_repair_rounds cannot be negative")
    if semantic_attempts < 1:
        raise ValueError("semantic_attempts must be at least 1")

    def announce(stage: str) -> None:
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError("deck generation exceeded its time limit")
        if progress_callback is not None:
            progress_callback(stage)

    settings = settings or Settings.from_env()
    if theme == "default" and beauty_brand(clean_prompt) is not None:
        theme = "blush"
    models = {
        stage: settings.model_for(stage) for stage in ("analyze", "outline", "write", "review")
    }
    if generator is None:
        client: LLMClient = create_llm_client(settings)
        generator = StructuredGenerator(client, max_attempts=settings.structured_attempts)

    def report_provider_wait(stage: str, seconds: int) -> None:
        stage_labels = {
            "analyze": "Analyzing request",
            "outline": "Planning slide sequence",
            "write": "Writing slide content",
            "review": "Reviewing slide content",
            "repair": "Revising slide content",
        }
        announce(
            f"Waiting {seconds}s for the AI provider's per-minute limit"
            if seconds
            else stage_labels.get(stage, "Generating presentation")
        )

    generator.deadline = deadline
    generator.on_provider_wait = report_provider_wait
    if provider_limit_callback is not None:
        generator.on_provider_limit = provider_limit_callback

    try:
        announce("Analyzing request")
        analysis = analyze_prompt(
            clean_prompt,
            generator,
            models["analyze"],
        )
        announce("Planning slide sequence")
        outline = create_outline(
            clean_prompt,
            analysis,
            generator,
            models["outline"],
            semantic_attempts=semantic_attempts,
        )
        if len(outline.slides) != analysis.slide_count:
            analysis = analysis.model_copy(update={"slide_count": len(outline.slides)})
        announce("Writing slide content")
        slides = write_slides(
            clean_prompt,
            analysis,
            outline,
            generator,
            models["write"],
            max_workers=max_workers,
            semantic_attempts=semantic_attempts,
        )
        announce("Reviewing slide content")
        reviewed = review_and_repair_deck(
            clean_prompt,
            analysis,
            outline,
            slides,
            generator,
            models["review"],
            models["write"],
            max_repair_rounds=max_repair_rounds,
            max_workers=max_workers,
            semantic_attempts=semantic_attempts,
            progress_callback=progress_callback,
            review_callback=review_callback,
        )
        announce("Validating presentation")
        if not reviewed.review.passed:
            raise DeckReviewError(reviewed.review, reviewed.repair_rounds)

        deck = Deck.model_validate(
            {
                "title": analysis.deck_title,
                "subtitle": outline.subtitle,
                "audience": analysis.audience,
                "tone": analysis.tone,
                "theme": theme,
                "slides": [slide.model_dump(mode="json") for slide in reviewed.slides],
            }
        )
        log.info(
            "generated deck with %d slides after %d repair round(s)",
            len(deck.slides),
            reviewed.repair_rounds,
        )
        announce("Resolving visual assets")
        return prepare_assets(deck, clean_prompt, deadline=deadline)
    finally:
        usage = getattr(generator, "usage", None)
        if usage is not None:
            counts = usage.snapshot()
            if usage_callback is not None:
                usage_callback(counts)
            else:
                log.info("pipeline token usage: %s", counts)
