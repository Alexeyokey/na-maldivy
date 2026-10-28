"""Личная loopback-сборка: настройки только из процесса, без чтения проектного .env."""

from fastapi import FastAPI

from app.config import Settings
from app.main import create_app as create_agent_app


def create_app() -> FastAPI:
    settings = Settings(_env_file=None)
    if settings.llm_provider != "codex_local":
        raise RuntimeError("Используйте scripts/run_personal_codex.py")
    return create_agent_app(settings)
