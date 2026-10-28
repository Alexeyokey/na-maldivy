"""«Отпуск под защитой»: полис → сбой рейса → пересборка → одно «да» → заявка в страховую."""

from datetime import date, datetime

import pytest

from app.agent import TravelAgent
from app.llm import FakeLLMClient
from app.protection import Disruption, ProtectionError, ProtectionService
from app.tools import TOOLS

TODAY = date(2026, 10, 27)


async def booked_session() -> tuple[TravelAgent, str]:
    agent = TravelAgent(FakeLLMClient(), TOOLS, llm_writes_text=False, today=lambda: TODAY)
    sid = None
    for message in ("из Москвы в Анталью в ноябре на неделю вдвоём", "беру первый", "да, оформляй"):
        events = [e async for e in agent.run(message, sid)]
        sid = events[-1].data["session_id"]
    assert agent.sessions.get(sid).booking is not None
    return agent, sid


async def test_no_policy_before_booking() -> None:
    agent = TravelAgent(FakeLLMClient(), TOOLS, llm_writes_text=False, today=lambda: TODAY)
    events = [e async for e in agent.run("из Москвы в Анталью в ноябре на неделю вдвоём")]
    session = agent.sessions.get(events[-1].data["session_id"])
    with pytest.raises(ProtectionError):
        ProtectionService(TOOLS).quote(session)


async def test_policy_needs_consent_and_is_priced_from_trip() -> None:
    agent, sid = await booked_session()
    session = agent.sessions.get(sid)
    service = ProtectionService(TOOLS)
    quote = service.quote(session)
    total = session.package(session.selected)["total_rub"]
    assert 0 < quote.premium_rub < total and quote.coverage_limit_rub >= 50_000
    with pytest.raises(ProtectionError):
        service.buy(session, consent_data_transfer=False)
    policy = service.buy(session, consent_data_transfer=True)
    assert service.buy(session, consent_data_transfer=True).id == policy.id  # повторная покупка не дублирует


async def test_cancelled_outbound_is_rebuilt_and_paid_by_insurer() -> None:
    agent, sid = await booked_session()
    session = agent.sessions.get(sid)
    service = ProtectionService(TOOLS)
    with pytest.raises(ProtectionError):  # без полиса защита не работает
        await service.report(session, Disruption(kind="cancel"))
    service.buy(session, consent_data_transfer=True)

    plan = await service.report(session, Disruption(leg="outbound", kind="cancel"))
    assert plan.status == "awaiting_confirmation" and plan.insured
    old, new = plan.old_flight, plan.new_flight
    assert new and (new["from"], new["to"]) == (old["from"], old["to"])
    assert datetime.fromisoformat(new["departure_at"]) > datetime.fromisoformat(old["departure_at"])
    assert any(c.code == "new_ticket" for c in plan.costs)
    assert plan.covered_rub + plan.client_pays_rub == plan.total_rub
    assert "Переоформляю?" in ProtectionService.plan_text(plan)
    assert not service.decisions  # до «да» в страховую ничего не уходит

    plan, decision = await service.confirm(session, plan.id)
    assert plan.status == "confirmed" and decision is not None
    assert decision.approved_rub == plan.covered_rub
    assert service.policies[sid].used_rub == decision.approved_rub
    assert any(a.who == "Страховая" for a in plan.actions)
    again, same = await service.confirm(session, plan.id)  # повторное «да» не шлёт вторую заявку
    assert same is decision and len(service.insurer.claims) == 1


async def test_delayed_return_adds_hotel_night() -> None:
    agent, sid = await booked_session()
    session = agent.sessions.get(sid)
    service = ProtectionService(TOOLS)
    service.buy(session, consent_data_transfer=True)
    plan = await service.report(session, Disruption(leg="inbound", kind="delay", delay_minutes=20 * 60))
    assert plan.insured and plan.hotel_change and plan.hotel_change["extra_nights"] >= 1
    assert [c.code for c in plan.costs] == ["extra_nights"]


async def test_short_delay_is_not_insured_but_still_handled() -> None:
    agent, sid = await booked_session()
    session = agent.sessions.get(sid)
    service = ProtectionService(TOOLS)
    service.buy(session, consent_data_transfer=True)
    plan = await service.report(session, Disruption(kind="delay", delay_minutes=90))
    assert not plan.insured and plan.covered_rub == 0
    _, decision = await service.confirm(session, plan.id)
    assert decision is None  # не страховой случай — заявку не отправляем


async def test_coverage_limit_caps_payout() -> None:
    agent, sid = await booked_session()
    session = agent.sessions.get(sid)
    service = ProtectionService(TOOLS)
    policy = service.buy(session, consent_data_transfer=True)
    policy.used_rub = policy.quote.coverage_limit_rub - 1_000
    plan = await service.report(session, Disruption(kind="cancel"))
    assert plan.covered_rub <= 1_000 and plan.client_pays_rub == plan.total_rub - plan.covered_rub


def test_api_is_off_by_default_and_works_when_enabled() -> None:
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app

    with TestClient(create_app(Settings(_env_file=None, llm_provider="fake"))) as client:
        assert client.get("/api/protection/any/offer").status_code == 404  # фича выключена — эндпоинтов нет

    settings = Settings(_env_file=None, llm_provider="fake", protection_enabled=True)
    with TestClient(create_app(settings)) as client:
        sid = None
        for message in ("из Москвы в Анталью в ноябре на неделю вдвоём", "беру первый", "да, оформляй"):
            body = {"message": message} | ({"session_id": sid} if sid else {})
            text = client.post("/api/chat", json=body).text
            sid = sid or text.split('"session_id": "')[1].split('"')[0]

        offer = client.get(f"/api/protection/{sid}/offer").json()
        assert offer["quote"]["premium_rub"] > 0 and "Отпуск под защитой" in offer["text"]
        assert client.post(f"/api/protection/{sid}/policy", json={"consent_data_transfer": False}).status_code == 409
        assert client.post(f"/api/protection/{sid}/policy", json={"consent_data_transfer": True}).status_code == 200

        plan = client.post(f"/api/protection/{sid}/disruption", json={"kind": "cancel"}).json()["plan"]
        assert plan["status"] == "awaiting_confirmation"
        done = client.post(f"/api/protection/{sid}/confirm", json={"plan_id": plan["id"]}).json()
        assert done["plan"]["status"] == "confirmed" and done["claim"]["status"] == "approved"
        assert "вылетаете" in done["notification"]
