"""Build the configured provider without coupling pipeline code to its SDK."""

from app.config import Settings
from app.core.errors import LLMProviderError
from app.llm.client import LLMClient


def create_llm_client(settings: Settings | None = None) -> LLMClient:
    settings = settings or Settings.from_env()
    if settings.llm_provider == "groq":
        from app.llm.groq_provider import GroqProvider

        return LLMClient(
            GroqProvider(settings.api_key, use_system_proxy=settings.use_system_proxy)
        )
    raise LLMProviderError(
        f"Unsupported LLM provider {settings.llm_provider!r}; currently supported: groq"
    )
