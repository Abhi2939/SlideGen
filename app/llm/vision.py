"""Multimodal review of rendered pixels, using the same safe provider adapter."""

import base64
import json
import os
import time
from io import BytesIO

from pydantic import BaseModel, Field
from PIL import Image

from app.config import Settings
from app.core.errors import StructuredOutputError, LLMProviderError
from app.llm.factory import create_llm_client
from app.llm.structured import StructuredGenerator


class VisionFix(BaseModel):
    action: str = Field(max_length=40)
    detail: str = Field(max_length=300)


class VisionGrade(BaseModel):
    observed_title: str = Field(max_length=150)
    composition: str = Field(max_length=500)
    score: float = Field(ge=0, le=10)
    hierarchy: float = Field(ge=0, le=10)
    whitespace_balance: float = Field(ge=0, le=10)
    alignment: float = Field(ge=0, le=10)
    legibility: float = Field(ge=0, le=10)
    visual_interest: float = Field(ge=0, le=10)
    on_brand_palette: float = Field(ge=0, le=10)
    fixes: list[VisionFix] = Field(default_factory=list, max_length=8)


class GroqVisionReviewer:
    def __init__(self, *, settings=None, client=None, deadline=None, progress_callback=None):
        settings = settings or Settings.from_env()
        self.generator = StructuredGenerator(client or create_llm_client(settings))
        self.generator.deadline = deadline
        if progress_callback:
            self.generator.on_provider_wait = lambda stage, seconds: progress_callback(
                f"Waiting for AI rate limit (~{seconds}s)..." if seconds else "Reviewing slide design"
            )
        self.model = os.getenv("SLIDEGEN_VISION_MODEL", "qwen/qwen3.8-27b")

    def __call__(self, png, *, theme, slide_index):
        # Groq's vision preprocessing pads wide inputs to a square. Qwen can
        # mistake that padding for blank slide space. Normalize the entire slide
        # to a square, preserving normalized positions, before multimodal review.
        with Image.open(png) as source:
            original_size = source.size
            normalized = source.convert("RGB").resize((1000, 1000), Image.Resampling.LANCZOS)
            encoded = BytesIO()
            normalized.save(encoded, format="JPEG", quality=92)
            image = encoded.getvalue()
        if len(image) > 4 * 1024 * 1024:
            raise ValueError("rendered review image exceeds the request budget")
        uri = "data:image/jpeg;base64," + base64.b64encode(image).decode("ascii")
        return self._review(
            VisionGrade,
            stage="review",
            model=self.model,
            system_prompt="You are an independent presentation visual reviewer. First read the visible main title and describe the actual composition in one sentence, then score hierarchy, whitespace balance, alignment, legibility, visual interest and on-brand palette 0-10. Overall score 8+ means ready for a client visual review. Penalize clipping, text collisions, tiny text, large unbalanced blank areas, empty cards, weak imagery and internal notes. A small repeated brand wordmark and page number in the footer are expected and are not duplicate main content. Intentional negative space around a cover title is appropriate when a large photo balances it. Do not infer tiling or repeated slides just because a product photo contains similar objects. Every criticism must be grounded in visible pixels. Do not award a pass merely because content exists. Return actionable fixes: switch_archetype, split_slide, recrop_image, or rebalance_spacing. Never ask to invent factual content.",
            user_prompt=json.dumps(
                {
                    "slide_index": slide_index,
                    "theme": theme,
                    "original_canvas_pixels": original_size,
                    "review_input": "Entire slide normalized to a square to avoid backend padding. Judge positions and whitespace relative to the whole canvas. The actual deck is widescreen; ignore the temporary aspect stretch.",
                }
            ),
            image_urls=[uri],
            max_tokens=1600,
        )

    def _review(self, *args, **kwargs):
        for attempt in range(2):
            try:
                return self.generator.generate(*args, **kwargs)
            except StructuredOutputError as exc:
                cause = exc.__cause__
                if attempt or not isinstance(cause, LLMProviderError) or cause.status_code != 503:
                    raise
                if self.generator.deadline and time.monotonic() + 2 >= self.generator.deadline:
                    raise TimeoutError("visual review deadline exceeded") from None
                time.sleep(2)
