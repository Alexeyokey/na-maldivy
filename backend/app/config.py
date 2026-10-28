"""Настройки сервиса: переменные окружения и .env (образец — .env.example в корне репозитория)."""

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.data_paths import resolve_data_dir


class Settings(BaseSettings):
    # ../.env — при запуске из backend/, .env — из корня; в docker переменные приходят из compose
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"), env_ignore_empty=True, extra="ignore", hide_input_in_errors=True
    )

    log_level: str = "INFO"
    cors_origins: str = "http://localhost:5173,http://localhost:3000"
    tools_mode: Literal["stub", "real"] = "stub"
    data_dir: Path = Field(default_factory=resolve_data_dir)
    flights_mode: Literal["synthetic", "aviasales", "demo"] = "synthetic"
    aviasales_api_token: SecretStr | None = Field(default=None, repr=False)
    aviasales_cache_dir: Path | None = None
    trains_mode: Literal["synthetic", "yandex"] = "synthetic"
    yandex_rasp_api_key: SecretStr | None = Field(default=None, repr=False)
    yandex_cache_dir: Path | None = None
    checkout_db_path: Path | None = None

    llm_provider: Literal["fake", "openai", "codex_local", "codex_private"] = "fake"
    llm_base_url: str | None = None
    llm_api_key: SecretStr | None = None
    llm_model_main: str | None = None
    llm_model_fast: str | None = None  # если не задана, берётся основная
    llm_timeout_s: float = 60.0
    llm_max_retries: int = 2
    codex_binary: str = "codex"
    codex_local_token: SecretStr | None = Field(default=None, repr=False)

    cache_mode: Literal["record", "replay", "live"] = "replay"

    @model_validator(mode="after")
    def _check_llm(self) -> "Settings":
        if self.flights_mode == "demo":
            if self.tools_mode != "real":
                raise ValueError("FLIGHTS_MODE=demo requires TOOLS_MODE=real")
            if self.cache_mode != "replay":
                raise ValueError("FLIGHTS_MODE=demo is offline-only and requires CACHE_MODE=replay")
        if self.flights_mode == "aviasales":
            if self.tools_mode != "real":
                raise ValueError("FLIGHTS_MODE=aviasales requires TOOLS_MODE=real")
            if self.cache_mode != "replay" and not self.aviasales_api_token:
                raise ValueError("AVIASALES_API_TOKEN is required for record/live; replay works without a token")
        if self.trains_mode == "yandex":
            if self.tools_mode != "real":
                raise ValueError("TRAINS_MODE=yandex requires TOOLS_MODE=real")
            if self.cache_mode != "replay" and not self.yandex_rasp_api_key:
                raise ValueError("YANDEX_RASP_API_KEY is required for record/live; replay works without a key")
        if self.llm_provider == "openai":
            required = {"LLM_API_KEY": self.llm_api_key, "LLM_MODEL_MAIN": self.llm_model_main}
            missing = [name for name, value in required.items() if not value]
            if missing:
                raise ValueError("Для LLM_PROVIDER=openai задайте " + ", ".join(missing))
        if self.llm_provider == "codex_local" and (
            self.codex_local_token is None or len(self.codex_local_token.get_secret_value()) < 32
        ):
            raise ValueError("Для личного Codex используйте scripts/run_personal_codex.py")
        if self.llm_provider == "codex_private":
            if self.codex_local_token is None or len(self.codex_local_token.get_secret_value()) < 32:
                raise ValueError("Для приватного Codex задайте CODEX_LOCAL_TOKEN длиной не менее 32 символов")
            if any(model not in {None, "gpt-6-luna"} for model in (self.llm_model_main, self.llm_model_fast)):
                raise ValueError("Личный сервер использует только быструю модель gpt-6-luna")
            self.llm_model_main = self.llm_model_fast = "gpt-6-luna"
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]
