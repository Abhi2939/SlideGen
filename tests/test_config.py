import pytest

from app.config import DEFAULT_MODELS, Settings


def _settings(**overrides):
    values = {
        "llm_provider": "groq",
        "api_key": "test-key",
        "default_model": None,
        "analysis_model": None,
        "outline_model": None,
        "writing_model": None,
        "review_model": None,
        "structured_attempts": 2,
    }
    values.update(overrides)
    return Settings(**values)


@pytest.mark.parametrize("stage", ["analyze", "outline", "write", "review"])
def test_settings_use_current_default_model_when_unset(stage):
    assert _settings().model_for(stage) == DEFAULT_MODELS[stage]


def test_stage_model_override_takes_precedence_over_default():
    settings = _settings(default_model="shared-model", analysis_model="analysis-override")
    assert settings.model_for("analyze") == "analysis-override"
    assert settings.model_for("review") == "shared-model"


def test_unknown_stage_is_rejected():
    with pytest.raises(ValueError, match="Unknown generation stage"):
        _settings().model_for("unknown")


def test_system_proxy_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("SLIDEGEN_USE_SYSTEM_PROXY", raising=False)

    assert Settings.from_env().use_system_proxy is False


def test_system_proxy_can_be_enabled_for_restricted_networks(monkeypatch):
    monkeypatch.setenv("SLIDEGEN_USE_SYSTEM_PROXY", "true")

    assert Settings.from_env().use_system_proxy is True


def test_system_proxy_setting_rejects_invalid_values(monkeypatch):
    monkeypatch.setenv("SLIDEGEN_USE_SYSTEM_PROXY", "sometimes")

    with pytest.raises(ValueError, match="must be a boolean"):
        Settings.from_env()
