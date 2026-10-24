"""Настройки сервиса: переменные окружения и .env (образец — .env.example в корне репозитория)."""

from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ../.env — при запуске из backend/, .env — из корня; в docker переменные приходят из compose
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), env_ignore_empty=True, extra="ignore")

    log_level: str = "INFO"
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    llm_provider: Literal["fake", "openai"] = "fake"
    llm_base_url: str | None = None
    llm_api_key: SecretStr | None = None
    llm_model_main: str | None = None
    llm_model_fast: str | None = None  # если не задана, берётся основная
    llm_timeout_s: float = 60.0
    llm_max_retries: int = 2

    @model_validator(mode="after")
    def _check_llm(self) -> "Settings":
        if self.llm_provider == "openai":
            required = {"LLM_API_KEY": self.llm_api_key, "LLM_MODEL_MAIN": self.llm_model_main}
            missing = [name for name, value in required.items() if not value]
            if missing:
                raise ValueError("Для LLM_PROVIDER=openai задайте " + ", ".join(missing))
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]
