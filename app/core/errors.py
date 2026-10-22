"""Application-level errors shared by the API and generation pipeline."""


class SlideGenError(Exception):
    """Base class for expected SlideGen failures."""


class LLMProviderError(SlideGenError):
    """An LLM provider could not return a completion."""


class ProviderLimitError(LLMProviderError):
    """A quota/rate limit that must not trigger generation retries."""

    def __init__(self):
        super().__init__(
            "The AI provider's daily or rate limit was reached. Please try again later."
        )


class StructuredOutputError(SlideGenError):
    """A model response remained invalid after the configured retries."""

    def __init__(self, stage: str, attempts: int, detail: str):
        self.stage = stage
        self.attempts = attempts
        self.detail = detail
        super().__init__(
            f"stage {stage!r} returned invalid structured output after "
            f"{attempts} attempt(s): {detail}"
        )


class SlideWriteError(SlideGenError):
    """A single planned slide could not be generated or validated."""

    def __init__(self, slide_index: int, title: str, detail: str):
        self.slide_index = slide_index
        self.title = title
        self.detail = detail
        super().__init__(f"failed to write slide {slide_index + 1} ({title!r}): {detail}")


class DeckReviewError(SlideGenError):
    """A deck still has review findings after all repair rounds."""

    def __init__(self, review, repair_rounds: int):
        self.review = review
        self.repair_rounds = repair_rounds
        super().__init__(f"deck review did not pass after {repair_rounds} repair round(s)")
