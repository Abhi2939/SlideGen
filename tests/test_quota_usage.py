import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from app.core.errors import ProviderLimitError
from app.jobs.runner import JobRunner
from app.jobs.store import JobStore
from app.llm.client import LLMClient
from app.llm.groq_provider import GroqProvider
from app.llm.structured import StructuredGenerator
from app.llm.usage import TokenUsage, record_usage, usage_scope
from app.pipeline.models import AnalysisOutput


class FakeLimit(Exception):
    status_code = 429


def test_sdk_limit_is_sanitized_and_not_retried_or_logged(tmp_path, caplog):
    caplog.set_level("INFO")
    calls = []

    class Provider:
        def complete(self, *args, **kwargs):
            calls.append(1)
            raise FakeLimit("SECRET raw provider response")

    generator = StructuredGenerator(LLMClient(Provider()), max_attempts=3)

    def generate(prompt):
        return generator.generate(
            AnalysisOutput, stage="analyze", model="fake", system_prompt="fake", user_prompt=prompt
        )

    store = JobStore(tmp_path)
    with JobRunner(store, generate=generate) as runner:
        job = runner.submit("Make a presentation")
    failed = store.get(job.id)
    assert failed.status == "failed"
    assert failed.error == str(ProviderLimitError())
    assert failed.output_path is None
    assert calls == [1]
    assert "SECRET" not in caplog.text
    assert caplog.text.count("token usage (reported calls only)") == 1


def test_groq_usage_is_counted_even_for_invalid_json(monkeypatch):
    from app.llm import groq_provider

    result = SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=20, completion_tokens=5, total_tokens=25),
        choices=[SimpleNamespace(message=SimpleNamespace(content="bad"))],
    )
    monkeypatch.setattr(
        groq_provider,
        "Groq",
        lambda **kwargs: SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: result))
        ),
    )
    generator = StructuredGenerator(LLMClient(GroqProvider("fake")), max_attempts=2)
    from app.core.errors import StructuredOutputError

    with pytest.raises(StructuredOutputError):
        generator.generate(
            AnalysisOutput, stage="analyze", model="fake", system_prompt="fake", user_prompt="fake"
        )
    assert generator.usage.snapshot()["analyze"] == {"input": 40, "output": 10, "total": 50}


def test_usage_thread_safety_repair_attribution_and_isolation():
    counts = TokenUsage()
    other = TokenUsage()

    def add(stage):
        with usage_scope(counts, stage):
            record_usage(SimpleNamespace(prompt_tokens=3, completion_tokens=2, total_tokens=5))

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(add, ["write", "repair"] * 50))
    assert counts.snapshot()["write"]["total"] == 250
    assert counts.snapshot()["repair"]["total"] == 250
    assert all(c["total"] == 0 for c in other.snapshot().values())
    assert "prompt" not in json.dumps(counts.snapshot())


def test_quota_during_writing_does_not_start_pending_slides():
    from app.pipeline.models import OutlineOutput
    from app.pipeline.write_slides import write_slides

    calls = []

    class Generator:
        def generate(self, *args, **kwargs):
            calls.append(kwargs["stage"])
            raise ProviderLimitError()

    analysis = AnalysisOutput(deck_title="Dogs", deck_type="education", slide_count=3)
    outline = OutlineOutput(
        deck_title="Dogs",
        slides=[
            {"index": i, "layout": layout, "title": title, "purpose": "Introduce"}
            for i, (layout, title) in enumerate(
                [("title", "Dogs"), ("bullets", "Care"), ("closing", "Close")]
            )
        ],
    )
    with pytest.raises(ProviderLimitError):
        write_slides(
            "Make a deck about dogs",
            analysis,
            outline,
            Generator(),
            "fake",
            max_workers=1,
            semantic_attempts=3,
        )
    assert calls == ["write"]


def test_nonquota_failure_does_not_start_another_slide_api_call():
    from app.core.errors import SlideWriteError, StructuredOutputError
    from app.pipeline.models import OutlineOutput
    from app.pipeline.write_slides import write_slides

    calls = []

    class Generator:
        def generate(self, *args, **kwargs):
            calls.append(kwargs["stage"])
            raise StructuredOutputError("write", 2, "response did not match the required schema")

    analysis = AnalysisOutput(deck_title="Dogs", deck_type="education", slide_count=3)
    outline = OutlineOutput(
        deck_title="Dogs",
        slides=[
            {"index": i, "layout": layout, "title": title, "purpose": "Explain"}
            for i, (layout, title) in enumerate(
                [("title", "Dogs"), ("bullets", "Care"), ("closing", "Summary")]
            )
        ],
    )
    with pytest.raises(SlideWriteError):
        write_slides(
            "Make a deck about dogs", analysis, outline, Generator(), "fake", max_workers=1
        )
    assert calls == ["write"]


def test_global_serialization_does_not_start_pending_slide_after_a_limit(tmp_path):
    from app.llm.pacing import RateSettings, TokenLimiter
    from app.core.deck_schema import TitleSlide
    calls = []

    class Provider:
        def complete(self, *args, **kwargs):
            calls.append(1)
            raise FakeLimit("PRIVATE provider response")

    generator = StructuredGenerator(LLMClient(Provider(), limiter=TokenLimiter(RateSettings())))

    def generate(prompt):
        return generator.generate(TitleSlide, stage="write", model="fake",
                                  system_prompt="Write one slide", user_prompt=prompt)

    store = JobStore(tmp_path)
    with JobRunner(store, generate=generate) as runner:
        job = runner.submit("Make a deck")
    failed = store.get(job.id)
    assert failed.status == "failed"
    assert failed.error == str(ProviderLimitError())
    assert calls == [1]
