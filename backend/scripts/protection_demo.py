"""Демо «Отпуск под защитой» в терминале: make protection-demo.

Без сети и ключей: агент оформляет поездку на синтетических данных, к ней добавляется полис,
потом «авиакомпания» отменяет рейс — и бот пересобирает поездку, а страховая платит.
Флаги: --delay N (задержка на N часов вместо отмены), --inbound (сбой на обратном рейсе).
"""

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent import TravelAgent  # noqa: E402
from app.llm import FakeLLMClient  # noqa: E402
from app.protection import Disruption, ProtectionService  # noqa: E402
from app.tools import TOOLS  # noqa: E402

BOLD, GREY, GREEN, RED, RESET = "\033[1m", "\033[90m", "\033[32m", "\033[31m", "\033[0m"


def say(who: str, text: str, colour: str = "") -> None:
    print(f"{BOLD}{colour}{who}:{RESET} {text}\n")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delay", type=int, default=0, help="задержка рейса в часах (по умолчанию — отмена)")
    parser.add_argument("--inbound", action="store_true", help="сбой на обратном рейсе")
    args = parser.parse_args()

    agent = TravelAgent(FakeLLMClient(), TOOLS, llm_writes_text=False, today=lambda: date(2026, 10, 27))
    sid = None
    print(f"{GREY}— 1. Обычная покупка поездки —{RESET}\n")
    for message in ("из Москвы в Анталью в ноябре на неделю вдвоём", "беру первый", "да, оформляй"):
        say("Клиент", message)
        events = [e async for e in agent.run(message, sid)]
        sid = events[-1].data["session_id"]
        reply = "".join(e.data["text"] for e in events if e.type == "delta").strip()
        say("Агент", reply.split("\n")[0] + (" …" if "\n" in reply else ""))
    session = agent.sessions.get(sid)

    service = ProtectionService(TOOLS)
    print(f"{GREY}— 2. Предложение защиты при покупке —{RESET}\n")
    say("Агент", service.offer_text(service.quote(session)))
    say("Клиент", "да, добавь (согласна на передачу данных брони страховой)")
    policy = service.buy(session, consent_data_transfer=True)
    say("Агент", f"Полис {policy.id} оформлен. Если с рейсом что-то случится — я напишу первым.", GREEN)

    disruption = Disruption(
        leg="inbound" if args.inbound else "outbound",
        kind="delay" if args.delay else "cancel",
        delay_minutes=args.delay * 60,
    )
    what = f"задержка на {args.delay} ч" if args.delay else "отмена"
    leg = "обратного рейса" if args.inbound else "рейса туда"
    print(f"{GREY}— 3. Авиакомпания сообщает: {what} {leg} —{RESET}\n")
    plan = await service.report(session, disruption)
    say("Агент", service.plan_text(plan), RED)

    if plan.status == "awaiting_confirmation":
        say("Клиент", "да")
        plan, decision = await service.confirm(session, plan.id)
        print(f"{GREY}— 4. Уведомление клиенту —{RESET}\n")
        say("Агент", service.notification_text(plan, decision), GREEN)
        print(f"{GREY}Таймлайн действий бота:{RESET}")
        for a in plan.actions:
            print(f"  {GREY}•{RESET} {a.who}: {a.text}")
        print(f"\n{GREY}Страховая и бронирования в демо — имитация, реальных платежей нет.{RESET}")


if __name__ == "__main__":
    asyncio.run(main())
