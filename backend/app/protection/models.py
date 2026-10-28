"""Данные защиты: полис, сбой, план пересборки, заявка в страховую."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

# Задержка, с которой сбой считается страховым случаем. Так устроены продукты рынка (например, «Вояж»
# у ЕВРОИНС): страховой случай — задержка рейса больше 3 часов или отмена.
INSURED_DELAY_MINUTES = 180


class PolicyQuote(BaseModel):
    product: str = "Отпуск под защитой"
    premium_rub: int
    coverage_limit_rub: int
    covers: list[str]
    excludes: list[str]
    insurer: str
    is_simulated: bool = True


class Policy(BaseModel):
    id: str
    session_id: str
    booking_id: str
    quote: PolicyQuote
    # Согласие на передачу данных брони страховой — без него бот не может действовать за клиента.
    consent_data_transfer: bool
    used_rub: int = 0

    @property
    def remaining_rub(self) -> int:
        return max(0, self.quote.coverage_limit_rub - self.used_rub)


class Disruption(BaseModel):
    """Сбой перелёта. В проде приходит от авиакомпании или сервиса статусов рейсов, в демо — вручную."""

    leg: Literal["outbound", "inbound"] = "outbound"
    kind: Literal["delay", "cancel"] = "cancel"
    delay_minutes: int = Field(default=0, ge=0, le=7 * 24 * 60)
    source: str = "Уведомление авиакомпании (демо)"

    @property
    def insured(self) -> bool:
        return self.kind == "cancel" or self.delay_minutes >= INSURED_DELAY_MINUTES


class CostItem(BaseModel):
    code: str
    label: str
    amount_rub: int


class Action(BaseModel):
    """Что бот сделал или сделает за клиента — это и есть «таймлайн спасения отпуска»."""

    who: str  # авиакомпания, отель, трансфер, страховая
    text: str


class RecoveryPlan(BaseModel):
    id: str
    session_id: str
    disruption: Disruption
    status: Literal["awaiting_confirmation", "confirmed", "manual"] = "awaiting_confirmation"
    insured: bool
    old_flight: dict[str, Any]
    new_flight: dict[str, Any] | None
    hotel_change: dict[str, Any] | None = None
    transfer_change: dict[str, Any] | None = None
    actions: list[Action]
    costs: list[CostItem]
    covered_rub: int
    client_pays_rub: int
    notes: list[str] = Field(default_factory=list)

    @property
    def total_rub(self) -> int:
        return sum(c.amount_rub for c in self.costs)


class Claim(BaseModel):
    """Пакет для страховой: бот собирает его сам, клиент ничего не заполняет."""

    policy_id: str
    booking_id: str
    event: str
    occurred_at: datetime
    items: list[CostItem]
    documents: list[dict[str, Any]]
    # Кто уже оплатил пересборку и кому страховая возмещает: клиент не платит из своего кармана.
    payer: str = "Островок (предоплата пересборки)"
    is_simulated: bool = True

    @property
    def amount_rub(self) -> int:
        return sum(i.amount_rub for i in self.items)


class ClaimDecision(BaseModel):
    claim_id: str
    status: Literal["approved", "partially_approved", "rejected", "in_review"]
    approved_rub: int
    comment: str
    is_simulated: bool = True
