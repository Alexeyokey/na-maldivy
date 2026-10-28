"""HTTP adapter for DEMO checkout. This is a local demo API, not an approved O1 contract."""

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.agent.session import Session, Stage
from app.booking.checkout_service import CheckoutError, CheckoutService, DemoTraveler


class CheckoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    session_id: str = Field(min_length=1, max_length=64)


class CartRequest(CheckoutRequest):
    option: int = Field(ge=1, le=20)
    version: int = Field(ge=0)


class TravelersRequest(CheckoutRequest):
    travelers: list[DemoTraveler] = Field(min_length=1, max_length=20)


class PaymentRequest(CheckoutRequest):
    outcome: Literal["approved", "declined"]
    idempotency_key: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    acknowledge_demo: bool
    acknowledge_unpriced: bool


def create_checkout_router() -> APIRouter:
    def private_response(response: Response) -> None:
        response.headers["Cache-Control"] = "no-store"

    router = APIRouter(prefix="/api/checkout", tags=["demo-checkout"], dependencies=[Depends(private_response)])

    def service(request: Request) -> CheckoutService:
        return request.app.state.checkout

    def session(request: Request, session_id: str) -> Session:
        found = request.app.state.agent.sessions.get(session_id)
        if found is None:
            raise HTTPException(404, "Сессия не найдена; повторите поиск")
        return found

    def invoke(call, *args, **kwargs):
        try:
            return call(*args, **kwargs)
        except CheckoutError as exc:
            raise HTTPException(exc.status_code, str(exc)) from None

    @router.post("/carts", status_code=201)
    async def create_cart(body: CartRequest, request: Request) -> dict:
        lock = request.app.state.agent._locks.setdefault(body.session_id, asyncio.Lock())
        async with lock:
            current = session(request, body.session_id)
            result = invoke(service(request).create_cart, current, option=body.option, expected_version=body.version)
            current.selected = body.option
            return result

    @router.get("/carts/{cart_id}")
    def get_cart(cart_id: str, request: Request, x_session_id: str = Header()) -> dict:
        return invoke(service(request).get_cart, cart_id, x_session_id)

    @router.put("/carts/{cart_id}/travelers")
    async def set_travelers(cart_id: str, body: TravelersRequest, request: Request) -> dict:
        lock = request.app.state.agent._locks.setdefault(body.session_id, asyncio.Lock())
        async with lock:
            return invoke(service(request).set_travelers, cart_id, session(request, body.session_id), body.travelers)

    @router.post("/carts/{cart_id}/pay")
    async def pay(cart_id: str, body: PaymentRequest, request: Request) -> dict:
        lock = request.app.state.agent._locks.setdefault(body.session_id, asyncio.Lock())
        async with lock:
            current = session(request, body.session_id)
            result = invoke(
                service(request).pay,
                cart_id,
                current,
                outcome=body.outcome,
                idempotency_key=body.idempotency_key,
                acknowledge_demo=body.acknowledge_demo,
                acknowledge_unpriced=body.acknowledge_unpriced,
            )
            if result["status"] == "paid_demo" and service(request).cart_is_current(cart_id, current):
                current.stage = Stage.BOOKED
                current.booking = {
                    "id": result["order_id"],
                    "order_id": result["order_id"],
                    "status": result["status"],
                    "cart_id": cart_id,
                    "hotel": result["package"]["hotel"]["name"],
                    "total_rub": result["package"]["total_rub"],
                    "option": current.selected,
                    "is_simulated": True,
                }
            return result

    @router.get("/orders/{order_id}")
    def get_order(order_id: str, request: Request, x_session_id: str = Header()) -> dict:
        return invoke(service(request).get_order, order_id, x_session_id)

    @router.get("/orders/{order_id}/voucher")
    def voucher(order_id: str, request: Request, x_session_id: str = Header()) -> Response:
        body = invoke(service(request).voucher, order_id, x_session_id)
        return Response(
            body,
            media_type="text/html",
            headers={
                "Content-Disposition": 'attachment; filename="demo-voucher.html"',
                "Cache-Control": "no-store",
            },
        )

    @router.get("/orders/{order_id}/calendar.ics")
    def calendar(order_id: str, request: Request, x_session_id: str = Header()) -> Response:
        body = invoke(service(request).calendar, order_id, x_session_id)
        return Response(
            body,
            media_type="text/calendar",
            headers={
                "Content-Disposition": 'attachment; filename="demo-trip.ics"',
                "Cache-Control": "no-store",
            },
        )

    return router
