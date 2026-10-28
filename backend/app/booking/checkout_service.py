"""Private durable DEMO checkout. No provider booking or payment processing happens here."""

from __future__ import annotations

import hashlib
import html
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

if TYPE_CHECKING:
    from app.agent.session import Session

PRICE_SCOPE = "round_trip_flights_baggage_hotel_arrival_transfer"
DEMO_NOTICE = "ДЕМО: деньги не списаны; билеты не выписаны; отель и трансферы не забронированы."


class CheckoutError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class DemoTraveler(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    full_name: str = Field(min_length=2, max_length=120)
    birth_date: date

    @field_validator("full_name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        if any(ord(char) < 32 for char in value):
            raise ValueError("Имя не должно содержать управляющие символы")
        value = value.strip()
        if len(value) < 2:
            raise ValueError("Укажите имя демо-путешественника")
        return value


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _fingerprint(session: Session, option: int) -> str:
    return hashlib.sha256(
        _json({"package": session.package(option), "travelers": session.brief.travelers.model_dump()}).encode()
    ).hexdigest()


def _aware(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        return parsed
    except (TypeError, ValueError):
        raise CheckoutError(409, "У поездки нет проверенного времени с часовым поясом") from None


def _amount(value: Any) -> int:
    if type(value) is not int or value < 0:
        raise CheckoutError(409, "Цена компонента не подтверждена")
    return value


def _safe_package(session: Session, option: int, expected_version: int) -> dict[str, Any]:
    if session.version != expected_version or session.stale:
        raise CheckoutError(409, "Вариант устарел: повторите поиск и выбор")
    package = session.package(option)
    if package is None:
        raise CheckoutError(404, "Вариант поездки не найден")
    if package.get("can_confirm") is not True or package.get("validation_status") != "checked":
        raise CheckoutError(409, "Обязательные условия поездки не подтверждены")
    if any(issue.get("severity") == "critical" for issue in package.get("issues", [])):
        raise CheckoutError(409, "Сначала устраните критические проблемы поездки")
    if package.get("price_scope") != PRICE_SCOPE:
        raise CheckoutError(409, "Состав подтверждаемой цены неизвестен")
    try:
        hotel, outbound, inbound, transfer = (
            package["hotel"],
            package["outbound"],
            package["inbound"],
            package["transfer"],
        )
        components = [
            outbound["price_rub"],
            outbound["baggage_price_rub"],
            inbound["price_rub"],
            inbound["baggage_price_rub"],
            hotel["price_rub"],
            transfer["price_rub"],
        ]
        total = sum(_amount(value) for value in components)
        if _amount(package["total_rub"]) != total or _amount(package["known_total_rub"]) != total:
            raise CheckoutError(409, "Сумма компонентов не совпадает с ценой варианта")
        for flight in (outbound, inbound):
            if _aware(flight["arrival_at"]) <= _aware(flight["departure_at"]):
                raise CheckoutError(409, "Время рейса противоречиво")
        if _aware(inbound["departure_at"]) <= _aware(outbound["arrival_at"]):
            raise CheckoutError(409, "Обратный рейс предшествует прилёту")
        if date.fromisoformat(hotel["check_out"]) <= date.fromisoformat(hotel["check_in"]):
            raise CheckoutError(409, "Даты проживания противоречивы")
        if session.brief.pets and hotel.get("pets_allowed") is not True:
            raise CheckoutError(409, "Проживание с животными не подтверждено")
        if session.brief.children_ages and hotel.get("adults_only") is not False:
            raise CheckoutError(409, "Проживание с детьми не подтверждено")
    except (KeyError, TypeError, ValueError):
        raise CheckoutError(409, "Состав или время поездки не подтверждены") from None
    return json.loads(_json(package))  # immutable value snapshot, detached from the session


class CheckoutService:
    """One SQLite transaction per write; DB belongs to private app state, never to D4."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS carts (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL, version INTEGER NOT NULL,
                    option_number INTEGER NOT NULL, fingerprint TEXT NOT NULL,
                    snapshot TEXT NOT NULL, travelers TEXT, status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(session_id, version, option_number, fingerprint)
                );
                CREATE TABLE IF NOT EXISTS orders (
                    id TEXT PRIMARY KEY, cart_id TEXT NOT NULL UNIQUE REFERENCES carts(id),
                    session_id TEXT NOT NULL, snapshot TEXT NOT NULL,
                    travelers TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS payment_attempts (
                    cart_id TEXT NOT NULL REFERENCES carts(id), idempotency_key TEXT NOT NULL,
                    outcome TEXT NOT NULL, result TEXT NOT NULL,
                    PRIMARY KEY(cart_id, idempotency_key)
                );
            """)
        self.db_path.chmod(0o600)

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _cart(row: sqlite3.Row) -> dict[str, Any]:
        snapshot = json.loads(row["snapshot"])
        return {
            "cart_id": row["id"],
            "session_id": row["session_id"],
            "version": row["version"],
            "option": row["option_number"],
            "status": row["status"],
            "package": snapshot["package"],
            "expected_travelers": snapshot["expected_travelers"],
            "travelers": json.loads(row["travelers"]) if row["travelers"] else [],
            "created_at": row["created_at"],
            "is_simulated": True,
            "notice": DEMO_NOTICE,
        }

    @staticmethod
    def _find_cart(db: sqlite3.Connection, cart_id: str, session_id: str) -> sqlite3.Row:
        row = db.execute("SELECT * FROM carts WHERE id=? AND session_id=?", (cart_id, session_id)).fetchone()
        if row is None:
            raise CheckoutError(404, "Корзина не найдена")
        return row

    def create_cart(self, session: Session, *, option: int, expected_version: int) -> dict[str, Any]:
        package = _safe_package(session, option, expected_version)
        fingerprint = _fingerprint(session, option)
        snapshot = {"package": package, "expected_travelers": session.brief.travelers.model_dump()}
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM carts WHERE session_id=? AND version=? AND option_number=? AND fingerprint=?",
                (session.id, session.version, option, fingerprint),
            ).fetchone()
            if row is None:
                cart_id = "CART-" + uuid.uuid4().hex
                db.execute(
                    "INSERT INTO carts VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)",
                    (
                        cart_id,
                        session.id,
                        session.version,
                        option,
                        fingerprint,
                        _json(snapshot),
                        "awaiting_travelers",
                        _utcnow(),
                    ),
                )
                row = self._find_cart(db, cart_id, session.id)
            return self._cart(row)

    def get_cart(self, cart_id: str, session_id: str) -> dict[str, Any]:
        with self._connection() as db:
            return self._cart(self._find_cart(db, cart_id, session_id))

    def cart_is_current(self, cart_id: str, session: Session) -> bool:
        """Historic idempotency responses must never overwrite a newer chat state."""
        with self._connection() as db:
            try:
                self._check_current(self._find_cart(db, cart_id, session.id), session)
            except CheckoutError:
                return False
            return True

    @staticmethod
    def _check_current(row: sqlite3.Row, session: Session) -> None:
        _safe_package(session, row["option_number"], row["version"])
        if (
            session.id != row["session_id"]
            or session.selected != row["option_number"]
            or _fingerprint(session, row["option_number"]) != row["fingerprint"]
        ):
            raise CheckoutError(409, "Вариант изменился: создайте новую корзину")

    def set_travelers(self, cart_id: str, session: Session, travelers: list[DemoTraveler]) -> dict[str, Any]:
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._find_cart(db, cart_id, session.id)
            self._check_current(row, session)
            if row["status"] == "paid_demo":
                raise CheckoutError(409, "Данные оплаченного демо-заказа неизменяемы")
            expected = json.loads(row["snapshot"])["expected_travelers"]
            trip_day = _aware(json.loads(row["snapshot"])["package"]["outbound"]["departure_at"]).date()
            ages = []
            for traveler in travelers:
                born = traveler.birth_date
                age = trip_day.year - born.year - ((trip_day.month, trip_day.day) < (born.month, born.day))
                if born > date.today() or not 0 <= age <= 130:
                    raise CheckoutError(422, "Дата рождения демо-путешественника недопустима")
                ages.append(age)
            if sum(age >= 18 for age in ages) != expected["adults"] or sorted(
                age for age in ages if age < 18
            ) != sorted(expected["children_ages"]):
                raise CheckoutError(422, "Состав путешественников не совпадает с брифом на дату вылета")
            payload = [traveler.model_dump(mode="json") for traveler in travelers]
            db.execute(
                "UPDATE carts SET travelers=?, status='ready_for_demo_payment' WHERE id=?", (_json(payload), cart_id)
            )
            return self._cart(self._find_cart(db, cart_id, session.id))

    def pay(
        self,
        cart_id: str,
        session: Session,
        *,
        outcome: Literal["approved", "declined"],
        idempotency_key: str,
        acknowledge_demo: bool,
        acknowledge_unpriced: bool,
    ) -> dict[str, Any]:
        if outcome not in ("approved", "declined") or not 8 <= len(idempotency_key) <= 100:
            raise CheckoutError(422, "Неверный тестовый исход или ключ повтора")
        if not acknowledge_demo or not acknowledge_unpriced:
            raise CheckoutError(422, "Подтвердите демо-режим и непосчитанные дополнительные расходы")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._find_cart(db, cart_id, session.id)
            attempt = db.execute(
                "SELECT * FROM payment_attempts WHERE cart_id=? AND idempotency_key=?", (cart_id, idempotency_key)
            ).fetchone()
            if attempt is not None:
                if attempt["outcome"] != outcome:
                    raise CheckoutError(409, "Этот ключ повтора уже использован с другим исходом")
                return json.loads(attempt["result"])
            # A new request must still refer to the selected server-side package, even after edits.
            self._check_current(row, session)
            if row["status"] == "paid_demo":
                order = db.execute("SELECT * FROM orders WHERE cart_id=?", (cart_id,)).fetchone()
                result = self._order(order)
                # Even a request that reuses the completed order reserves its key.
                # A later request with that key must keep the same requested outcome.
                db.execute(
                    "INSERT INTO payment_attempts VALUES (?, ?, ?, ?)",
                    (cart_id, idempotency_key, outcome, _json(result)),
                )
                return result
            if not row["travelers"]:
                raise CheckoutError(409, "Сначала заполните данные демо-путешественников")
            if outcome == "declined":
                result = {
                    "cart_id": cart_id,
                    "status": "payment_declined",
                    "is_simulated": True,
                    "notice": "Тестовый отказ. Деньги не списаны, заказ не создан.",
                }
                db.execute("UPDATE carts SET status='payment_declined' WHERE id=?", (cart_id,))
            else:
                order_id = "DEMO-" + uuid.uuid4().hex.upper()
                db.execute(
                    "INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?)",
                    (order_id, cart_id, session.id, row["snapshot"], row["travelers"], _utcnow()),
                )
                db.execute("UPDATE carts SET status='paid_demo' WHERE id=?", (cart_id,))
                result = self._order(db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone())
            db.execute(
                "INSERT INTO payment_attempts VALUES (?, ?, ?, ?)", (cart_id, idempotency_key, outcome, _json(result))
            )
            return result

    @staticmethod
    def _order(row: sqlite3.Row) -> dict[str, Any]:
        snapshot = json.loads(row["snapshot"])
        order_id = row["id"]
        return {
            "order_id": order_id,
            "cart_id": row["cart_id"],
            "session_id": row["session_id"],
            "status": "paid_demo",
            "is_simulated": True,
            "notice": DEMO_NOTICE,
            "package": snapshot["package"],
            "travelers": json.loads(row["travelers"]),
            "created_at": row["created_at"],
            "voucher_path": f"/api/checkout/orders/{order_id}/voucher",
            "calendar_path": f"/api/checkout/orders/{order_id}/calendar.ics",
        }

    def get_order(self, order_id: str, session_id: str) -> dict[str, Any]:
        with self._connection() as db:
            row = db.execute("SELECT * FROM orders WHERE id=? AND session_id=?", (order_id, session_id)).fetchone()
            if row is None:
                raise CheckoutError(404, "Демо-заказ не найден")
            return self._order(row)

    def voucher(self, order_id: str, session_id: str) -> str:
        order = self.get_order(order_id, session_id)
        package = order["package"]

        def esc(value: Any) -> str:
            return html.escape(str(value))

        travelers = ", ".join(traveler["full_name"] for traveler in order["travelers"])
        components = [
            ("Перелёт туда", package["outbound"]["price_rub"]),
            ("Багаж туда", package["outbound"]["baggage_price_rub"]),
            ("Перелёт обратно", package["inbound"]["price_rub"]),
            ("Багаж обратно", package["inbound"]["baggage_price_rub"]),
            ("Проживание", package["hotel"]["price_rub"]),
            ("Трансфер в отель", package["transfer"]["price_rub"]),
        ]
        lines = "".join(f"<li>{esc(label)}: {esc(amount)} RUB</li>" for label, amount in components)
        extras = ", ".join(package["unpriced_components"])

        def flight_source(leg: dict[str, Any]) -> str:
            demo = leg.get("demo_source") or {}
            if demo.get("kind") == "deterministic_mock":
                return "ДЕМО-МОК: расписание и цена сгенерированы; это не котировка Aviasales и не наличие мест."
            price_source = leg.get("price_source") or {}
            if demo.get("kind") == "provider_replay" or price_source.get("provider") == "aviasales_data_api":
                return "Aviasales D4: сохранённая котировка; наличие мест и цена покупки не подтверждены."
            return "СИНТЕТИЧЕСКОЕ ДЕМО: расчётные расписание и цена; наличие мест не подтверждено."

        flights = "".join(
            f"<li>{title}: {esc(leg['from'])} → {esc(leg['to'])}: "
            f"{esc(leg['departure_at'])} — {esc(leg['arrival_at'])}. {esc(flight_source(leg))}</li>"
            for title, leg in (("Туда", package["outbound"]), ("Обратно", package["inbound"]))
        )
        return (
            '<!doctype html><html lang="ru"><meta charset="utf-8"><title>Демо-ваучер</title>'
            f"<body><h1>ДЕМО-ВАУЧЕР {esc(order_id)}</h1><p><strong>{DEMO_NOTICE}</strong></p>"
            f"<p>Путешественники: {esc(travelers)}</p><p>Отель: {esc(package['hotel']['name'])}; "
            f"{esc(package['hotel']['check_in'])} — {esc(package['hotel']['check_out'])}</p><ul>{flights}</ul>"
            f"<h2>Расчёт показанных компонентов</h2><ul>{lines}</ul><p>Итого: {package['known_total_rub']} RUB</p>"
            f"<p>Не включены и не оценены: {esc(extras)}. Итоговая стоимость всей поездки не подтверждена.</p>"
            "<p>Котировки из кэша и расчётные компоненты не подтверждают наличие. "
            "Документ предназначен только для демонстрации.</p></body></html>"
        )

    def calendar(self, order_id: str, session_id: str) -> str:
        order = self.get_order(order_id, session_id)
        package = order["package"]
        lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//na-maldivy//DEMO checkout//RU", "CALSCALE:GREGORIAN"]
        stamp = _aware(order["created_at"]).strftime("%Y%m%dT%H%M%SZ")
        for direction in ("outbound", "inbound"):
            leg = package[direction]
            lines += [
                "BEGIN:VEVENT",
                f"UID:{order_id}-{direction}@na-maldivy.demo",
                f"DTSTAMP:{stamp}",
                f"DTSTART:{_aware(leg['departure_at']).astimezone(UTC):%Y%m%dT%H%M%SZ}",
                f"DTEND:{_aware(leg['arrival_at']).astimezone(UTC):%Y%m%dT%H%M%SZ}",
                "SUMMARY:" + _ics_escape(f"ДЕМО перелёт {leg['from']} → {leg['to']}"),
                "DESCRIPTION:" + _ics_escape(DEMO_NOTICE),
                "STATUS:TENTATIVE",
                "END:VEVENT",
            ]
        hotel = package["hotel"]
        lines += [
            "BEGIN:VEVENT",
            f"UID:{order_id}-hotel@na-maldivy.demo",
            f"DTSTAMP:{stamp}",
            "DTSTART;VALUE=DATE:" + date.fromisoformat(hotel["check_in"]).strftime("%Y%m%d"),
            "DTEND;VALUE=DATE:" + date.fromisoformat(hotel["check_out"]).strftime("%Y%m%d"),
            "SUMMARY:" + _ics_escape("ДЕМО проживание: " + hotel["name"]),
            "DESCRIPTION:" + _ics_escape(DEMO_NOTICE + " Время заезда и выезда уточняется отдельно."),
            "STATUS:TENTATIVE",
            "END:VEVENT",
            "END:VCALENDAR",
        ]
        return "\r\n".join(_ics_fold(line) for line in lines) + "\r\n"


def _ics_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\r", "").replace("\n", "\\n").replace(";", "\\;").replace(",", "\\,")


def _ics_fold(line: str) -> str:
    result, current = [], ""
    for char in line:
        if len((current + char).encode()) > 75:
            result.append(current)
            current = " "
        current += char
    result.append(current)
    return "\r\n".join(result)
