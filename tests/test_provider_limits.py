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


class Clock:
    def __init__(self):
        self.now = 0.0
        self.waits = []

    def sleep(self, seconds):
        self.waits.append(seconds)
        self.now += seconds


def client(provider, **settings):
    from app.llm.pacing import RateSettings, TokenLimiter

    clock = Clock()
    limiter = TokenLimiter(RateSettings(**settings), clock=lambda: clock.now, sleep=clock.sleep)
    return LLMClient(provider, limiter=limiter), clock


def test_temporary_minute_limit_resumes_same_call_without_spending_json_attempt(caplog):
    provider = Provider([limit_error(retry="19.1"), '{"title":"Ready"}'])
    llm, clock = client(provider)
    generator = StructuredGenerator(llm, max_attempts=1)
    progress = []
    generator.on_provider_wait = lambda stage, seconds: progress.append((stage, seconds))
    assert generate(generator).title == "Ready"
    assert provider.calls[0] == provider.calls[1]
    assert clock.now >= 20
    assert progress[0][0] == "write" and progress[-1] == ("write", 0)
    assert "tokens_per_minute" in caplog.text and "PRIVATE" not in caplog.text


@pytest.mark.parametrize("metric", ["tokens per day (TPD)", "requests per day (RPD)"])
def test_daily_limits_fail_immediately_with_reset(metric):
    provider = Provider([limit_error(metric)])
    llm, clock = client(provider)
    with pytest.raises(ProviderLimitError) as caught:
        generate(StructuredGenerator(llm))
    assert len(provider.calls) == 1 and clock.waits == []
    assert "daily" in str(caught.value) and "Resets at" in str(caught.value)
    assert "PRIVATE" not in "".join(traceback.format_exception(caught.value))


def test_production_payload_is_tpm_even_with_984_daily_requests_remaining():
    from app.llm.limits import provider_limit_error

    error = limit_error(requested=2286, retry="6", remaining="984")
    error.body["error"]["message"] = (
        "Rate limit reached on tokens per minute (TPM): Limit 8000, Used 6441, Requested 2286."
    )
    parsed = provider_limit_error(error)
    assert parsed.diagnostics["limit_type"] == "tokens_per_minute"
    assert parsed.diagnostics["used"] == 6441
    assert parsed.diagnostics["retry_after_seconds"] == 6
    assert parsed.diagnostics["requests_per_day_remaining"] == 984
    assert "daily" not in str(parsed)


@pytest.mark.parametrize("retry", ["0", "1", "61", "PRIVATE_CREDENTIAL", "nan"])
def test_minute_limits_wait_even_without_valid_short_retry_header(retry):
    provider = Provider([limit_error(retry=retry), '{"title":"Ready"}'])
    llm, clock = client(provider)
    assert generate(StructuredGenerator(llm)).title == "Ready"
    assert clock.now >= 60  # Remote remaining=9 also constrains admission.


def test_minute_retries_are_bounded_and_metadata_is_sanitized(caplog):
    provider = Provider([limit_error(retry="1") for _ in range(9)])
    llm, clock = client(provider, max_retries=8, wait_budget=1000)
    with pytest.raises(ProviderLimitError) as caught:
        generate(StructuredGenerator(llm))
    assert len(provider.calls) == 9
    assert clock.now >= 8 * 60
    assert caught.value.diagnostics["limit_type"] == "tokens_per_minute"
    assert "PRIVATE" not in caplog.text


def test_wait_budget_is_bounded():
    provider = Provider([limit_error(retry="1") for _ in range(9)])
    llm, clock = client(provider, wait_budget=180)
    with pytest.raises(ProviderLimitError):
        generate(StructuredGenerator(llm))
    assert clock.now <= 180
    assert len(provider.calls) <= 4


def test_limit_wait_respects_job_deadline():
    provider = Provider([limit_error()])
    llm, clock = client(provider)
    generator = StructuredGenerator(llm)
    generator.deadline = 15
    with pytest.raises(TimeoutError):
        generate(generator)
    assert clock.waits == [] and len(provider.calls) == 1


def test_temporary_limit_retries_can_be_disabled():
    provider = Provider([limit_error()])
    llm, clock = client(provider)
    with pytest.raises(ProviderLimitError):
        generate(StructuredGenerator(llm, max_rate_limit_retries=0))
    assert clock.waits == [] and len(provider.calls) == 1


def test_provider_retry_payload_and_duration_headers():
    from app.llm.limits import header_diagnostics, provider_limit_error

    error = SimpleNamespace(
        body={"error": {"limit_type": "requests_per_minute", "retry_after_seconds": 12.2}}
    )
    assert provider_limit_error(error).diagnostics == {
        "limit_type": "requests_per_minute",
        "retry_after_seconds": 13,
    }
    assert header_diagnostics({"X-RateLimit-Reset-Tokens": "1m2.5s", "Retry-After": "2.5"}) == {
        "tokens_reset_seconds": 62.5,
        "retry_after_seconds": 3,
    }


def test_production_short_wait_sequence_recovers_without_restarting_work():
    provider = Provider([limit_error(retry=r) for r in ("17", "2", "1")] + ['{"title":"Ready"}'])
    llm, _ = client(provider, wait_budget=240)
    generator = StructuredGenerator(llm, max_attempts=1)
    assert generate(generator).title == "Ready"
    assert all(call == provider.calls[0] for call in provider.calls)
    assert not generator._limited.is_set()


def test_unstructured_private_error_body_is_never_copied():
    error = SimpleNamespace()
    # Error metadata can be absent or malformed without leaking exception text.
    from app.llm.limits import provider_limit_error, safe_label

    assert provider_limit_error(error).diagnostics == {}
    assert safe_label("private\nlog injection") == "unknown"
