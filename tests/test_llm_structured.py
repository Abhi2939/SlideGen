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
    assert provider.calls[0][1]["response_format"]["type"] == "json_schema"
    assert provider.calls[0][1]["reasoning_effort"] == "medium"
    recovery = provider.calls[1][1]
    assert recovery["response_format"] == {"type": "json_object"}
    assert recovery["reasoning_effort"] == "low"
    assert recovery["max_tokens"] == 1800
    assert "PRIVATE_COMPLETION" not in json.dumps(provider.calls)
    assert "PRIVATE_CREDENTIAL" not in json.dumps(provider.calls)


@pytest.mark.parametrize("raw", ['{"title":7}', '{"wrong":"field"}', "not json"])
def test_provider_recovery_never_accepts_content_that_fails_local_validation(raw):
    provider = FakeProvider([parse_error("json_validate_failed"), raw])
    with pytest.raises(StructuredOutputError) as caught:
        StructuredGenerator(LLMClient(provider)).generate(
            Result,
            stage="outline",
            model="openai/gpt-oss-20b",
            system_prompt="Return JSON.",
            user_prompt="Make a title.",
        )
    assert caught.value.attempts == 2
    assert provider.calls[1][1]["response_format"] == {"type": "json_object"}


def test_json_object_recovery_stays_active_during_local_schema_correction():
    provider = FakeProvider(
        [parse_error("json_validate_failed"), '{"title":7}', '{"title":"Ready"}']
    )
    result = StructuredGenerator(LLMClient(provider), max_attempts=3).generate(
        Result,
        stage="outline",
        model="openai/gpt-oss-20b",
        system_prompt="Return JSON.",
        user_prompt="Make a title.",
    )
    assert result.title == "Ready"
    assert [kwargs["response_format"]["type"] for _, kwargs in provider.calls] == [
        "json_schema",
        "json_object",
        "json_object",
    ]


def test_provider_rejection_logs_safe_request_metadata_without_generated_content(caplog):
    error = parse_error("json_validate_failed")
    error.response.headers["x-request-id"] = "req_0123456789abcdef"
    provider = FakeProvider([error, '{"title":"Ready"}'])
    caplog.set_level("INFO")
    StructuredGenerator(LLMClient(provider)).generate(
        Result,
        stage="outline",
        model="openai/gpt-oss-20b",
        system_prompt="Return JSON.",
        user_prompt="Make a title.",
        max_tokens=3600,
    )
    assert "code=json_validate_failed" in caplog.text
    assert "req_0123456789abcdef" in caplog.text
    assert "schema_id=" in caplog.text
    assert "generated_characters" in caplog.text
    assert "generated_json_valid" in caplog.text
    assert "prompt_tokens=None completion_tokens=None" in caplog.text
    assert "response_format=json_schema" in caplog.text
    assert "response_format=json_object" in caplog.text
    assert "max_completion_tokens=3600" in caplog.text
    assert "PRIVATE_COMPLETION" not in caplog.text
    assert "PRIVATE_CREDENTIAL" not in caplog.text


@pytest.mark.parametrize("generated,valid", [(' {"title":"PRIVATE"} ', True), ('{"title":', False)])
def test_provider_failure_metadata_records_only_json_shape(generated, valid):
    from app.llm.client import response_error_diagnostics

    error = parse_error("json_validate_failed")
    error.response.headers["x-request-id"] = "PRIVATE_CREDENTIAL"
    diagnostics = response_error_diagnostics(error, {"failed_generation": generated})
    assert diagnostics == {
        "generated_characters": len(generated),
        "generated_json_valid": valid,
    }


def test_rate_limit_after_schema_recovery_stops_without_a_third_request():
    provider = FakeProvider([parse_error("json_validate_failed"), ProviderLimitError()])
    with pytest.raises(ProviderLimitError):
        StructuredGenerator(LLMClient(provider), max_attempts=3).generate(
            Result,
            stage="outline",
            model="openai/gpt-oss-20b",
            system_prompt="Return JSON.",
            user_prompt="Make a title.",
        )
    assert len(provider.calls) == 2


@pytest.mark.parametrize("model", ["openai/gpt-oss-20b", "openai/gpt-oss-120b"])
def test_slide_copy_uses_strict_schema_and_low_reasoning_within_small_budget(
    model,
):
    provider = FakeProvider(['{"title":7}', '{"title":"Ready"}'])
    result = StructuredGenerator(LLMClient(provider)).generate(
        Result,
        stage="write",
        model=model,
        system_prompt="Write a title",
        user_prompt="A title",
        max_tokens=1600,
    )
    assert result.title == "Ready"
    first, correction = [kwargs for _, kwargs in provider.calls]
    assert first["response_format"]["type"] == "json_schema"
    schema = first["response_format"]["json_schema"]["schema"]
    assert schema["required"] == ["title"]
    assert schema["additionalProperties"] is False
    assert first["reasoning_effort"] == "low"
    assert correction["reasoning_effort"] == "low"
    assert first["max_tokens"] == correction["max_tokens"] == 1600
    assert "additionalProperties" not in Result.model_json_schema()


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
