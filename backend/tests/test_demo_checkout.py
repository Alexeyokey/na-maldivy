"""Durable demo checkout: server snapshot, blocked unsafe carts and no real payment data."""

import asyncio
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent.brief import TripBrief
from app.agent.session import Session, SessionStore, Stage
from app.booking.checkout import create_checkout_router
from app.booking.checkout_service import CheckoutError, CheckoutService, DemoTraveler


@pytest.fixture
def trip() -> Session:
    return Session(
        id="checkout-test-session",
        version=2,
        selected=1,
        stage=Stage.AWAITING_CONFIRMATION,
        brief=TripBrief(origin="MOW", destination="MLE", start_date="2026-11-18", adults=2),
        packages=[
            {
                "index": 1,
                "kind": "package",
                "can_confirm": True,
                "validation_status": "checked",
                "issues": [],
                "price_scope": "round_trip_flights_baggage_hotel_arrival_transfer",
                "unpriced_components": ["return_transfer", "visa_and_other_required_extras"],
                "total_rub": 125000,
                "known_total_rub": 125000,
                "outbound": {
                    "id": "out",
                    "from": "SVO",
                    "to": "MLE",
                    "carrier": "DEMO",
                    "departure_at": "2026-11-18T22:30:00+03:00",
                    "arrival_at": "2026-11-19T08:00:00+05:00",
                    "price_rub": 30000,
                    "baggage_price_rub": 4000,
                },
                "inbound": {
                    "id": "back",
                    "from": "MLE",
                    "to": "SVO",
                    "carrier": "DEMO",
                    "departure_at": "2026-11-26T01:00:00+05:00",
                    "arrival_at": "2026-11-26T07:00:00+03:00",
                    "price_rub": 35000,
                    "baggage_price_rub": 4000,
                },
                "hotel": {
                    "hotel_id": "demo-hotel",
                    "name": "Hotel <Ocean>; Русский",
                    "check_in": "2026-11-19",
                    "check_out": "2026-11-26",
                    "price_rub": 50000,
                    "pets_allowed": None,
                    "adults_only": False,
                },
                "transfer": {"id": "boat", "price_rub": 2000},
            }
        ],
    )


@pytest.fixture
def service(tmp_path: Path) -> CheckoutService:
    return CheckoutService(tmp_path / "private" / "orders.sqlite3")


def travelers() -> list[DemoTraveler]:
    return [
        DemoTraveler(full_name="Demo Adult One", birth_date="1990-01-01"),
        DemoTraveler(full_name="Demo Adult Two", birth_date="1991-02-01"),
    ]


def ready(service: CheckoutService, trip: Session) -> str:
    cart = service.create_cart(trip, option=1, expected_version=trip.version)
    service.set_travelers(cart["cart_id"], trip, travelers())
    return cart["cart_id"]


def pay(service: CheckoutService, trip: Session, cart_id: str, key: str = "approval-001", outcome: str = "approved"):
    return service.pay(
        cart_id, trip, outcome=outcome, idempotency_key=key, acknowledge_demo=True, acknowledge_unpriced=True
    )


