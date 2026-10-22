from app.llm import groq_provider


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
