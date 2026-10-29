"""Regressions discovered by rehearsing ordinary judge questions over real HTTP."""

import hashlib
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.submission_rehearsal import forbid_external_network
from app.three_destination_rehearsal import mixed_demo_settings
from app.u2_eval import parse_sse

ROOT = Path(__file__).resolve().parents[2]
MAIN = "Из Москвы в Анталью 18 ноября 2026 на неделю вдвоём"
TRAVELERS = [
    {"full_name": "SYNTHETIC JUDGE ADULT ONE", "birth_date": "1990-01-01"},
    {"full_name": "SYNTHETIC JUDGE ADULT TWO", "birth_date": "1991-01-01"},
]


class Dialogue:
    def __init__(self, client: TestClient):
        self.client, self.sid, self.cart, self.order = client, None, None, None

    def state(self):
        return self.client.get(f"/api/session/{self.sid}").json()

    def say(self, text):
        response = self.client.post(
            "/api/chat", json={"message": text, **({"session_id": self.sid} if self.sid else {})}
        )
        assert response.status_code == 200
        events = parse_sse(response.text)
        assert events[-1]["type"] == "done" and not any(row["type"] == "error" for row in events)
        done = events[-1]["data"]
        self.sid = done["session_id"]
        assert done == self.state(), "SSE and reload must expose the same authoritative state"
        return events

    def setup(self, stage="selected", request=MAIN):
        self.say(request)
        option = next(row for row in self.state()["options"]["items"] if row["can_confirm"])
        if stage == "results":
            return
        self.say(f"Беру вариант {option['index']}")
        if stage == "selected":
            return
        events = self.say("Подтверждаю")
        self.cart = next(row["data"]["cart_id"] for row in events if row["type"] == "booking")
        if stage == "pending":
            return
        response = self.client.put(
            f"/api/checkout/carts/{self.cart}/travelers", json={"session_id": self.sid, "travelers": TRAVELERS}
        )
        assert response.status_code == 200
        if stage == "ready":
            return
        response = self.payment("approved" if stage == "paid" else "declined", "judge-initial-payment")
        assert response.status_code == 200
        if stage == "paid":
            self.order = response.json()
            assert self.order["status"] == "paid_demo"
        else:
            assert response.json()["status"] == "payment_declined"

    def payment(self, outcome, key):
        return self.client.post(
            f"/api/checkout/carts/{self.cart}/pay",
            json={
                "session_id": self.sid,
                "outcome": outcome,
                "idempotency_key": key,
                "acknowledge_demo": True,
                "acknowledge_unpriced": True,
            },
        )

    def durable_order(self):
        response = self.client.get(f"/api/checkout/orders/{self.order['order_id']}", headers={"X-Session-ID": self.sid})
        assert response.status_code == 200 and response.json() == self.order
        documents = []
        for key in ("voucher_path", "calendar_path"):
            response = self.client.get(self.order[key], headers={"X-Session-ID": self.sid})
            assert response.status_code == 200
            documents.append(hashlib.sha256(response.content).hexdigest())
        return documents


@pytest.fixture
def dialogue(tmp_path):
    settings = mixed_demo_settings(ROOT / "data/demo/aviasales", tmp_path / "checkout.sqlite3")
    with forbid_external_network() as network, TestClient(create_app(settings)) as client:
        client.app.state.agent._today = lambda: date(2026, 10, 29)
        yield Dialogue(client)
        assert network["blocked_attempts"] == 0


@pytest.mark.parametrize("stage", ["pending", "ready", "declined"])
def test_cancel_unpaid_checkout_closes_the_form_and_retires_old_payment_capabilities(dialogue, stage):
    dialogue.setup(stage)
    before = dialogue.state()
    dialogue.say("Отмени оформление, я не буду платить")
    after = dialogue.state()
    assert after["stage"] == "packages_shown" and after["selected"] is after["booking"] is None
    assert after["brief"] == before["brief"] and after["options"] == before["options"]
    assert after["version"] > before["version"]
    assert dialogue.payment("approved", "judge-old-cancelled-cart").status_code == 409
    assert (
        dialogue.client.put(
            f"/api/checkout/carts/{dialogue.cart}/travelers", json={"session_id": dialogue.sid, "travelers": TRAVELERS}
        ).status_code
        == 409
    )
    old_cart = dialogue.cart
    dialogue.say("Беру вариант 1")
    events = dialogue.say("Подтверждаю")
    new_cart = next(row["data"]["cart_id"] for row in events if row["type"] == "booking")
    assert new_cart != old_cart


