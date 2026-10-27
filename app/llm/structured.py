"""Validated structured-output requests with bounded stage-local retries."""

import json
import hashlib
import logging
import os
from threading import Event
from collections.abc import Mapping
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.core.errors import LLMProviderError, ProviderLimitError, StructuredOutputError
from app.llm.client import LLMClient
from app.llm.limits import safe_label
from app.llm.checkpoints import checkpoint_key, load_checkpoint, save_checkpoint, tag_checkpoint
from app.llm.schema import GPT_OSS_MODELS, recovery_format
from app.llm.usage import TokenUsage, usage_scope


T = TypeVar("T", bound=BaseModel)
log = logging.getLogger(__name__)

_SAFE_VALIDATION_TYPES = frozenset(
    {
        "missing",
        "extra_forbidden",
        "string_type",
        "string_too_short",
        "string_too_long",
        "string_pattern_mismatch",
        "list_type",
        "too_short",
        "too_long",
        "dict_type",
        "int_type",
        "int_parsing",
        "int_from_float",
        "float_type",
        "float_parsing",
        "bool_type",
        "bool_parsing",
        "literal_error",
        "enum",
        "value_error",
        "greater_than",
        "greater_than_equal",
        "less_than",
        "less_than_equal",
        "model_type",
        "union_tag_invalid",
        "union_tag_not_found",
        "none_required",
        "draft_point_limit",
        "draft_missing_source_facts",
        "draft_invalid_fact_reference",
        "draft_source_capacity",
        "draft_fact_not_in_source",
        "draft_fact_not_visible",
        "draft_unprovided_number",
        "draft_missing_number",
        "draft_unprovided_contact",
        "draft_unsupported_specific_claim",
        "draft_unsupported_claim",
    }
)


def validation_issues(exc, schema):
    """Log only schema-owned paths and fixed error codes, never values/messages."""
    if not isinstance(exc, ValidationError):
        return []
    fields = set()

    def visit(node):
        if isinstance(node, dict):
            properties = node.get("properties")
            if isinstance(properties, dict):
                fields.update(properties)
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(schema)
    issues = []
    for error in exc.errors(include_input=False, include_context=False, include_url=False)[:8]:
        path = (
            ".".join(
                str(part)
                if type(part) is int and 0 <= part <= 10000
                else part
                if isinstance(part, str) and part in fields
                else "*"
                for part in error["loc"]
            )
            or "$"
        )
        issues.append(
            {
                "path": path,
                "type": error["type"]
                if error["type"] in _SAFE_VALIDATION_TYPES
                else "validation_error",
            }
        )
    return issues


def _correction_detail(exc, fallback, schema):
    if not isinstance(exc, ValidationError):
        return fallback
    issues = validation_issues(exc, schema)
    errors = exc.errors(include_input=False, include_context=False, include_url=False)
    return "; ".join(f"{issue['path']}: {error['msg']}" for issue, error in zip(issues, errors))[
        :700
    ]


def _decode_json(content: str) -> Any:
    text = content.strip()
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]).strip()
    return json.loads(text)


def _is_provider_json_validation_error(exc: LLMProviderError) -> bool:
    """Return True only when the provider rejected its own generated JSON."""
    return exc.status_code == 400 and exc.code in {"json_validate_failed", "output_parse_failed"}


