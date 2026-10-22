from app.llm import groq_provider
from types import SimpleNamespace

import pytest


class _FakeHttpxClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _FakeGroq:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


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


def test_native_schema_recovery_uses_low_reasoning_with_unchanged_budget(monkeypatch):
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
    )

    assert requests[0]["reasoning_effort"] == "low"
    assert requests[0]["include_reasoning"] is False
    assert requests[0]["max_completion_tokens"] == 1800
