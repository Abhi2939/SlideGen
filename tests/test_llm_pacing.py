import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.core.errors import LLMProviderError, ProviderLimitError
from app.llm.client import LLMClient, prompt_tokens
from app.llm.groq_provider import Completion
from app.llm.pacing import RateSettings, TokenLimiter
from app.llm.structured import StructuredGenerator
from test_provider_limits import Clock, Provider, Result, limit_error


def limiter(**settings):
    clock = Clock()
    return TokenLimiter(RateSettings(**settings), clock=lambda: clock.now, sleep=clock.sleep), clock


def test_no_reservation_exceeds_any_rolling_minute():
    rate, clock = limiter()
    events = []
    for cost in [2300, 3200, 2286, 3100] * 12:
        delay = rate.reserve("primary", cost)
        while delay:
            clock.sleep(delay)
            delay = rate.reserve("primary", cost)
        events.append((clock.now, cost))
        assert sum(n for stamp, n in events if clock.now - 60 < stamp <= clock.now) <= 6800
    assert clock.now > 60


def test_models_have_separate_windows_and_oversized_request_fails_fast():
    rate, _ = limiter()
    assert rate.reserve("primary", 6800) == 0
    assert rate.reserve("fallback", 6800) == 0
    assert rate.reserve("primary", 1) == 60
    with pytest.raises(LLMProviderError, match="exceeds"):
        rate.reserve("primary", 6801)


def test_reported_usage_releases_only_unused_tokens_and_preserves_admission_limit():
    rate, clock = limiter()
    events = []

    class MeasuredProvider:
        def complete(self, messages, **kwargs):
            events.append(clock.now)
            assert rate.snapshot()["models"]["primary"]["window_tokens"] <= rate.budget
            return Completion("ok", {}, SimpleNamespace(prompt_tokens=1900, completion_tokens=100))

    client = LLMClient(MeasuredProvider(), limiter=rate)
    messages = [{"content": "x" * 9008}]  # 2260 estimated prompt + 1600 output.
    for _ in range(8):
        client.complete(messages, model="primary", max_tokens=1600)
    assert events == [0, 0, 60, 60, 120, 120, 180, 180]
    assert rate.snapshot()["models"]["primary"]["window_tokens"] == 4000


def test_unknown_usage_keeps_the_full_reservation():
    rate, clock = limiter()
    client = LLMClient(Provider(["ok", "ok"]), limiter=rate)
    for _ in range(2):
        client.complete([{"content": "x" * 9008}], model="primary", max_tokens=1600)
    assert clock.now == 60
    assert rate.snapshot()["models"]["primary"]["window_tokens"] == 3860


def test_settlement_matches_request_identity_and_does_not_refund_remote_limits():
    rate, _ = limiter()
    first, second = object(), object()
    assert rate.reserve("primary", 3000, reservation_id=first) == 0
    assert rate.reserve("primary", 3000, reservation_id=second) == 0
    rate.feedback("primary", {"tokens_per_minute_remaining": 100, "tokens_reset_seconds": 20})
    rate.settle("primary", second, 1000)
    rate.settle("primary", object(), 0)
    assert rate.snapshot()["models"]["primary"]["window_tokens"] == 4000
    assert rate.reserve("primary", 1000) == 20
    rate.settle("primary", first, 3500)
    assert rate.snapshot()["models"]["primary"]["window_tokens"] == 4500


def test_atomic_reservations_across_threads():
    rate, _ = limiter()
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda _: rate.reserve("primary", 1000), range(30)))
    assert results.count(0) == 6
    assert rate.snapshot()["models"]["primary"]["window_tokens"] == 6000


def test_clients_and_jobs_share_default_limiter():
    assert LLMClient(Provider([])).limiter is LLMClient(Provider([])).limiter


def test_global_semaphore_serializes_different_clients():
    rate = TokenLimiter(RateSettings())
    active = 0
    peak = 0
    lock = threading.Lock()

    class BlockingProvider:
        def complete(self, *args, **kwargs):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.03)
            with lock:
                active -= 1
            return "ok"

    clients = [LLMClient(BlockingProvider(), limiter=rate) for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda c: c.complete([], model="primary", max_tokens=10), clients))
    assert results == ["ok", "ok"] and peak == 1


def test_headers_restrict_next_call_and_reset_is_respected():
    rate, clock = limiter()
    usage = SimpleNamespace(prompt_tokens=20, completion_tokens=5)
    provider = Provider(
        [
            Completion(
                "ok",
                {"x-ratelimit-remaining-tokens": "10", "x-ratelimit-reset-tokens": "7.66s"},
                usage,
            ),
            "ok",
        ]
    )
    llm = LLMClient(provider, limiter=rate)
    llm.complete([], model="primary", max_tokens=100)
    llm.complete([], model="primary", max_tokens=100)
    assert clock.now == pytest.approx(7.66)


