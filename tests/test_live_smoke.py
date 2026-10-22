from types import SimpleNamespace

from app.core.errors import ProviderLimitError
from scripts.live_smoke import PROMPTS, run


def test_smoke_stops_at_first_limit_and_reports_completed(tmp_path, capsys):
    calls = []
    builds = []

    def generate(prompt, **kwargs):
        calls.append(prompt)
        kwargs["usage_callback"]({"analyze": {"total": 17}})
        if len(calls) == 2:
            raise ProviderLimitError()
        kwargs["review_callback"]({"passed": True, "issues": []})
        return SimpleNamespace(slides=[SimpleNamespace(layout="title")])

    result = run(
        SimpleNamespace(api_key="fake"),
        tmp_path,
        generate=generate,
        build=lambda *args, **kwargs: builds.append(args[1]),
    )
    assert result == 1
    assert calls == [p for _, p in PROMPTS[:2]]
    assert len(builds) == 1
    output = capsys.readouterr().out
    assert "Prompts completed: acme-pay" in output
    assert "17" in output
    assert "1 slides" in output


def test_smoke_requires_key_without_calling_provider(tmp_path, capsys):
    assert run(SimpleNamespace(api_key=None), tmp_path) == 1
    assert "No prompts completed" in capsys.readouterr().out
