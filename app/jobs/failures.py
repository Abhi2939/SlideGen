"""Persist only safe failure metadata, never exception messages or provider bodies."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from app.core.errors import (
    DeckReviewError,
    DraftContentError,
    LLMProviderError,
    OutlineSourceError,
    ProviderLimitError,
    SlideWriteError,
    StructuredOutputError,
)


USER_ERROR = "Deck generation failed. Please try again."


class JobFailure(BaseModel):
    code: str
    message: str
    stage: Literal["analyze", "outline", "write", "review", "repair", "render", "generate"]
    slide_index: int | None = None
    location: str | None = None
    provider_status: int | None = None
    provider_code: str | None = None


def failure_location(exc: Exception) -> str | None:
    app_root = Path(__file__).resolve().parents[1]
    location = None
    frame = exc.__traceback__
    while frame is not None:
        try:
            relative = Path(frame.tb_frame.f_code.co_filename).resolve().relative_to(app_root)
        except ValueError:
            pass
        else:
            location = f"{relative.as_posix()}:{frame.tb_lineno}"
        frame = frame.tb_next
    return location


def describe_failure(exc: Exception, *, stage: str = "generate") -> JobFailure:
    requested_stage = getattr(exc, "stage", None)
    if requested_stage in {"analyze", "outline", "write", "review", "repair"}:
        stage = requested_stage
    failure = JobFailure(
        code="generation_failed", message=USER_ERROR, stage=stage, location=failure_location(exc)
    )
    if isinstance(exc, ProviderLimitError):
        failure.code = "provider_limit"
        failure.message = str(exc)
    elif isinstance(exc, TimeoutError):
        failure.code = "generation_timeout"
        failure.message = "Generation exceeded its time limit before the presentation was ready."
    elif isinstance(exc, OutlineSourceError):
        failure.code = "outline_fact_not_in_source"
        failure.message = "The AI could not extract source facts that match your brief."
    elif isinstance(exc, SlideWriteError):
        # SlideWriteError derives this from a fixed application-owned mapping.
        failure.code = exc.reason
        failure.slide_index = exc.slide_index
        failure.message = f"The AI could not produce valid, source-supported content for slide {exc.slide_index + 1}."
        if exc.reason == "item_count_mismatch":
            failure.message = (
                f"The AI could not match the required layout for slide {exc.slide_index + 1}."
            )
    elif isinstance(exc, DraftContentError):
        failure.code = "draft_content_invalid"
        failure.message = "The AI could not match the brief within the draft's content limits. Try rephrasing the brief or requesting more slides."
    elif isinstance(exc, StructuredOutputError):
        failure.code = "invalid_ai_response"
        failure.message = "The AI returned an unreadable or incomplete response."
    elif isinstance(exc, DeckReviewError):
        failure.code = "source_review_failed"
        failure.stage = "review"
        failure.message = "The presentation did not pass its final content review."
    # Some older editorial checks raise ValueError with fixed application text.
    # Match only those exact constants; never persist the exception text itself.
    elif isinstance(exc, ValueError) and str(exc) in {
        "review returned invalid references",
        "story failed final source review",
        "missing source fact has no planned slide",
        "review failed without actionable findings",
    }:
        failure.code = "source_review_failed"
        failure.stage = "review"
        failure.message = "The presentation did not pass its final content review."
    elif getattr(exc, "reason", None) in {
        "draft_file_validation_failed",
        "draft_text_does_not_fit",
    }:
        failure.code = "draft_export_failed"
        failure.stage = "render"
        failure.message = "The draft could not be exported with readable text. Try a shorter brief or more slides."
    elif stage == "render":
        failure.code = "render_or_visual_check_failed"
        failure.message = (
            "The presentation could not be rendered or did not pass its visual checks."
        )
    cause = exc
    seen = set()
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        if isinstance(cause, LLMProviderError):
            status = cause.status_code
            if type(status) is int and 100 <= status <= 599:
                failure.provider_status = status
            if cause.code in {"output_parse_failed", "json_validate_failed", "empty_response"}:
                failure.provider_code = cause.code
            if (
                isinstance(exc, StructuredOutputError)
                and status == 400
                and cause.code in {"output_parse_failed", "json_validate_failed"}
            ):
                failure.code = "provider_output_rejected"
                failure.message = (
                    "The AI provider could not generate a valid presentation outline."
                    if failure.stage == "outline"
                    else "The AI provider could not generate valid content for this presentation."
                )
            if status in {401, 403}:
                failure.code = "provider_access_failed"
                failure.message = (
                    "The AI provider rejected the configured credentials or model access."
                )
        cause = cause.__cause__
    return failure
