"""Интеграция со страховой. Сейчас — только имитация: настоящего API партнёра в демо нет.

Чтобы подключить реальную страховую, достаточно реализовать InsurerClient и передать его в ProtectionService.
"""

import uuid
from typing import Protocol

from app.protection.models import Claim, ClaimDecision, Policy, PolicyQuote


class InsurerClient(Protocol):
    name: str

    def issue(self, quote: PolicyQuote, booking_id: str) -> str: ...

    async def submit_claim(self, claim: Claim, policy: Policy) -> ClaimDecision: ...


class MockInsurer:
    """Имитация страховой: автоматически одобряет формализуемые случаи в пределах лимита.

    Формализуемый случай — тот, что подтверждается данными, а не справками: отмена или задержка рейса
    по данным авиакомпании и чеки пересборки, которые бот оформил сам.
    """

    name = "Страховая-партнёр (имитация)"

    def __init__(self) -> None:
        self.claims: dict[str, tuple[Claim, ClaimDecision]] = {}

    def issue(self, quote: PolicyQuote, booking_id: str) -> str:
        return "POL-" + uuid.uuid4().hex[:8].upper()

    async def submit_claim(self, claim: Claim, policy: Policy) -> ClaimDecision:
        claim_id = "CLM-" + uuid.uuid4().hex[:8].upper()
        approved = min(claim.amount_rub, policy.remaining_rub)
        if approved == 0:
            decision = ClaimDecision(
                claim_id=claim_id, status="rejected", approved_rub=0, comment="Лимит полиса исчерпан"
            )
        elif approved < claim.amount_rub:
            decision = ClaimDecision(
                claim_id=claim_id,
                status="partially_approved",
                approved_rub=approved,
                comment="Одобрено в пределах остатка лимита",
            )
        else:
            decision = ClaimDecision(
                claim_id=claim_id,
                status="approved",
                approved_rub=approved,
                comment="Одобрено автоматически: событие подтверждено данными авиакомпании",
            )
        self.claims[claim_id] = (claim, decision)
        return decision
