"""Application-level errors shared by the API and generation pipeline."""


class SlideGenError(Exception):
    """Base class for expected SlideGen failures."""


class LLMProviderError(SlideGenError):
    """An LLM provider could not return a completion."""


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