def test_fallback_after_shared_admission_wait_preserves_schema():
    rate, clock = limiter(fallback_after=12, fallback_models=("other-model",))
    rate.reserve("primary", 6800)
    provider = Provider(['```json\n{"title":"Ready"}\n```'])
    result = StructuredGenerator(LLMClient(provider, limiter=rate)).generate(
        Result,
        stage="write",
        model="primary",
        system_prompt="JSON",
        user_prompt="Title",
        max_tokens=100,
    )
    assert result.title == "Ready" and clock.now == 12
    assert provider.calls[0][1]["model"] == "other-model"


def test_429_fallback_removes_unsupported_reasoning_and_native_format():
    rate, clock = limiter(fallback_after=5, fallback_models=("other-model",))
    provider = Provider([limit_error(), '{"title":"Ready"}'])
    result = StructuredGenerator(LLMClient(provider, limiter=rate)).generate(
        Result,
        stage="repair",
        model="openai/gpt-oss-120b",
        system_prompt="JSON",
        user_prompt="Title",
        max_tokens=100,
    )
    assert result.title == "Ready" and clock.now == 5
    assert provider.calls[1][1]["response_format"] == {"type": "json_object"}
    assert "reasoning_effort" not in provider.calls[1][1]


def test_daily_limit_never_falls_back():
    rate, clock = limiter(fallback_after=0, fallback_models=("other-model",))
    provider = Provider([limit_error("requests per day (RPD)")])
    with pytest.raises(ProviderLimitError):
        LLMClient(provider, limiter=rate).complete([], model="primary", max_tokens=100)
    assert clock.now == 0 and len(provider.calls) == 1


@pytest.mark.parametrize("count", [8, 9, 10])
def test_full_deck_completes_under_8000_tpm(count, monkeypatch):
    from app.core.deck_schema import Deck
    from app.pipeline.editorial import StoryOutline
    from app.pipeline.orchestrator import generate_deck
    from test_editorial_pipeline import FIXTURE, PROMPT, StoryGenerator

    fixture = Deck.model_validate_json(FIXTURE.read_text())
    if count == 8:
        fixture.slides.pop(5)
    elif count == 10:
        fixture.slides.insert(
            -1, fixture.slides[2].model_copy(update={"title": "Proposed next step"})
        )
    fake = StoryGenerator(fixture)
    outline = fake.generate(StoryOutline, stage="outline")
    rate, clock = limiter()
    events = []
    responses = [
        outline.model_dump_json(),
        *[s.model_dump_json() for s in fixture.slides],
        '{"passed":true,"issues":[],"missing_fact_ids":[]}',
    ]

    class MeasuredProvider(Provider):
        def complete(self, messages, **kwargs):
            cost = prompt_tokens(messages) + kwargs["max_tokens"]
            events.append((clock.now, cost))
            assert sum(n for stamp, n in events if clock.now - 60 < stamp <= clock.now) <= 6800
            return super().complete(messages, **kwargs)

    provider = MeasuredProvider(responses)
    settings = Settings("groq", None, "primary", None, None, None, None, 2)
    monkeypatch.setattr("app.pipeline.editorial.prepare_assets", lambda deck, *a, **k: deck)
    deck = generate_deck(
        PROMPT, settings=settings, generator=StructuredGenerator(LLMClient(provider, limiter=rate))
    )
    assert len(deck.slides) == count
    assert len(provider.calls) == count + 2 and clock.now >= 60


def test_stage_token_override_and_completion_observability(monkeypatch, caplog):
    monkeypatch.setenv("LLM_WRITE_MAX_TOKENS", "950")
    caplog.set_level("INFO")
    rate, _ = limiter()
    provider = Provider(
        [Completion("ok", {}, SimpleNamespace(prompt_tokens=25, completion_tokens=12))]
    )
    LLMClient(provider, limiter=rate).complete([], stage="write", model="primary", max_tokens=1800)
    assert provider.calls[0][1]["max_tokens"] == 950
    assert "prompt_tokens=25 completion_tokens=12" in caplog.text
    assert "wait_seconds=" in caplog.text and "fallback_used=False" in caplog.text


def test_image_bytes_are_not_estimated_as_text():
    assert (
        prompt_tokens([{"content": [{"type": "image_url", "image_url": {"url": "x" * 100000}}]}])
        == 1608
    )


def test_rate_settings_defaults_and_validation(monkeypatch):
    assert RateSettings.from_env().tpm == 8000
    monkeypatch.setenv("LLM_SAFETY_MARGIN", "1.2")
    with pytest.raises(ValueError):
        RateSettings.from_env()