class StructuredGenerator:
    def __init__(
        self, client: LLMClient, *, max_attempts: int = 2, max_rate_limit_retries: int | None = None
    ):
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self.client = client
        self.max_attempts = max_attempts
        if max_rate_limit_retries is not None and max_rate_limit_retries < 0:
            raise ValueError("max_rate_limit_retries cannot be negative")
        self.max_rate_limit_retries = max_rate_limit_retries
        self.deadline = None
        self.on_provider_wait = None
        self.usage = TokenUsage()
        self.on_provider_limit = None
        self._limited = Event()

    def _complete(self, *, stage: str, **kwargs) -> str:
        return self.client.complete(
            stage=stage,
            deadline=self.deadline,
            on_wait=self.on_provider_wait,
            max_retries=self.max_rate_limit_retries,
            **kwargs,
        )

    def generate(
        self,
        output_model: type[T],
        *,
        stage: str,
        model: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        response_schema: Mapping[str, Any] | None = None,
        image_urls: list[str] | None = None,
        compact_corrections: bool = False,
    ) -> T:
        key = checkpoint_key(
            output_model,
            stage=stage,
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            max_tokens=os.getenv(f"LLM_{stage.upper()}_MAX_TOKENS") or max_tokens,
            response_schema=response_schema,
            image_urls=image_urls,
        )
        cached = load_checkpoint(key)
        if cached is not None:
            return tag_checkpoint(output_model.model_validate_json(cached), key)
        # A stage may narrow references to the current job's IDs. The output
        # model and stage validators remain authoritative after generation.
        schema = (
            response_schema if response_schema is not None else output_model.model_json_schema()
        )
        messages: list[Mapping[str, str]] = [
            {
                "role": "system",
                "content": (
                    f"{system_prompt}\nReturn only one JSON object matching this schema. "
                    f"Do not include markdown or commentary.\nJSON Schema:\n"
                    f"{json.dumps(schema, ensure_ascii=False, separators=(',', ':'))}"
                ),
            },
            {
                "role": "user",
                "content": user_prompt
                if not image_urls
                else [
                    {"type": "text", "text": user_prompt},
                    *[{"type": "image_url", "image_url": {"url": url}} for url in image_urls],
                ],
            },
        ]
        last_detail = "no response"
        task_messages = list(messages)
        request_format = recovery_format(model, output_model.__name__, schema)
        schema_id = hashlib.sha256(
            json.dumps(schema, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:16]

        for attempt in range(1, self.max_attempts + 1):
            if self._limited.is_set():
                raise ProviderLimitError()
            try:
                options = {}
                if model in GPT_OSS_MODELS:
                    # Single-slide copy needs room for final JSON within the
                    # smaller completion budget, including hidden reasoning.
                    options["reasoning_effort"] = (
                        "medium" if attempt == 1 and stage not in {"write", "repair"} else "low"
                    )
                with usage_scope(self.usage, stage):
                    raw = self._complete(
                        stage=stage,
                        messages=messages,
                        model=model,
                        temperature=temperature,
                        response_format=request_format,
                        max_tokens=max_tokens,
                        **options,
                    )
            except ProviderLimitError as exc:
                self._limited.set()
                log.warning(
                    "provider limit: stage=%s model=%s HTTP=429 details=%s",
                    safe_label(stage),
                    safe_label(model),
                    exc.diagnostics,
                )
                if self.on_provider_limit is not None:
                    self.on_provider_limit()
                raise
            except LLMProviderError as exc:
                last_detail = str(exc)
                log.warning(
                    "structured provider failure: stage=%s model=%s attempt=%d/%d "
                    "response_format=%s schema_id=%s HTTP=%s code=%s diagnostics=%s",
                    safe_label(stage),
                    safe_label(model),
                    attempt,
                    self.max_attempts,
                    request_format["type"],
                    schema_id,
                    exc.status_code,
                    safe_label(exc.code) if exc.code else None,
                    exc.diagnostics,
                )
                # Groq can reject a completion when its JSON-mode generation
                # is malformed, before returning any content to parse. Retry
                # only that specific response-format failure. In particular,
                # do not use JSON corrections to repeat quota failures.
                # Temporary minute limits have their own bounded wait above.
                if _is_provider_json_validation_error(exc):
                    if attempt == self.max_attempts:
                        raise StructuredOutputError(stage, attempt, last_detail) from exc
                    # A second native-schema request can repeat the provider's
                    # decoding failure. Use a different output mode while the
                    # complete Pydantic and semantic validators remain mandatory.
                    request_format = {"type": "json_object"}
                    messages = [
                        *messages,
                        {
                            "role": "user",
                            "content": (
                                "The provider could not produce valid JSON. Retry the same task "
                                "and return exactly one syntactically valid JSON object that "
                                "matches the required schema. Return the final JSON only, with no "
                                "analysis, reasoning, markdown, or trailing commas."
                            ),
                        },
                    ]
                    continue
                if exc.code != "empty_response":
                    raise StructuredOutputError(stage, attempt, last_detail) from exc
                if attempt == self.max_attempts:
                    break
                if compact_corrections:
                    detail = _correction_detail(exc, last_detail, schema)
                    messages = [
                        *task_messages,
                        {
                            "role": "user",
                            "content": (
                                "Regenerate the complete JSON for the same brief. Correct these errors: "
                                + detail
                            ),
                        },
                    ]
                    continue
                messages = [
                    *messages,
                    {
                        "role": "user",
                        "content": "The provider returned an empty response. Retry the same task and return only valid JSON.",
                    },
                ]
                continue

            try:
                result = output_model.model_validate(_decode_json(raw))
                if stage != "review" or getattr(result, "passed", True):
                    save_checkpoint(key, result.model_dump_json())
                return tag_checkpoint(result, key)
            except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
                # Corrections go back to the provider, but stored exception
                # details must never echo generated text or validator input.
                last_detail = (
                    "response was not valid JSON"
                    if isinstance(exc, json.JSONDecodeError)
                    else "response did not match the required schema"
                )
                log.warning(
                    "structured local validation failure: stage=%s model=%s attempt=%d/%d "
                    "response_format=%s schema_id=%s category=%s validation=%s",
                    safe_label(stage),
                    safe_label(model),
                    attempt,
                    self.max_attempts,
                    request_format["type"],
                    schema_id,
                    "json_syntax" if isinstance(exc, json.JSONDecodeError) else "schema",
                    json.dumps(validation_issues(exc, schema), separators=(",", ":")),
                )
                if attempt == self.max_attempts:
                    break
                if compact_corrections:
                    detail = _correction_detail(exc, last_detail, schema)
                    messages = [
                        *task_messages,
                        {
                            "role": "user",
                            "content": (
                                "Regenerate the complete JSON for the same brief. Correct these errors: "
                                + detail
                            ),
                        },
                    ]
                    continue
                messages = [
                    *messages,
                    {"role": "assistant", "content": raw},
                    {
                        "role": "user",
                        "content": (
                            "The previous JSON did not validate. Correct the response for the same task. "
                            "Validation error: " + str(exc)
                        ),
                    },
                ]

        raise StructuredOutputError(stage, self.max_attempts, last_detail)
