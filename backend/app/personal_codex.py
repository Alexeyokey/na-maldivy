"""Личная сборка: настройки только из процесса, без чтения проектного .env."""

from fastapi import FastAPI

from app.config import Settings
from app.main import create_app as create_agent_app


def create_app() -> FastAPI:
    settings = Settings(_env_file=None)
    if settings.llm_provider not in {"codex_local", "codex_private"}:
        raise RuntimeError("Используйте личную сборку Codex")
    return create_agent_app(settings)