@pytest.mark.parametrize("stage", ["pending", "paid"])
@pytest.mark.parametrize("question", ["А если отмена?", "Можно ли отменить?", "Как отменить?"])
def test_cancellation_questions_preserve_checkout_and_paid_history(dialogue, stage, question):
    dialogue.setup(stage)
    before = dialogue.state()
    documents = dialogue.durable_order() if stage == "paid" else None
    dialogue.say(question)
    assert dialogue.state() == before
    if documents:
        assert dialogue.durable_order() == documents


def test_paid_order_cannot_be_cancelled_or_refunded_by_a_chat_imperative(dialogue):
    dialogue.setup("paid")
    before, documents = dialogue.state(), dialogue.durable_order()
    events = dialogue.say("Отмени оформление")
    assert dialogue.state() == before and dialogue.durable_order() == documents
    text = "".join(row["data"]["text"] for row in events if row["type"] == "delta")
    assert "отмечен как оплаченный" in text and "Отмена и возврат не выполнялись" in text


@pytest.mark.parametrize("stage", ["results", "selected", "pending", "paid"])
def test_reference_destination_retires_old_cards_but_retains_other_criteria_and_paid_order(dialogue, stage):
    dialogue.setup(stage)
    documents = dialogue.durable_order() if stage == "paid" else None
    events = dialogue.say("Хочу в Бангкок 20 ноября 2026 на 5 ночей, трое взрослых, бюджет 300 тысяч")
    after = dialogue.state()
    assert after["brief"]["destination"] == "BKK"
    assert {key: after["brief"][key] for key in ("origin", "start_date", "nights", "adults", "budget_rub")} == {
        "origin": "MOW",
        "start_date": "2026-11-20",
        "nights": 5,
        "adults": 3,
        "budget_rub": 300_000,
    }
    assert after["stage"] == "destinations_shown" and after["options"]["items"] == []
    assert after["stale"] is False and after["last_diff"] is None
    assert after["selected"] is after["booking"] is None
    question = next(row["data"] for row in events if row["type"] == "question")
    assert question["field"] == "destination" and "полного подбора" in question["text"]
    information = next(row["data"] for row in events if row["type"] == "destination_info")
    assert information["destination"] == "BKK" and not information["full_trip_supported"]
    assert not any(row["type"] == "tool_call" for row in events), "Do not silently search a different destination"
    if documents:
        assert dialogue.durable_order() == documents


@pytest.mark.parametrize("destination", ["BKK", "NRT", "CMB", "GOI"])
def test_explicit_d5_iata_outside_full_demo_coverage_is_retained_without_inventing_trips(dialogue, destination):
    dialogue.say(f"Из Москвы в {destination} 18 ноября 2026 на неделю вдвоём")
    state = dialogue.state()
    assert state["brief"]["destination"] == destination
    # GOI is not in this D5 catalogue; don't invent reference facts just because
    # an arbitrary IATA was accepted.
    assert state["options"]["kind"] == ("packages" if destination == "GOI" else "destinations")
    assert state["options"]["items"] == []
    assert state["selected"] is state["booking"] is None


@pytest.mark.parametrize(
    "message", ["Из Москвы в Дубай 5.11 на неделю вдвоём", "Из Москвы в Дубай 05.11 на неделю вдвоём"]
)
def test_numeric_date_without_year_uses_the_next_actual_date(dialogue, message):
    dialogue.say(message)
    state = dialogue.state()
    assert state["brief"]["start_date"] == "2026-11-05"
    assert all(row["outbound"]["departure_at"].startswith("2026-11-05T") for row in state["options"]["items"])


@pytest.mark.parametrize("span", ["с 18 по 25", "18–25", "18-25"])
def test_same_month_range_keeps_the_first_day_instead_of_the_last_day(dialogue, span):
    dialogue.say(f"Из Москвы в Дубай {span} ноября 2026 вдвоём")
    state = dialogue.state()
    assert state["brief"]["start_date"] == "2026-11-18" and state["brief"]["nights"] == 7
    assert state["options"]["items"] and all(
        row["outbound"]["departure_at"].startswith("2026-11-18T") for row in state["options"]["items"]
    )


