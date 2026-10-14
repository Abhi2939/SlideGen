"""Run all generation stages and return a validated deck model."""

import logging

from app.config import Settings
from app.core.deck_schema import Deck
from app.core.errors import DeckReviewError
from app.llm.client import LLMClient
from app.llm.factory import create_llm_client
from app.llm.structured import StructuredGenerator
from app.pipeline.analyze import MAX_PROMPT_CHARS, analyze_prompt
from app.pipeline.briefs import short_beauty_brief
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

    settings = settings or Settings.from_env()
    if theme == "default" and short_beauty_brief(clean_prompt) is not None:
        theme = "blush"
    models = {stage: settings.model_for(stage) for stage in ("analyze", "outline", "write", "review")}
    if generator is None:
        client: LLMClient = create_llm_client(settings)
        generator = StructuredGenerator(client, max_attempts=settings.structured_attempts)

    analysis = analyze_prompt(
        clean_prompt,
        generator,
        models["analyze"],
    )
    outline = create_outline(
        clean_prompt,
        analysis,
        generator,
        models["outline"],
        semantic_attempts=semantic_attempts,
    )
    slides = write_slides(
        clean_prompt,
        analysis,
        outline,
        generator,
        models["write"],
        max_workers=max_workers,
        semantic_attempts=semantic_attempts,
    )
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
    )
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
        "generated deck %r with %d slides after %d repair round(s)",
        deck.title,
        len(deck.slides),
        reviewed.repair_rounds,
    )
    return deck
