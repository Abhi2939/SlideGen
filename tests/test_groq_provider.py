from app.llm import groq_provider
from types import SimpleNamespace

import pytest
import httpx


class _FakeHttpxClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _FakeGroq:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def test_raw_sdk_response_preserves_usage_and_rate_headers():
    from groq import Groq

    def respond(request):
        return httpx.Response(
            200,
            headers={"x-ratelimit-remaining-tokens": "6000", "x-ratelimit-reset-tokens": "7.66s"},
            json={
                "id": "test",
                "object": "chat.completion",
                "created": 1,
                "model": "test",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25},
            },
        )

    # Exercise the installed SDK's real raw-response parser without networking.
    provider = groq_provider.GroqProvider.__new__(groq_provider.GroqProvider)
    with httpx.Client(transport=httpx.MockTransport(respond)) as transport:
        provider._client = Groq(api_key="fake", http_client=transport, max_retries=0)
        content = provider.complete(
            [{"role": "user", "content": "Hello"}], model="test", max_tokens=100
        )
    assert content == "ok"
    assert content.headers["x-ratelimit-remaining-tokens"] == "6000"
    assert content.usage.completion_tokens == 5


def test_groq_provider_ignores_environment_proxies_by_default(monkeypatch):
    monkeypatch.setattr(groq_provider.httpx, "Client", _FakeHttpxClient)
    monkeypatch.setattr(groq_provider, "Groq", _FakeGroq)

    provider = groq_provider.GroqProvider("test-key")

    assert provider._client.kwargs["http_client"].kwargs == {"trust_env": False}
    assert provider._client.kwargs["max_retries"] == 0


def test_groq_provider_can_use_environment_proxy_when_requested(monkeypatch):
    monkeypatch.setattr(groq_provider.httpx, "Client", _FakeHttpxClient)
    monkeypatch.setattr(groq_provider, "Groq", _FakeGroq)

    provider = groq_provider.GroqProvider("test-key", use_system_proxy=True)

    assert provider._client.kwargs["http_client"].kwargs == {"trust_env": True}


@pytest.mark.parametrize("model", ["openai/gpt-oss-20b", "openai/gpt-oss-120b", "other-groq-model"])
def test_structured_completion_excludes_reasoning_only_for_supported_models(monkeypatch, model):
    requests = []

    def create(**kwargs):
        requests.append(kwargs)
        return SimpleNamespace(
            usage=None,
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content='{"title":"Ready"}', reasoning="PRIVATE_REASONING"
                    )
                )
            ],
        )

    monkeypatch.setattr(groq_provider.httpx, "Client", _FakeHttpxClient)
    monkeypatch.setattr(
        groq_provider,
        "Groq",
        lambda **kwargs: SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        ),
    )

    content = groq_provider.GroqProvider("fake").complete(
        [{"role": "user", "content": "Return JSON"}],
        model=model,
        response_format={"type": "json_object"},
        max_tokens=1800,
    )

    assert content == '{"title":"Ready"}'
    assert requests[0]["max_completion_tokens"] == 1800
    assert "reasoning_format" not in requests[0]
    if model.startswith("openai/gpt-oss-"):
        assert requests[0]["include_reasoning"] is False
    else:
        assert "include_reasoning" not in requests[0]
    assert "reasoning_effort" not in requests[0]


@pytest.mark.parametrize("effort, expected", [(None, "low"), ("medium", "medium")])
def test_native_schema_preserves_explicit_normal_effort_and_recovery_budget(
    monkeypatch, effort, expected
):
    requests = []

    def create(**kwargs):
        requests.append(kwargs)
        return SimpleNamespace(
            usage=None,
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"title":"Ready"}'))],
        )

    monkeypatch.setattr(groq_provider.httpx, "Client", _FakeHttpxClient)
    monkeypatch.setattr(
        groq_provider,
        "Groq",
        lambda **kwargs: SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        ),
    )

    groq_provider.GroqProvider("fake").complete(
        [{"role": "user", "content": "Return JSON"}],
        model="openai/gpt-oss-20b",
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "test",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
            },
        },
        max_tokens=1800,
        reasoning_effort=effort,
    )

    assert requests[0]["reasoning_effort"] == expected
    assert requests[0]["include_reasoning"] is False
    assert requests[0]["max_completion_tokens"] == 1800
