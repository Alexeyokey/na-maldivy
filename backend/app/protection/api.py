"""HTTP-эндпоинты защиты. Подключаются в app/main.py, только если PROTECTION_ENABLED=true."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.agent.session import Session
from app.protection.models import Disruption
from app.protection.service import ProtectionError, ProtectionService


class BuyRequest(BaseModel):
    consent_data_transfer: bool


class ConfirmRequest(BaseModel):
    plan_id: str


def _service(request: Request) -> ProtectionService:
    state = request.app.state
    if getattr(state, "protection", None) is None:
        # Те же инструменты, что у агента: пересборка ищет рейсы в том же источнике данных.
        state.protection = ProtectionService(tools=getattr(state.agent, "_tools", None))
    return state.protection


def _session(request: Request, session_id: str) -> Session:
    session = request.app.state.agent.sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Сессия не найдена")
    return session


def create_protection_router() -> APIRouter:
    router = APIRouter(prefix="/api/protection", tags=["protection"])

    @router.get("/{session_id}/offer")
    async def offer(session_id: str, request: Request) -> dict[str, Any]:
        """Предложение полиса для оформленной поездки."""
        try:
            quote = _service(request).quote(_session(request, session_id))
        except ProtectionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"quote": quote.model_dump(), "text": ProtectionService.offer_text(quote)}

    @router.post("/{session_id}/policy")
    async def buy(session_id: str, body: BuyRequest, request: Request) -> dict[str, Any]:
        try:
            policy = _service(request).buy(_session(request, session_id), body.consent_data_transfer)
        except ProtectionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return policy.model_dump()

    @router.post("/{session_id}/disruption")
    async def disruption(session_id: str, body: Disruption, request: Request) -> dict[str, Any]:
        """Сбой рейса (в демо его присылают вручную). Возвращает план, который ждёт подтверждения клиента."""
        try:
            plan = await _service(request).report(_session(request, session_id), body)
        except ProtectionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"plan": plan.model_dump(mode="json"), "text": ProtectionService.plan_text(plan)}

    @router.post("/{session_id}/confirm")
    async def confirm(session_id: str, body: ConfirmRequest, request: Request) -> dict[str, Any]:
        try:
            plan, decision = await _service(request).confirm(_session(request, session_id), body.plan_id)
        except ProtectionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "plan": plan.model_dump(mode="json"),
            "claim": decision.model_dump() if decision else None,
            "notification": ProtectionService.notification_text(plan, decision),
        }

    return router
