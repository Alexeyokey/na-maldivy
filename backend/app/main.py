"""HTTP-сервис агента. Запуск: uvicorn app.main:create_app --factory."""

import logging
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse, StreamingResponse

from app.agent import TravelAgent
from app.booking.checkout import create_checkout_router
from app.booking.checkout_service import CheckoutService
from app.chat_recovery import ChatRecovery, ChatRecoveryError, legacy_stream
from app.config import Settings
from app.llm import LLMClient, create_llm_client
from app.tools import create_tools


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    # Пусто — новая сессия; id приходит в событии done, его передают в следующих сообщениях.
    session_id: str | None = Field(default=None, max_length=64)
    # The browser keeps this ID and the original payload until done, including on retry.
    request_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    # A durable acceptance marker distinguishes a lost response from an unsent request.
    retry: bool = False


def create_app(settings: Settings | None = None, llm: LLMClient | None = None) -> FastAPI:
    settings = settings or Settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        client = llm or create_llm_client(settings)
        app.state.llm = client
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
            app.state.chat_recovery = ChatRecovery(
                ledger_path=app.state.checkout.db_path.with_name("chat-requests.sqlite3")
            )
            yield
        finally:
            try:
                recovery = getattr(app.state, "chat_recovery", None)
                if recovery is not None:
                    await recovery.aclose()
            finally:
                try:
                    if tools is not None:
                        await tools.aclose()
                finally:
                    await client.aclose()

    app = FastAPI(title="na-maldivy agent", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origin_list, allow_methods=["*"], allow_headers=["*"]
    )
    if settings.llm_provider in {"codex_local", "codex_private"}:
        # The token joins trusted server processes and never reaches the browser.
        # codex_private additionally requires owner authentication at the HTTPS edge;
        # its agent container must never publish a host port.
        assert settings.codex_local_token is not None
        local_token = settings.codex_local_token.get_secret_value()

        @app.middleware("http")
        async def personal_codex_only(request: Request, call_next: Any) -> Response:
            peer = request.client.host if request.client else ""
            supplied = request.headers.get("x-local-codex-token", "")
            allowed_peer = settings.llm_provider == "codex_private" or peer in {"127.0.0.1", "::1", "localhost"}
            if not allowed_peer or not secrets.compare_digest(supplied.encode(), local_token.encode()):
                return JSONResponse({"detail": "Доступ к личному Codex запрещён."}, 403)
            return await call_next(request)

    app.include_router(create_checkout_router())

    @app.get("/health")
    async def health() -> dict[str, str | int]:
        result: dict[str, str | int] = {"status": "ok", "llm_provider": settings.llm_provider}
        if settings.llm_provider in {"codex_local", "codex_private"}:
            result["codex_completed_calls"] = getattr(getattr(app.state, "llm", None), "completed_calls", 0)
            result["codex_model"] = settings.llm_model_main or "CLI-default"
            result["codex_reasoning_effort"] = "low"
        return result

    @app.post("/api/chat", response_class=StreamingResponse, responses={200: {"content": {"text/event-stream": {}}}})
    async def chat(body: ChatRequest, request: Request) -> StreamingResponse:
        """Поток SSE: события хода агента (см. app/agent/core.py), в конце done или error."""
        agent: TravelAgent = request.app.state.agent
        if body.request_id is None:
            if body.retry:
                raise HTTPException(status_code=422, detail="Для повтора сообщения нужен request_id.")
            stream = legacy_stream(agent.run(body.message, body.session_id))
        else:
            recovery: ChatRecovery = request.app.state.chat_recovery
            try:
                turn = recovery.get_or_start(
                    body.request_id,
                    body.message,
                    body.session_id,
                    lambda: agent.run(body.message, body.session_id),
                    retry=body.retry,
                    session_exists=body.session_id is None or agent.sessions.get(body.session_id) is not None,
                )
            except ChatRecoveryError as exc:
                raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
            stream = turn.stream()
        # Explicit response lets conflict/expired retry validation run before SSE headers.
        return StreamingResponse(
            stream,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

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
