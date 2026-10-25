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
        kind = self.diagnostics.get("limit_type")
        if kind in {"requests_per_day", "tokens_per_day"}:
            reset = self.diagnostics.get("reset_at")
            message = f"The AI provider's daily {kind.split('_')[0]} limit was reached. " + (
                f"Resets at {reset}." if reset else "Reset time was not supplied by the provider."
            )
        else:
            message = (
                "The AI provider's temporary rate limit is still busy. Please try again shortly."
            )
        super().__init__(
            message,
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


class OutlineSourceError(SlideGenError, ValueError):
    """An outline claimed evidence that cannot be found in the supplied brief."""

    stage = "outline"
    reason = "outline_fact_not_in_source"

    def __init__(self, fact_ids: list[int]):
        self.fact_ids = fact_ids
        super().__init__("outline facts must be verbatim source evidence")


class SlideWriteError(SlideGenError):
    """A single planned slide could not be generated or validated."""

    def __init__(self, slide_index: int, title: str, detail: str, *, stage: str = "write"):
        self.slide_index = slide_index
        self.stage = stage
        self.title = title
        self.detail = detail
        self.reason = next(
            (
                code
                for marker, code in (
                    ("copy changed planned archetype", "layout_mismatch"),
                    ("incorrect archetype item count", "item_count_mismatch"),
                    ("region word budget", "copy_too_long"),
                    ("Cover tagline", "cover_tagline_too_long"),
                    ("illustrative targets need", "missing_illustrative_disclosure"),
                    ("illustrative values only", "invalid_illustrative_slide"),
                    ("unprovided numeric claim", "unprovided_numeric_data"),
                    ("unsupported hard company claim", "unsupported_claim"),
                    ("unsupported product or operational claim", "unsupported_claim"),
                    ("unprovided product specification", "unsupported_claim"),
                    ("unprovided contact", "unsupported_contact"),
                    ("Roadmap must have", "roadmap_activity_mismatch"),
                    ("quarters require", "unprovided_quarters"),
                    ("quarter assignments", "invalid_quarter_sequence"),
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