def test_checkout_snapshot_restart_idempotency_and_private_files(service: CheckoutService, trip: Session) -> None:
    cart_id = ready(service, trip)
    order = pay(service, trip, cart_id)
    assert order["status"] == "paid_demo" and order["is_simulated"] is True
    assert "билеты не выписаны" in order["notice"]
    assert pay(service, trip, cart_id) == order
    assert pay(service, trip, cart_id, "approval-another") == order
    trip.packages[0]["hotel"]["name"] = "changed after payment"
    restored = CheckoutService(service.db_path)
    assert restored.get_order(order["order_id"], trip.id)["package"]["hotel"]["name"] == "Hotel <Ocean>; Русский"
    assert restored.db_path.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(restored.db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 1
    with pytest.raises(CheckoutError, match="не найден"):
        restored.get_order(order["order_id"], "wrong-session")


def test_decline_creates_no_order_then_retry_success(service: CheckoutService, trip: Session) -> None:
    cart_id = ready(service, trip)
    declined = pay(service, trip, cart_id, "decline-001", "declined")
    assert declined["status"] == "payment_declined" and "order_id" not in declined
    assert pay(service, trip, cart_id, "decline-001", "declined") == declined
    with sqlite3.connect(service.db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0
    with pytest.raises(CheckoutError, match="другим исходом"):
        pay(service, trip, cart_id, "decline-001", "approved")
    assert pay(service, trip, cart_id)["status"] == "paid_demo"


def test_new_key_after_paid_order_is_reserved_and_cannot_change_outcome(
    service: CheckoutService, trip: Session
) -> None:
    cart_id = ready(service, trip)
    initial = pay(service, trip, cart_id, "initial-approval-001", "approved")
    assert pay(service, trip, cart_id, "second-approval-001", "approved") == initial
    assert pay(service, trip, cart_id, "second-approval-001", "approved") == initial
    with pytest.raises(CheckoutError, match="другим исходом"):
        pay(service, trip, cart_id, "second-approval-001", "declined")
    with sqlite3.connect(service.db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM payment_attempts").fetchone()[0] == 2


def test_checkout_service_imports_in_fresh_process_without_agent_import_cycle() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.booking.checkout_service import CheckoutError, CheckoutService, DemoTraveler; print('import-ok')",
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "import-ok"


def test_concurrent_payment_creates_one_order(service: CheckoutService, trip: Session) -> None:
    cart_id = ready(service, trip)
    with ThreadPoolExecutor(max_workers=4) as pool:
        orders = list(pool.map(lambda index: pay(service, trip, cart_id, f"parallel-{index}"), range(4)))
    assert len({order["order_id"] for order in orders}) == 1


@pytest.mark.parametrize("mutation", ["stale", "version", "unknown", "critical", "price", "naive", "pets"])
def test_unsafe_or_stale_package_cannot_create_cart(service: CheckoutService, trip: Session, mutation: str) -> None:
    if mutation == "stale":
        trip.stale = True
    elif mutation == "version":
        trip.version += 1
    elif mutation == "unknown":
        trip.packages[0]["can_confirm"] = None
    elif mutation == "critical":
        trip.packages[0]["issues"] = [{"severity": "critical"}]
    elif mutation == "price":
        trip.packages[0]["total_rub"] += 1
    elif mutation == "naive":
        trip.packages[0]["inbound"]["departure_at"] = "2026-11-26T01:00:00"
    elif mutation == "pets":
        trip.brief.pets = 1
    with pytest.raises(CheckoutError):
        service.create_cart(trip, option=1, expected_version=2)


def test_selection_changes_block_payment_and_names_require_matching_group(
    service: CheckoutService, trip: Session
) -> None:
    cart_id = service.create_cart(trip, option=1, expected_version=trip.version)["cart_id"]
    with pytest.raises(CheckoutError, match="Сначала заполните"):
        pay(service, trip, cart_id)
    with pytest.raises(CheckoutError, match="Состав путешественников"):
        service.set_travelers(cart_id, trip, travelers()[:1])
    service.set_travelers(cart_id, trip, travelers())
    trip.selected = 2
    with pytest.raises(CheckoutError, match="Вариант изменился"):
        pay(service, trip, cart_id)
    trip.selected = 1
    trip.packages[0]["hotel"]["name"] = "different hotel without version bump"
    with pytest.raises(CheckoutError, match="Вариант изменился"):
        pay(service, trip, cart_id)


def test_child_age_uses_departure_day_and_cart_is_idempotent(service: CheckoutService, trip: Session) -> None:
    trip.brief.adults = 1
    trip.brief.children_ages = [5]
    first = service.create_cart(trip, option=1, expected_version=trip.version)
    assert service.create_cart(trip, option=1, expected_version=trip.version) == first
    family = [travelers()[0], DemoTraveler(full_name="Demo Child", birth_date="2021-11-19")]
    with pytest.raises(CheckoutError, match="Состав путешественников"):
        service.set_travelers(first["cart_id"], trip, family)  # turns 5 only after departure
    family[1] = DemoTraveler(full_name="Demo Child", birth_date="2021-11-18")
    assert service.set_travelers(first["cart_id"], trip, family)["status"] == "ready_for_demo_payment"


def test_voucher_escapes_html_and_calendar_keeps_utc_day_changes(service: CheckoutService, trip: Session) -> None:
    order = pay(service, trip, ready(service, trip))
    voucher = service.voucher(order["order_id"], trip.id)
    assert "Hotel &lt;Ocean&gt;; Русский" in voucher and "Hotel <Ocean>" not in voucher
    assert "125000 RUB" in voucher and "return_transfer" in voucher and "не забронированы" in voucher
    calendar = service.calendar(order["order_id"], trip.id)
    unfolded = calendar.replace("\r\n ", "")
    assert "DTSTART:20261118T193000Z" in unfolded
    assert "DTEND:20261119T030000Z" in unfolded
    assert "DTSTART:20261125T200000Z" in unfolded  # local 26 Nov becomes 25 Nov UTC
    assert "DTSTART;VALUE=DATE:20261119" in unfolded and "DTEND;VALUE=DATE:20261126" in unfolded
    assert unfolded.count("BEGIN:VEVENT") == 3
    assert all(len(line.encode()) <= 75 for line in calendar.split("\r\n"))
    assert "\n" not in calendar.replace("\r\n", "")


def test_voucher_discloses_each_mixed_demo_leg_and_never_calls_mock_a_provider_quote(
    service: CheckoutService,
    trip: Session,
) -> None:
    trip.packages[0]["outbound"]["demo_source"] = {"kind": "deterministic_mock"}
    trip.packages[0]["outbound"]["price_source"] = None
    trip.packages[0]["inbound"]["demo_source"] = {"kind": "provider_replay"}
    trip.packages[0]["inbound"]["price_source"] = {"provider": "aviasales_data_api", "cache_mode": "replay"}
    order = pay(service, trip, ready(service, trip))
    voucher = service.voucher(order["order_id"], trip.id)
    outward = voucher.split("<li>Туда:", 1)[1].split("</li>", 1)[0]
    inbound = voucher.split("<li>Обратно:", 1)[1].split("</li>", 1)[0]
    assert "ДЕМО-МОК" in outward and "не котировка Aviasales" in outward and "Aviasales D4" not in outward
    assert "Aviasales D4" in inbound and "наличие мест и цена покупки не подтверждены" in inbound
    assert "ДЕМО-МОК" not in inbound and "билеты не выписаны" in voucher and "не забронированы" in voucher


def test_legacy_synthetic_voucher_does_not_claim_provider_origin(service: CheckoutService, trip: Session) -> None:
    order = pay(service, trip, ready(service, trip))
    voucher = service.voucher(order["order_id"], trip.id)
    assert voucher.count("СИНТЕТИЧЕСКОЕ ДЕМО") == 2
    assert "Aviasales D4" not in voucher


def test_http_checkout_owns_prices_and_requires_session_capability(service: CheckoutService, trip: Session) -> None:
    store = SessionStore()
    store._sessions[trip.id] = trip
    app = FastAPI()
    app.state.agent = SimpleNamespace(sessions=store, _locks={})
    app.state.checkout = service
    app.include_router(create_checkout_router())
    with TestClient(app) as client:
        payload = {"session_id": trip.id, "option": 1, "version": trip.version}
        assert client.post("/api/checkout/carts", json={**payload, "total_rub": 1}).status_code == 422
        cart_response = client.post("/api/checkout/carts", json=payload)
        assert cart_response.status_code == 201 and cart_response.headers["cache-control"] == "no-store"
        cart_id = cart_response.json()["cart_id"]
        path = f"/api/checkout/carts/{cart_id}"
        assert client.get(path, headers={"X-Session-ID": "wrong"}).status_code == 404
        assert client.get(path).status_code == 422
        body = {"session_id": trip.id, "travelers": [item.model_dump(mode="json") for item in travelers()]}
        assert client.put(path + "/travelers", json=body).status_code == 200
        payment = {
            "session_id": trip.id,
            "outcome": "approved",
            "idempotency_key": "http-success-001",
            "acknowledge_demo": True,
            "acknowledge_unpriced": True,
        }
        assert client.post(path + "/pay", json={**payment, "card_number": "TEST-FORBIDDEN"}).status_code == 422
        assert client.post(path + "/pay", json={**payment, "acknowledge_demo": False}).status_code == 422
        result = client.post(path + "/pay", json=payment).json()
        assert trip.stage == Stage.BOOKED and trip.booking["id"] == result["order_id"]
        assert "travelers" not in trip.booking and "package" not in trip.booking
        headers = {"X-Session-ID": trip.id}
        for suffix in ("", "/voucher", "/calendar.ics"):
            response = client.get(f"/api/checkout/orders/{result['order_id']}" + suffix, headers=headers)
            assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        assert client.put(path + "/travelers", json=body).status_code == 409
        # Replaying an old successful payment returns its original durable result,
        # but cannot turn a changed/new search into an old booked session.
        trip.version += 1
        trip.stale = True
        trip.stage = Stage.COLLECTING
        trip.booking = None
        trip.selected = None
        replay = client.post(path + "/pay", json=payment)
        assert replay.status_code == 200 and replay.json() == result
        assert trip.stage == Stage.COLLECTING and trip.booking is None and trip.selected is None
        # Both the DB and immutable paid order stay free of rejected card-like fields.
        assert b"TEST-FORBIDDEN" not in service.db_path.read_bytes()
        assert "card_number" not in str(result)


def test_paid_order_read_after_app_restart_without_session(service: CheckoutService, trip: Session) -> None:
    order = pay(service, trip, ready(service, trip))
    app = FastAPI()
    app.state.agent = SimpleNamespace(sessions=SessionStore(), _locks={})
    app.state.checkout = CheckoutService(service.db_path)
    app.include_router(create_checkout_router())
    with TestClient(app) as client:
        response = client.get(f"/api/checkout/orders/{order['order_id']}", headers={"X-Session-ID": trip.id})
        assert response.status_code == 200 and response.json() == order


@pytest.mark.parametrize("operation", ["cart", "travelers", "pay"])
async def test_mutating_checkout_waits_for_chat_session_lock(
    service: CheckoutService,
    trip: Session,
    operation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cart_id = ready(service, trip)
    lock = asyncio.Lock()
    store = SessionStore()
    store._sessions[trip.id] = trip
    app = FastAPI()
    app.state.agent = SimpleNamespace(sessions=store, _locks={trip.id: lock})
    app.state.checkout = service
    app.include_router(create_checkout_router())
    called = False
    method_name = {"cart": "create_cart", "travelers": "set_travelers", "pay": "pay"}[operation]
    original = getattr(service, method_name)

    def wrapped(*args, **kwargs):
        nonlocal called
        called = True
        return original(*args, **kwargs)

    monkeypatch.setattr(service, method_name, wrapped)
    path = f"/api/checkout/carts/{cart_id}"
    method = "POST"
    if operation == "cart":
        path = "/api/checkout/carts"
        payload = {"session_id": trip.id, "option": 1, "version": trip.version}
    elif operation == "travelers":
        path += "/travelers"
        method = "PUT"
        payload = {"session_id": trip.id, "travelers": [item.model_dump(mode="json") for item in travelers()]}
    else:
        path += "/pay"
        payload = {
            "session_id": trip.id,
            "outcome": "approved",
            "idempotency_key": "lock-approval-001",
            "acknowledge_demo": True,
            "acknowledge_unpriced": True,
        }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        await lock.acquire()
        task = asyncio.create_task(client.request(method, path, json=payload))
        await asyncio.sleep(0)
        assert not called and not task.done()
        lock.release()
        response = await asyncio.wait_for(task, 2)
        assert response.status_code in (200, 201) and called
