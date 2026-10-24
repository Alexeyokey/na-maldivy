"""HTTP-сервис агента. Запуск: uvicorn app.main:create_app --factory."""

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, Field

from app.agent import EmptyAgent
from app.config import Settings
from app.llm import LLMClient, create_llm_client


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


def create_app(settings: Settings | None = None, llm: LLMClient | None = None) -> FastAPI:
    settings = settings or Settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        client = llm or create_llm_client(settings)
        app.state.agent = EmptyAgent(client)
        try:
            yield
        finally:
            await client.aclose()

    app = FastAPI(title="na-maldivy agent", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origin_list, allow_methods=["*"], allow_headers=["*"]
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "llm_provider": settings.llm_provider}

    @app.post("/api/chat", response_class=EventSourceResponse)
    async def chat(body: ChatRequest, request: Request) -> AsyncIterator[ServerSentEvent]:
        """Поток SSE: события delta с кусками текста, в конце done или error."""
        agent: EmptyAgent = request.app.state.agent
        async for event in agent.run(body.message):
            # raw_data вместо data: кириллица остаётся читаемой в DevTools и curl, а не \uXXXX
            yield ServerSentEvent(event=event.type, raw_data=json.dumps(event.data, ensure_ascii=False))

    return app
