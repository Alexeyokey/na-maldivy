"""«Отпуск под защитой»: страховой продукт, при котором сбой перелёта разруливает агент, а платит страховая.

Модуль самостоятельный. Агент (app/agent) о нём не знает: защита читает уже оформленную сессию
и работает рядом. Чтобы убрать фичу целиком — удалите эту папку и строки с пометкой
«protection» в app/main.py, app/config.py и Makefile (см. docs/protection.md).
"""

from app.protection.insurer import InsurerClient, MockInsurer
from app.protection.models import Claim, ClaimDecision, Disruption, Policy, PolicyQuote, RecoveryPlan
from app.protection.service import ProtectionError, ProtectionService

__all__ = [
    "Claim",
    "ClaimDecision",
    "Disruption",
    "InsurerClient",
    "MockInsurer",
    "Policy",
    "PolicyQuote",
    "ProtectionError",
    "ProtectionService",
    "RecoveryPlan",
]
