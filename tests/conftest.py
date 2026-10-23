import pytest


@pytest.fixture(autouse=True)
def disable_live_assets(monkeypatch):
    """All automated tests stay offline; asset tests inject fake HTTP explicitly."""
    monkeypatch.setenv("SLIDEGEN_ASSETS_ENABLED", "false")
