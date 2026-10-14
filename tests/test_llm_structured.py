import json

import pytest
from pydantic import BaseModel, ValidationError

from app.core.errors import StructuredOutputError
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
    provider = FakeProvider([RuntimeError("rate limit; retry after 2 seconds"), '{"title":"Ready"}'])
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
    assert "rate limit" in exc_info.value.detail
    assert len(provider.calls) == 1
