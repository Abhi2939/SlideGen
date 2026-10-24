import traceback
from types import SimpleNamespace

import httpx
import pytest
from groq import RateLimitError
from pydantic import BaseModel

from app.core.errors import ProviderLimitError
from app.llm.client import LLMClient
from app.llm.structured import StructuredGenerator


class Result(BaseModel):
    title: str


def limit_error(metric="tokens per minute (TPM)", *, requested=2627, retry="20", remaining="993"):
    return RateLimitError(
        "PRIVATE_CREDENTIAL PRIVATE_COMPLETION",
        response=httpx.Response(
            429,
            request=httpx.Request("POST", "https://example.test/completions"),
            headers={
                "retry-after": retry,
                "x-ratelimit-limit-tokens": "8000",
                "x-ratelimit-remaining-tokens": "9",
                "x-ratelimit-remaining-requests": remaining,
                "x-private": "PRIVATE_CREDENTIAL",
            },
        ),
        body={
            "error": {
                "message": f"Rate limit reached on {metric}: Limit 8000, Used 7991, "
                f"Requested {requested}. PRIVATE_CREDENTIAL",
                "failed_generation": "PRIVATE_COMPLETION",
            }
        },
    )


class Provider:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def complete(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        result = next(self.responses)
        if isinstance(result, Exception):
            raise result
        return result


def generate(generator):
    return generator.generate(
        Result, stage="write", model="fake", system_prompt="Return JSON", user_prompt="Title"
    )


def test_temporary_minute_limit_resumes_same_call_without_spending_json_attempt(
    monkeypatch, caplog
):
    waits = []
    monkeypatch.setattr("app.llm.structured.time.sleep", waits.append)
    provider = Provider([limit_error(retry="19.1"), '{"title":"Ready"}'])
    generator = StructuredGenerator(LLMClient(provider), max_attempts=1)
    progress = []
    generator.on_provider_wait = lambda stage, seconds: progress.append((stage, seconds))
    assert generate(generator).title == "Ready"
    assert provider.calls[0] == provider.calls[1]
    assert waits == [21]
    assert progress == [("write", 21), ("write", 0)]
    assert "tokens_per_minute" in caplog.text
    assert "PRIVATE" not in caplog.text


@pytest.mark.parametrize(
    "error",
    [
        limit_error("tokens per day (TPD)"),
        limit_error("requests per day (RPD)"),
        limit_error("unrecognized"),
        limit_error(requested=8001),
        limit_error(retry="61"),
        limit_error(retry="PRIVATE_CREDENTIAL"),
        limit_error(retry="0"),
        limit_error(retry="-1"),
        limit_error(retry="nan"),
        limit_error(remaining="0"),
    ],
)
def test_nonrecoverable_limits_never_sleep_or_retry(monkeypatch, error):
    waits = []
    monkeypatch.setattr("app.llm.structured.time.sleep", waits.append)
    provider = Provider([error])
    with pytest.raises(ProviderLimitError) as caught:
        generate(StructuredGenerator(LLMClient(provider)))
    assert len(provider.calls) == 1
    assert waits == []
    assert "PRIVATE" not in "".join(traceback.format_exception(caught.value))


def test_minute_retries_are_bounded_and_metadata_is_sanitized(monkeypatch, caplog):
    waits = []
    monkeypatch.setattr("app.llm.structured.time.sleep", waits.append)
    provider = Provider([limit_error() for _ in range(7)])
    with pytest.raises(ProviderLimitError) as caught:
        generate(StructuredGenerator(LLMClient(provider)))
    assert len(provider.calls) == 7
    assert waits == [21, 21, 21, 21, 32, 60]
    assert caught.value.status_code == 429
    assert caught.value.diagnostics == {
        "limit_type": "tokens_per_minute",
        "limit": 8000,
        "used": 7991,
        "requested": 2627,
        "tokens_per_minute_limit": 8000,
        "tokens_per_minute_remaining": 9,
        "requests_per_day_remaining": 993,
        "retry_after_seconds": 20,
    }
    assert "PRIVATE" not in caplog.text
    assert "PRIVATE" not in "".join(traceback.format_exception(caught.value))


def test_limit_wait_respects_job_deadline(monkeypatch):
    waits = []
    monkeypatch.setattr("app.llm.structured.time.sleep", waits.append)
    monkeypatch.setattr("app.llm.structured.time.monotonic", lambda: 100)
    provider = Provider([limit_error()])
    generator = StructuredGenerator(LLMClient(provider))
    generator.deadline = 115
    with pytest.raises(TimeoutError):
        generate(generator)
    assert waits == []
    assert len(provider.calls) == 1


def test_pending_retry_stops_after_another_call_exhausts_provider_budget(monkeypatch):
    provider = Provider([limit_error(), '{"title":"Unused"}'])
    generator = StructuredGenerator(LLMClient(provider))
    monkeypatch.setattr("app.llm.structured.time.sleep", lambda seconds: generator._limited.set())
    with pytest.raises(ProviderLimitError):
        generate(generator)
    assert len(provider.calls) == 1


def test_temporary_limit_retries_can_be_disabled(monkeypatch):
    waits = []
    monkeypatch.setattr("app.llm.structured.time.sleep", waits.append)
    provider = Provider([limit_error()])
    with pytest.raises(ProviderLimitError):
        generate(StructuredGenerator(LLMClient(provider), max_rate_limit_retries=0))
    assert waits == []
    assert len(provider.calls) == 1


def test_production_short_wait_sequence_recovers_without_restarting_work(monkeypatch):
    waits = []
    monkeypatch.setattr("app.llm.structured.time.sleep", waits.append)
    provider = Provider(
        [limit_error(retry=retry) for retry in ("17", "2", "1")] + ['{"title":"Ready"}']
    )
    generator = StructuredGenerator(LLMClient(provider), max_attempts=1)
    assert generate(generator).title == "Ready"
    assert waits == [18, 4, 8]
    assert all(call == provider.calls[0] for call in provider.calls)
    assert not generator._limited.is_set()


def test_custom_minute_retry_cap_is_preserved(monkeypatch):
    waits = []
    monkeypatch.setattr("app.llm.structured.time.sleep", waits.append)
    provider = Provider([limit_error(retry="1") for _ in range(3)])
    generator = StructuredGenerator(LLMClient(provider), max_rate_limit_retries=2)
    with pytest.raises(ProviderLimitError):
        generate(generator)
    assert len(provider.calls) == 3
    assert waits == [2, 4]


def test_backoff_headroom_must_also_fit_job_deadline(monkeypatch):
    waits = []
    monkeypatch.setattr("app.llm.structured.time.sleep", waits.append)
    monkeypatch.setattr("app.llm.structured.time.monotonic", lambda: 100)
    provider = Provider([limit_error(retry="2")])
    generator = StructuredGenerator(LLMClient(provider))
    generator.deadline = 103
    with pytest.raises(TimeoutError):
        generate(generator)
    assert waits == []
    assert len(provider.calls) == 1


def test_unstructured_private_error_body_is_never_copied():
    error = SimpleNamespace()
    # Error metadata can be absent or malformed without leaking exception text.
    from app.llm.limits import provider_limit_error, safe_label

    assert provider_limit_error(error).diagnostics == {}
    assert safe_label("private\nlog injection") == "unknown"
