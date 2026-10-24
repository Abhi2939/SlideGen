import json
import threading
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
from app.config import Settings
from app.pipeline.orchestrator import generate_legacy_deck as generate_deck
from test_jobs import wait_for_status


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


def test_job_fails_on_limit_before_another_inflight_call_finishes(tmp_path, monkeypatch):
    started = threading.Event()
    release = threading.Event()
    calls = []

    class Provider:
        def complete(self, messages, **kwargs):
            model = json.loads(messages[0]["content"].split("JSON Schema:\n")[1])["title"]
            calls.append(model)
            if model == "AnalysisOutput":
                return json.dumps(
                    {"deck_title": "Dogs", "deck_type": "education", "slide_count": 3}
                )
            if model == "OutlineOutput":
                return json.dumps(
                    {
                        "deck_title": "Dogs",
                        "slides": [
                            {"index": i, "title": title, "layout": layout, "purpose": "Explain"}
                            for i, (title, layout) in enumerate(
                                [("Dogs", "title"), ("Care", "bullets"), ("Close", "closing")]
                            )
                        ],
                    }
                )
            if model == "TitleSlide":
                started.set()
                assert release.wait(5)
                # Invalid output would normally trigger a second structured call.
                return "invalid JSON"
            assert model == "BulletsSlide"
            assert started.wait(5)
            raise FakeLimit("PRIVATE provider response")

    generator = StructuredGenerator(LLMClient(Provider()))
    settings = Settings("groq", None, "fake", None, None, None, None, 2)
    store = JobStore(tmp_path)
    runner = JobRunner(store)
    runner.generate = lambda prompt, **kwargs: generate_deck(
        prompt, settings=settings, generator=generator, max_workers=2, **kwargs
    )
    try:
        job = runner.submit("Make a 3-slide deck about dogs")
        assert started.wait(5)
        failed = wait_for_status(store, job.id, "failed")
        assert failed.error == str(ProviderLimitError())
        assert not release.is_set()
    finally:
        release.set()
        runner.close()
    assert calls.count("TitleSlide") == 1
    assert "ClosingSlide" not in calls
