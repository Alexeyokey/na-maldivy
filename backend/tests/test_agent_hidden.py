"""A4: разбор правилами и словарь скрытых требований."""

from datetime import date

import pytest

from app.agent.brief import TripBrief
from app.agent.hidden import NEEDS, RULES, detect_needs, extract
from app.agent.places import to_iata

TODAY = date(2026, 10, 27)


def test_every_rule_points_to_known_need() -> None:
    assert {rule.need for rule in RULES} <= set(NEEDS)


@pytest.mark.parametrize(
    ("text", "needs"),
    [
        ("хочу к тёплому морю в ноябре, недорого, с ребёнком", {"sea", "warm", "budget_low", "child"}),
        ("с нами едет собака", {"pets"}),
        ("медовый месяц, хотим тишины", {"romantic", "quiet"}),
        ("прилетаем ночью", {"late_arrival"}),
        ("едем с бабушкой", {"short_transfer"}),
        ("даты не точные, планы могут поменяться", {"flexible"}),
        ("без долгих перелётов", {"short_flight"}),
        ("с маленьким ребёнком", {"child"}),
    ],
)
def test_detects_hidden_needs(text: str, needs: set[str]) -> None:
    assert needs <= set(detect_needs(text))


def test_vague_request_from_task_statement() -> None:
    patch = extract("хочу куда-нибудь к тёплому морю в ноябре, недорого, с ребёнком, без долгих перелётов", TODAY)
    assert patch.month == 11
    assert patch.origin is None and patch.destination is None  # не угадываем
    assert patch.min_water_temperature_c == 24
    assert patch.adults == 2 and "adults" in patch.inferred
    assert patch.children_ages is None  # возраст не назван — его спросят
    assert {"child", "sea", "budget_low", "short_flight"} <= set(patch.needs_add)


def test_concrete_request() -> None:
    patch = extract("Летим из Питера в Дубай 18 ноября на неделю вдвоём, бюджет 250 тысяч", TODAY)
    assert (patch.origin, patch.destination) == ("LED", "DXB")
    assert patch.start_date == "2026-11-18"
    assert patch.nights == 7
    assert patch.adults == 2 and "adults" in patch.inferred
    assert patch.budget_rub == 250_000


def test_pets_and_child_age() -> None:
    patch = extract("с собакой на 5 дней в Анталью, сыну 7 лет, из Москвы", TODAY)
    assert patch.pets == 1
    assert patch.children_ages == [7]
    assert patch.nights == 4 and "nights" in patch.inferred  # 5 дней = 4 ночи, это вывод
    assert {"pets", "child"} <= set(patch.needs_add)


def test_hours_are_not_budget() -> None:
    patch = extract("до 5 часов в пути", TODAY)
    assert patch.budget_rub is None
    assert patch.max_flight_minutes == 300


def test_past_date_moves_to_next_year() -> None:
    assert extract("вылет 3 января", TODAY).start_date == "2027-01-03"


@pytest.mark.parametrize(
    ("text", "field", "value"),
    [
        ("а если на два дня позже?", "start_date", "2026-11-09"),
        ("на неделю раньше", "start_date", "2026-10-31"),
        ("на 3 дня дольше", "nights", 10),
        ("на две ночи короче", "nights", 5),
        ("бюджет меньше на 20 тысяч", "budget_rub", 180_000),
    ],
)
def test_relative_changes(text: str, field: str, value: object) -> None:
    brief = TripBrief(start_date=date(2026, 11, 7), nights=7, budget_rub=200_000)
    patch = extract(text, TODAY, brief)
    assert getattr(patch, field) == value
    if field == "start_date":
        assert patch.nights is None  # «на два дня позже» — не срок поездки


@pytest.mark.parametrize(
    ("name", "code"),
    [
        ("Питер", "LED"),
        ("из Москвы", "MOW"),
        ("на Мальдивы", "MLE"),
        ("Турция", "AYT"),
        ("led", "LED"),
        ("маленький", None),
        ("Марс", None),
    ],
)
def test_places(name: str, code: str | None) -> None:
    assert to_iata(name) == code
