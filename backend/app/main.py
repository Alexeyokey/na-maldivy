"""HTTP-сервис агента. Запуск: uvicorn app.main:create_app --factory."""

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, Field

from app.agent import TravelAgent
from app.booking.checkout import create_checkout_router
from app.booking.checkout_service import CheckoutService
from app.config import Settings
from app.llm import LLMClient, create_llm_client
from app.tools import create_tools


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    # Пусто — новая сессия; id приходит в событии done, его передают в следующих сообщениях.
    session_id: str | None = Field(default=None, max_length=64)


def create_app(settings: Settings | None = None, llm: LLMClient | None = None) -> FastAPI:
    settings = settings or Settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        client = llm or create_llm_client(settings)
        tools = None
        try:
            tools = create_tools(settings)
            app.state.checkout = CheckoutService(
                settings.checkout_db_path or settings.data_dir.parent / ".runtime/checkout.sqlite3"
            )
            # Fake selects rules/text templates independently from the flight data source.
            app.state.agent = TravelAgent(
                client,
                tools,
                llm_writes_text=llm is not None or settings.llm_provider != "fake",
                checkout=app.state.checkout,
            )
            yield
        finally:
            try:
                if tools is not None:
                    await tools.aclose()
            finally:
                await client.aclose()

    app = FastAPI(title="na-maldivy agent", lifespan=lifespan)
    app.include_router(create_checkout_router())
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origin_list, allow_methods=["*"], allow_headers=["*"]
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "llm_provider": settings.llm_provider}

    @app.post("/api/chat", response_class=EventSourceResponse)
    async def chat(body: ChatRequest, request: Request) -> AsyncIterator[ServerSentEvent]:
        """Поток SSE: события хода агента (см. app/agent/core.py), в конце done или error."""
        agent: TravelAgent = request.app.state.agent
        async for event in agent.run(body.message, body.session_id):
            # raw_data вместо data: кириллица остаётся читаемой в DevTools и curl, а не \uXXXX
            yield ServerSentEvent(event=event.type, raw_data=json.dumps(event.data, ensure_ascii=False))

    @app.get("/api/session/{session_id}")
    async def session_state(session_id: str, request: Request, response: Response) -> dict[str, Any]:
        """Бриф, этап и выбранный вариант — для отладки и восстановления интерфейса."""
        agent: TravelAgent = request.app.state.agent
        session = agent.sessions.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Сессия не найдена")
        response.headers["Cache-Control"] = "no-store"
        return session.snapshot()

    @app.delete("/api/session/{session_id}", status_code=204)
    async def reset_session(session_id: str, request: Request) -> None:
        request.app.state.agent.sessions.reset(session_id)

    return app
