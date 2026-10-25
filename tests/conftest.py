import pytest
import time


@pytest.fixture(autouse=True)
def disable_live_assets(monkeypatch):
    """All automated tests stay offline; asset tests inject fake HTTP explicitly."""
    monkeypatch.setenv("SLIDEGEN_ASSETS_ENABLED", "false")


@pytest.fixture(autouse=True)
def isolated_llm_window(monkeypatch):
    """Offline tests exercise admission without real minute-long sleeps."""
    from app.llm import pacing

    offset = [0.0]
    limiter = pacing.TokenLimiter(
        pacing.RateSettings(),
        clock=lambda: time.monotonic() + offset[0],
        sleep=lambda seconds: offset.__setitem__(0, offset[0] + seconds),
    )
    monkeypatch.setattr(pacing, "_shared", limiter)
