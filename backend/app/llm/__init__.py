"""LLM-клиент: общий интерфейс, OpenAI-совместимый провайдер и фейк для тестов."""

from app.config import Settings
from app.llm.base import LLMClient, LLMError, LLMResponse, Message, Tier, ToolCall, ToolSpec, Usage
from app.llm.fake import FakeLLMClient
from app.llm.openai_compat import OpenAICompatClient

__all__ = [
    "FakeLLMClient",
    "LLMClient",
    "LLMError",
    "LLMResponse",
    "Message",
    "OpenAICompatClient",
    "Tier",
    "ToolCall",
    "ToolSpec",
    "Usage",
    "create_llm_client",
]


def create_llm_client(settings: Settings) -> LLMClient:
    if settings.llm_provider == "fake":
        return FakeLLMClient()
    assert settings.llm_api_key and settings.llm_model_main  # проверено в Settings
    return OpenAICompatClient(
        api_key=settings.llm_api_key.get_secret_value(),
        base_url=settings.llm_base_url,
        models={"main": settings.llm_model_main, "fast": settings.llm_model_fast or settings.llm_model_main},
        timeout_s=settings.llm_timeout_s,
        max_retries=settings.llm_max_retries,
    )