@pytest.mark.parametrize("clear", ["Сними бюджет", "Убери ограничение по бюджету", "Бюджет неважен"])
def test_explicitly_relaxing_filters_rebuilds_selection_and_keeps_paid_history(dialogue, clear):
    dialogue.setup(
        "paid",
        "Из Москвы в Дубай 18 ноября 2026 на неделю вдвоём, только прямые рейсы, возвратный отель, бюджет 200 тысяч",
    )
    documents = dialogue.durable_order()
    dialogue.say(f"{clear}. Теперь можно с пересадками и можно невозвратный отель")
    state = dialogue.state()
    assert state["brief"]["budget_rub"] is None
    assert state["brief"]["direct_only"] is state["brief"]["refundable_only"] is False
    assert "flexible" not in state["brief"]["needs"]
    assert state["selected"] is state["booking"] is None and state["stage"] == "packages_shown"
    assert dialogue.durable_order() == documents


@pytest.mark.parametrize(
    "question",
    [
        "Почему отель невозвратный?",
        "Почему отель невозвратный",
        "Почему отель возвратный?",
        "Нужны ли прямые рейсы?",
        "Можно ли с пересадками?",
    ],
)
def test_questions_about_filters_do_not_rewrite_the_trip(dialogue, question):
    dialogue.setup("results")
    before = dialogue.state()
    dialogue.say(question)
    assert dialogue.state() == before


def test_decimal_money_is_not_a_short_numeric_departure_date(dialogue):
    dialogue.say("Из Москвы в Дубай в ноябре на неделю, бюджет 5.11 тысяч рублей")
    assert dialogue.state()["brief"]["budget_rub"] == 5110
    assert dialogue.state()["brief"]["start_date"] != "2026-11-05"


@pytest.mark.parametrize("wish", ["тихий отель на море", "семейный отель", "любую тёплую страну"])
def test_hotel_wishes_are_not_mistaken_for_unrecognized_city_names(dialogue, wish):
    events = dialogue.say(f"Хочу в {wish} в ноябре из Москвы")
    state = dialogue.state()
    assert state["stage"] == "destinations_shown" and state["options"]["kind"] == "destinations"
    assert state["options"]["items"]
    assert not any(row["type"] == "question" and row["data"]["field"] == "destination" for row in events)


@pytest.mark.parametrize("message", ["Сними бюджет", "Отмени ограничение бюджета"])
def test_clearing_an_already_absent_budget_does_not_cancel_or_expire_an_unchanged_cart(dialogue, message):
    dialogue.setup("pending")
    before = dialogue.state()
    dialogue.say(message)
    assert dialogue.state() == before


@pytest.mark.parametrize(
    ("message", "destination", "budget"),
    [
        ("Хочу в пределах 150 тысяч", "AYT", 150_000),
        ("Хочу в центре Дубая", "DXB", None),
        ("Хочу в номер с видом на море", "AYT", None),
        ("Хочу в начале ноября", "AYT", None),
        ("Хочу на завтра", "AYT", None),
        ("Хочу в тот же отель", "AYT", None),
        ("Хочу на завтрак", "AYT", None),
        ("Хочу на пляж", "AYT", None),
        ("Хочу на одного", "AYT", None),
        ("Хочу в город с пляжем", "AYT", None),
    ],
)
def test_ordinary_trip_followups_do_not_erase_the_destination(dialogue, message, destination, budget):
    dialogue.setup("pending")
    events = dialogue.say(message)
    state = dialogue.state()
    assert state["brief"]["destination"] == destination
    assert state["brief"]["budget_rub"] == budget
    assert state["options"]["kind"] == "packages" and state["options"]["items"]
    assert not any(row["type"] == "question" and row["data"]["field"] == "destination" for row in events)


@pytest.mark.parametrize(
    "message",
    [
        "Отменить не нужно, я просто спрашиваю про условия",
        "Не буду платить за страховку",
        "Отмени трансфер",
        "Отмени оплату страховки",
        "Не оформляй страховку",
        "Не отменяй оформление",
        "Отмени, нет, не отменяй",
    ],
)
def test_negations_and_ancillary_service_changes_do_not_cancel_the_whole_checkout(dialogue, message):
    dialogue.setup("pending")
    before = dialogue.state()
    dialogue.say(message)
    assert dialogue.state() == before


def test_explicit_request_to_stop_whole_checkout_still_closes_the_unpaid_form(dialogue):
    dialogue.setup("pending")
    dialogue.say("Хочу отменить оформление")
    after = dialogue.state()
    assert after["stage"] == "packages_shown" and after["selected"] is after["booking"] is None
    assert dialogue.payment("approved", "judge-explicit-stop").status_code == 409
