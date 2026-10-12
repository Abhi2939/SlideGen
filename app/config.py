"""Environment-backed application settings."""

from dataclasses import dataclass
import os

from dotenv import load_dotenv


DEFAULT_MODELS = {
    "analyze": "openai/gpt-oss-20b",
    "outline": "openai/gpt-oss-20b",
    "write": "openai/gpt-oss-120b",
    "review": "openai/gpt-oss-20b",
}


@dataclass(frozen=True)
class Settings:
    llm_provider: str
    api_key: str | None
    default_model: str | None
    analysis_model: str | None
    outline_model: str | None
    writing_model: str | None
    review_model: str | None
    structured_attempts: int

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        provider = os.getenv("SLIDEGEN_LLM_PROVIDER", "groq").strip().lower()
        provider_key = os.getenv(f"{provider.upper()}_API_KEY")
        api_key = os.getenv("SLIDEGEN_API_KEY") or provider_key
        attempts = int(os.getenv("SLIDEGEN_STRUCTURED_ATTEMPTS", "2"))
        if attempts < 1:
            raise ValueError("SLIDEGEN_STRUCTURED_ATTEMPTS must be at least 1")
        return cls(
            llm_provider=provider,
            api_key=api_key,
            default_model=os.getenv("SLIDEGEN_MODEL") or None,
            analysis_model=os.getenv("SLIDEGEN_ANALYSIS_MODEL") or None,
            outline_model=os.getenv("SLIDEGEN_OUTLINE_MODEL") or None,
            writing_model=os.getenv("SLIDEGEN_WRITING_MODEL") or None,
            review_model=os.getenv("SLIDEGEN_REVIEW_MODEL") or None,
            structured_attempts=attempts,
        )

    def model_for(self, stage: str) -> str:
        stage_settings = {
            "analyze": (self.analysis_model, "SLIDEGEN_ANALYSIS_MODEL"),
            "outline": (self.outline_model, "SLIDEGEN_OUTLINE_MODEL"),
            "write": (self.writing_model, "SLIDEGEN_WRITING_MODEL"),
            "review": (self.review_model, "SLIDEGEN_REVIEW_MODEL"),
        }.get(stage)
        if stage_settings is None:
            raise ValueError(f"Unknown generation stage {stage!r}")
        specific, env_name = stage_settings
        model = specific or self.default_model or DEFAULT_MODELS[stage]
        if not model:
            raise ValueError(
                f"No model configured for stage {stage!r}; set {env_name} or SLIDEGEN_MODEL"
            )
        return model
