"""Сценарий защиты: предложить полис → оформить → поймать сбой → пересобрать → подтвердить → заявка.

Клиент участвует дважды: соглашается на полис при покупке и одним «да» подтверждает пересборку.
Всё остальное — переписку с авиакомпанией, отелем, трансфером и страховой — делает бот.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from app.agent.session import Session
from app.protection import recovery
from app.protection.insurer import InsurerClient, MockInsurer
from app.protection.models import (
    INSURED_DELAY_MINUTES,
    Action,
    Claim,
    ClaimDecision,
    Disruption,
    Policy,
    PolicyQuote,
    RecoveryPlan,
)
from app.tools import run_tool

PREMIUM_SHARE = 0.04  # 4% от стоимости поездки — порядок цены туристических страховок
MIN_PREMIUM_RUB = 990
COVERAGE_SHARE = 0.6  # лимит — 60% стоимости поездки, но не меньше 50 000 ₽
MIN_COVERAGE_RUB = 50_000


class ProtectionError(Exception):
    """Ошибка сценария, понятная клиенту: текст можно показывать как есть."""


def rub(amount: int) -> str:
    return f"{amount:,}".replace(",", " ") + " ₽"


class ProtectionService:
    def __init__(self, tools: Any | None = None, insurer: InsurerClient | None = None) -> None:
        self._tools = tools
        self.insurer: InsurerClient = insurer or MockInsurer()
        self.policies: dict[str, Policy] = {}  # по session_id
        self.plans: dict[str, RecoveryPlan] = {}  # по plan_id
        self.decisions: dict[str, ClaimDecision] = {}  # по plan_id

    # ------------------------------------------------------------------ полис

    @staticmethod
    def _booked(session: Session) -> tuple[dict[str, Any], dict[str, Any]]:
        package = session.package(session.selected)
        if session.booking is None or package is None:
            raise ProtectionError("Защиту можно добавить после оформления поездки.")
        return session.booking, package

    def quote(self, session: Session) -> PolicyQuote:
        _, package = self._booked(session)
        total = int(package["total_rub"])
        return PolicyQuote(
            premium_rub=max(MIN_PREMIUM_RUB, round(total * PREMIUM_SHARE / 10) * 10),
            coverage_limit_rub=max(MIN_COVERAGE_RUB, round(total * COVERAGE_SHARE / 1000) * 1000),
            covers=[
                f"Задержка рейса больше {INSURED_DELAY_MINUTES // 60} часов или отмена — туда и обратно",
                "Новый билет, если рейс отменили",
                "Сгоревшие ночи в отеле и лишние ночи, если вылет домой сдвинулся",
                "Повторный трансфер",
                "Всю переписку с авиакомпанией, отелем и страховой ведёт бот",
            ],
            excludes=["Отказ от поездки по желанию клиента", "Опоздание на рейс по вине клиента"],
            insurer=self.insurer.name,
        )

    def buy(self, session: Session, consent_data_transfer: bool) -> Policy:
        booking, _ = self._booked(session)
        if not consent_data_transfer:
            raise ProtectionError(
                "Нужно согласие на передачу данных брони страховой — иначе бот не сможет действовать за вас."
            )
        if session.id in self.policies:
            return self.policies[session.id]
        quote = self.quote(session)
        policy = Policy(
            id=self.insurer.issue(quote, booking["id"]),
            session_id=session.id,
            booking_id=booking["id"],
            quote=quote,
            consent_data_transfer=True,
        )
        self.policies[session.id] = policy
        return policy

    # ------------------------------------------------------------------ сбой

    async def _call(self, name: str, arguments: dict[str, Any]) -> Any | None:
        try:
            return await run_tool(name, arguments, tools=self._tools)
        except Exception:  # недоступный источник — пробуем следующий день, а не падаем
            return None

    async def report(self, session: Session, disruption: Disruption) -> RecoveryPlan:
        """Бот узнал о сбое: готовит пересборку и считает, сколько покроет страховая. Ничего не покупает."""
        policy = self.policies.get(session.id)
        if policy is None:
            raise ProtectionError("У этой поездки нет полиса «Отпуск под защитой».")
        _, package = self._booked(session)
        r = await recovery.plan(package, disruption, session.brief.travelers.model_dump(), self._call)

        total = sum(c.amount_rub for c in r.costs)
        covered = min(total, policy.remaining_rub) if disruption.insured else 0
        notes = list(r.notes)
        if not disruption.insured:
            notes.append(
                f"Задержка меньше {INSURED_DELAY_MINUTES // 60} ч — это не страховой случай, "
                "но бот всё равно предупредил отель и трансфер."
            )
        plan = RecoveryPlan(
            id="PLAN-" + uuid.uuid4().hex[:8].upper(),
            session_id=session.id,
            disruption=disruption,
            status="manual" if r.new_flight is None else "awaiting_confirmation",
            insured=disruption.insured,
            old_flight=r.old_flight,
            new_flight=r.new_flight,
            hotel_change=r.hotel_change,
            transfer_change=r.transfer_change,
            actions=r.actions,
            costs=r.costs,
            covered_rub=covered,
            client_pays_rub=total - covered,
            notes=notes,
        )
        self.plans[plan.id] = plan
        return plan

    async def confirm(self, session: Session, plan_id: str) -> tuple[RecoveryPlan, ClaimDecision | None]:
        """Клиент сказал «да»: бот переоформляет поездку и сам отправляет заявку в страховую."""
        plan = self.plans.get(plan_id)
        if plan is None or plan.session_id != session.id:
            raise ProtectionError("План пересборки не найден.")
        if plan.status == "confirmed":
            return plan, self.decisions.get(plan_id)
        if plan.status != "awaiting_confirmation":
            raise ProtectionError("Этот случай передан оператору — автоматически переоформить нельзя.")
        policy = self.policies[session.id]

        plan.status = "confirmed"
        decision = None
        if plan.insured and plan.costs:
            claim = self._claim(plan, policy)
            decision = await self.insurer.submit_claim(claim, policy)
            policy.used_rub += decision.approved_rub
            self.decisions[plan_id] = decision
            plan.actions.append(
                Action(
                    who="Страховая",
                    text=f"Отправил заявку {decision.claim_id} на {rub(claim.amount_rub)}: "
                    f"документов в пакете — {len(claim.documents)}, собраны автоматически",
                )
            )
        return plan, decision

    def _claim(self, plan: RecoveryPlan, policy: Policy) -> Claim:
        d = plan.disruption
        event = "Отмена рейса" if d.kind == "cancel" else f"Задержка рейса на {d.delay_minutes // 60} ч"
        documents: list[dict[str, Any]] = [
            {"type": "policy", "id": policy.id},
            {"type": "booking", "id": policy.booking_id},
            {"type": "airline_notice", "flight": plan.old_flight["id"], "source": d.source},
        ]
        if plan.new_flight and d.kind == "cancel":
            documents.append({"type": "new_ticket", "flight": plan.new_flight["id"]})
        if plan.hotel_change:
            documents.append({"type": "hotel_confirmation", **plan.hotel_change})
        return Claim(
            policy_id=policy.id,
            booking_id=policy.booking_id,
            event=event,
            occurred_at=datetime.now(UTC),
            items=plan.costs,
            documents=documents,
        )

    # ------------------------------------------------------------------ тексты

    @staticmethod
    def offer_text(quote: PolicyQuote) -> str:
        lines = [
            f"Добавить «{quote.product}» за {rub(quote.premium_rub)}? "
            f"Лимит покрытия — {rub(quote.coverage_limit_rub)}.",
            "Если рейс задержат больше чем на 3 часа или отменят, я сам пересоберу поездку, а заплатит страховая:",
            *[f"  • {c}" for c in quote.covers],
        ]
        return "\n".join(lines)

    @staticmethod
    def plan_text(plan: RecoveryPlan) -> str:
        lines = ["Что случилось и что я уже подготовил:"]
        lines += [f"  • {a.who}: {a.text}" for a in plan.actions]
        if plan.costs:
            lines.append("Расходы на пересборку:")
            lines += [f"  • {c.label} — {rub(c.amount_rub)}" for c in plan.costs]
            lines.append(f"Покрывает страховка: {rub(plan.covered_rub)}. Вам доплатить: {rub(plan.client_pays_rub)}.")
        lines += [f"Примечание: {n}" for n in plan.notes]
        if plan.status == "awaiting_confirmation":
            lines.append("Переоформляю? Ответьте «да» — дальше я всё сделаю сам.")
        return "\n".join(lines)

    @staticmethod
    def notification_text(plan: RecoveryPlan, decision: ClaimDecision | None) -> str:
        new = plan.new_flight or {}
        when = datetime.fromisoformat(new["departure_at"]).strftime("%d.%m в %H:%M") if new else "—"
        text = f"Готово: вы вылетаете {when}. Отель и трансфер уже в курсе."
        if decision is not None:
            pays = plan.client_pays_rub
            extra = f"ваша доплата — {rub(pays)}" if pays else "вы ничего не доплачиваете"
            text += f" Заявка {decision.claim_id}: одобрено {rub(decision.approved_rub)}, {extra}."
        return text
