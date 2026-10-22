import json
import traceback

import httpx

import pytest
from pydantic import BaseModel
from groq import BadRequestError

from app.core.errors import ProviderLimitError, StructuredOutputError
from app.llm.client import LLMClient
from app.llm.structured import StructuredGenerator


class Result(BaseModel):
    title: str


class FakeProvider:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def complete(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_structured_generator_validates_response():
    provider = FakeProvider(['{"title":"Ready"}'])
    result = StructuredGenerator(LLMClient(provider)).generate(
        Result,
        stage="outline",
        model="test-model",
        system_prompt="Return a title.",
        user_prompt="Make a title.",
    )
    assert result == Result(title="Ready")
    assert provider.calls[0][1]["response_format"] == {"type": "json_object"}


def test_request_specific_schema_is_sent_but_output_model_still_validates():
    schema = Result.model_json_schema()
    schema["properties"]["title"]["enum"] = ["Ready"]
    provider = FakeProvider(['{"wrong":"field"}', '{"title":"Ready"}'])

    result = StructuredGenerator(LLMClient(provider)).generate(
        Result,
        stage="review",
        model="test-model",
        system_prompt="Return a title.",
        user_prompt="Make a title.",
        response_schema=schema,
    )

    assert result.title == "Ready"
    assert len(provider.calls) == 2
    sent_schema = json.loads(provider.calls[0][0][0]["content"].split("JSON Schema:\n")[1])
    assert sent_schema == schema
    assert provider.calls[0][1]["response_format"] == {"type": "json_object"}
    assert "enum" not in Result.model_json_schema()["properties"]["title"]


def test_structured_generator_retries_invalid_json():
    provider = FakeProvider(["not json", '{"title":"Fixed"}'])
    result = StructuredGenerator(LLMClient(provider), max_attempts=2).generate(
        Result,
        stage="outline",
        model="test-model",
        system_prompt="Return a title.",
        user_prompt="Make a title.",
    )
    assert result.title == "Fixed"
    assert len(provider.calls) == 2
    assert provider.calls[1][0][-1]["role"] == "user"


def test_structured_generator_raises_after_validation_retries():
    provider = FakeProvider([json.dumps({"wrong": "field"}), "still not json"])
    generator = StructuredGenerator(LLMClient(provider), max_attempts=2)
    with pytest.raises(StructuredOutputError) as exc_info:
        generator.generate(
            Result,
            stage="write",
            model="test-model",
            system_prompt="Return a title.",
            user_prompt="Make a title.",
        )
    assert exc_info.value.stage == "write"
    assert exc_info.value.attempts == 2


def test_empty_structured_provider_response_is_retried():
    provider = FakeProvider(["", '{"title":"Recovered"}'])
    result = StructuredGenerator(LLMClient(provider), max_attempts=2).generate(
        Result,
        stage="review",
        model="test-model",
        system_prompt="Return a title.",
        user_prompt="Make a title.",
    )
    assert result.title == "Recovered"
    assert len(provider.calls) == 2


def test_provider_failure_is_not_retried_by_structured_output_loop():
    provider = FakeProvider(
        [RuntimeError("rate limit; retry after 2 seconds"), '{"title":"Ready"}']
    )
    generator = StructuredGenerator(LLMClient(provider), max_attempts=2)

    with pytest.raises(StructuredOutputError) as exc_info:
        generator.generate(
            Result,
            stage="write",
            model="test-model",
            system_prompt="Return a title.",
            user_prompt="Make a title.",
        )

    assert exc_info.value.attempts == 1
    assert exc_info.value.detail == "provider request failed"
    assert "rate limit" not in "".join(traceback.format_exception(exc_info.value))
    assert len(provider.calls) == 1


def parse_error(code="output_parse_failed", *, nested=True):
    error = {"code": code, "failed_generation": "PRIVATE_COMPLETION"}
    return BadRequestError(
        "PRIVATE_COMPLETION PRIVATE_CREDENTIAL",
        response=httpx.Response(
            400, request=httpx.Request("POST", "https://example.test/completions")
        ),
        body={"error": error} if nested else error,
    )


@pytest.mark.parametrize("code", ["output_parse_failed", "json_validate_failed"])
@pytest.mark.parametrize("nested", [True, False])
def test_provider_parse_error_recovers_without_reusing_failed_generation(code, nested):
    provider = FakeProvider([parse_error(code, nested=nested), '{"title":"Ready"}'])
    generator = StructuredGenerator(LLMClient(provider))

    result = generator.generate(
        Result,
        stage="analyze",
        model="openai/gpt-oss-20b",
        system_prompt="Return JSON.",
        user_prompt="Make a title.",
        max_tokens=1800,
    )

    assert result.title == "Ready"
    assert len(provider.calls) == 2
    assert provider.calls[0][1]["response_format"] == {"type": "json_object"}
    recovery = provider.calls[1][1]
    assert recovery["response_format"]["type"] == "json_schema"
    assert recovery["response_format"]["json_schema"]["strict"] is True
    assert recovery["max_tokens"] == 1800
    assert "PRIVATE_COMPLETION" not in json.dumps(provider.calls)
    assert "PRIVATE_CREDENTIAL" not in json.dumps(provider.calls)


def test_provider_parse_error_exhausts_configured_attempts_with_sanitized_traceback():
    provider = FakeProvider([parse_error(), parse_error(), '{"title":"Unused"}'])

    with pytest.raises(StructuredOutputError) as caught:
        StructuredGenerator(LLMClient(provider)).generate(
            Result,
            stage="analyze",
            model="openai/gpt-oss-20b",
            system_prompt="Return JSON.",
            user_prompt="Make a title.",
        )

    assert caught.value.attempts == 2
    assert len(provider.calls) == 2
    assert "output_parse_failed" in caught.value.detail
    formatted = "".join(traceback.format_exception(caught.value))
    assert "PRIVATE_COMPLETION" not in formatted
    assert "PRIVATE_CREDENTIAL" not in formatted
    assert "failed_generation" not in formatted


@pytest.mark.parametrize(
    "status, body",
    [
        (400, {"error": {"code": "invalid_api_key", "message": "PRIVATE_CREDENTIAL"}}),
        (400, {"error": {"code": ["output_parse_failed"]}}),
        (401, {"error": {"code": "output_parse_failed"}}),
        (500, {"error": {"code": "output_parse_failed"}}),
    ],
)
def test_other_provider_errors_are_sanitized_and_not_retried(status, body):
    class ProviderError(Exception):
        status_code = status

    error = ProviderError("PRIVATE_CREDENTIAL PRIVATE_COMPLETION")
    error.body = body
    provider = FakeProvider([error, '{"title":"Unused"}'])

    with pytest.raises(StructuredOutputError) as caught:
        StructuredGenerator(LLMClient(provider)).generate(
            Result,
            stage="analyze",
            model="openai/gpt-oss-20b",
            system_prompt="Return JSON.",
            user_prompt="Make a title.",
        )

    assert caught.value.attempts == 1
    assert len(provider.calls) == 1
    formatted = "".join(traceback.format_exception(caught.value))
    assert "PRIVATE_CREDENTIAL" not in formatted
    assert "PRIVATE_COMPLETION" not in formatted


def test_rate_limit_with_parse_code_is_never_retried():
    class RateLimit(Exception):
        status_code = 429
        body = {"error": {"code": "output_parse_failed"}}

    provider = FakeProvider([RateLimit("PRIVATE_CREDENTIAL"), '{"title":"Unused"}'])

    with pytest.raises(ProviderLimitError):
        StructuredGenerator(LLMClient(provider)).generate(
            Result,
            stage="analyze",
            model="openai/gpt-oss-20b",
            system_prompt="Return JSON.",
            user_prompt="Make a title.",
        )

    assert len(provider.calls) == 1


@pytest.mark.parametrize(
    "raw", ["PRIVATE_COMPLETION invalid JSON", '{"title":{"secret":"PRIVATE_COMPLETION"}}']
)
def test_invalid_completion_text_is_not_in_terminal_exception(raw):
    provider = FakeProvider([raw, raw])

    with pytest.raises(StructuredOutputError) as caught:
        StructuredGenerator(LLMClient(provider)).generate(
            Result,
            stage="analyze",
            model="openai/gpt-oss-20b",
            system_prompt="Return JSON.",
            user_prompt="Make a title.",
        )

    assert caught.value.attempts == 2
    assert "PRIVATE_COMPLETION" not in "".join(traceback.format_exception(caught.value))
