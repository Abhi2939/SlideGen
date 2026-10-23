"""Application-level errors shared by the API and generation pipeline."""


class SlideGenError(Exception):
    """Base class for expected SlideGen failures."""


class LLMProviderError(SlideGenError):
    """An LLM provider could not return a completion."""

    def __init__(self, message: str, *, status_code: int | None = None, code: str | None = None):
        self.status_code = status_code
        self.code = code
        super().__init__(message)


class ProviderLimitError(LLMProviderError):
    """A sanitized provider limit, with metadata for bounded minute-limit recovery."""

    def __init__(self, *, diagnostics: dict | None = None):
        self.diagnostics = diagnostics or {}
        super().__init__(
            "The AI provider's daily or rate limit was reached. Please try again later.",
            status_code=429,
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
        self.reason = next(
            (
                code
                for marker, code in (
                    ("generated title does not match", "title_mismatch"),
                    ("generated layout", "layout_mismatch"),
                    ("duplicate bullet", "duplicate_bullets"),
                    ("repeated verbatim facts", "repeated_fact"),
                    ("omitted verbatim fact", "missing_verbatim_fact"),
                    ("presentation instructions", "instruction_echo"),
                    ("numeric details", "unprovided_numeric_data"),
                    ("unsupported details", "unsupported_claim"),
                    ("contact information", "unsupported_contact"),
                    ("invalid structured output", "invalid_structured_output"),
                )
                if marker in detail
            ),
            "slide_validation_failed",
        )
        super().__init__(f"failed to write slide {slide_index + 1} ({title!r}): {detail}")


class DeckReviewError(SlideGenError):
    """A deck still has review findings after all repair rounds."""

    def __init__(self, review, repair_rounds: int):
        self.review = review
        self.repair_rounds = repair_rounds
        super().__init__(f"deck review did not pass after {repair_rounds} repair round(s)")
