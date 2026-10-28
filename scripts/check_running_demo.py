"""Smoke the running HTTP gateway, then verify persistence with --resume after restart.

Pure stdlib. Never reads .env or prints identifiers, traveler data or headers.
The context file contains private session access and must remain local.
"""

import argparse
import hashlib
import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

REFERENCE_DATE = date(2026, 11, 18)
MOSCOW_AIRPORTS = {"MOW", "SVO", "DME", "VKO"}
EXPECTED_TRAVELERS = {"adults": 2, "children_ages": [], "pets": 0}


def departure_date(value=None, *, today=None):
    """Keep the recorded-fare rehearsal while future; otherwise use honest demo data."""
    today = today or date.today()
    result = (
        date.fromisoformat(value)
        if value
        else max(REFERENCE_DATE, today + timedelta(days=28))
    )
    # Before the fixed reference date, keep its verifiable saved quotes.
    if value is None and today < REFERENCE_DATE:
        result = REFERENCE_DATE
    if result <= today:
        raise ValueError("Departure date must be in the future.")
    return result


def local_base_url(value):
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1", "app"}
        or not parsed.port
        or (parsed.hostname == "app" and parsed.port != 8080)
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise ValueError(
            "Use http://localhost:8080 or the Compose-internal http://app:8080 gateway."
        )
    return value.rstrip("/")


def gateway_argument(value):
    try:
        return local_base_url(value)
    except ValueError:
        # argparse's default ValueError handling repeats the raw argument,
        # which could contain credentials accidentally pasted into a URL.
        raise argparse.ArgumentTypeError(
            "Use a local HTTP gateway URL without credentials, query or fragment."
        ) from None


class HTTPStatusError(Exception):
    def __init__(self, method, status, expected):
        # Do not include a URL: it can contain private session/order capabilities.
        super().__init__(f"{method} returned HTTP {status}; expected {expected}.")


class VerificationError(Exception):
    pass


def require(condition, message="Verification failed"):
    if not condition:
        raise VerificationError(message)


def digest(value):
    data = (
        value
        if isinstance(value, bytes)
        else json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    )
    return hashlib.sha256(data).hexdigest()


def atomic_private_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".demo-context-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as output:
            json.dump(value, output, ensure_ascii=False)
            output.write("\n")
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        Path(temporary).unlink(missing_ok=True)


def parse_sse(body):
    events = []
    for block in body.decode().replace("\r\n", "\n").strip().split("\n\n"):
        kind, data = "message", []
        for line in block.splitlines():
            if line.startswith("event:"):
                kind = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].lstrip())
        if data:
            events.append({"type": kind, "data": json.loads("\n".join(data))})
    require(events and events[-1]["type"] == "done", "Incomplete or failed SSE stream")
    require(not any(row["type"] == "error" for row in events), "SSE error event")
    return events


def single(events, kind):
    rows = [row["data"] for row in events if row["type"] == kind]
    require(len(rows) == 1, "Expected one event")
    return rows[0]


def flight_source(value, expected=None, *, origin=None, destination=None, on_date=None):
    """Validate advertised provenance and requested route/date, not raw provider responses."""
    if origin is not None or destination is not None:
        require(
            (origin is None or value.get("from") in origin)
            and (destination is None or value.get("to") in destination),
            "Flight source is attached to the wrong route.",
        )
    if on_date is not None:
        departure = datetime.fromisoformat(value["departure_at"])
        require(
            departure.utcoffset() is not None and departure.date() == on_date,
            "Flight source is attached to the wrong departure date.",
        )
    demo, price = value.get("demo_source") or {}, value.get("price_source")
    kind = demo.get("kind")
    require(
        kind in {"provider_replay", "deterministic_mock"},
        "Explicit flight source missing",
    )
    require(expected is None or kind == expected, "Frozen source composition changed")
    if kind == "deterministic_mock":
        require(
            price is None
            and "ДЕМО-МОК" in demo.get("notice", "")
            and "не котировка Aviasales" in demo["notice"]
            and "не наличие мест" in demo["notice"],
            "Mock is not honestly labelled",
        )
        return {"kind": kind, "provider_evidence": False}
    require(
        price
        and price.get("provider") == "aviasales_data_api"
        and price.get("cache_mode") == "replay"
        and price.get("cache_hit") is True,
        "Provider replay evidence missing",
    )
    require(
        "не подтвержден" in demo.get("notice", "")
        and price.get("seat_price_rub", 0) > 0,
        "Provider source cannot confirm live inventory",
    )
    require(
        datetime.fromisoformat(price["recorded_at"]).utcoffset() is not None,
        "Recording timestamp missing",
    )
    if "price_rub" in value:
        require(value["price_rub"] == 2 * price["seat_price_rub"], "Group fare changed")
    return {
        "kind": kind,
        "provider_evidence": True,
        "seat_price_rub": price["seat_price_rub"],
    }


class Check:
    def __init__(self, base_url, context_path, resume, trip_date=None):
        self.base_url = local_base_url(base_url)
        self.context_path = context_path

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *_args, **_kwargs):
                return None

        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect()
        )
        self.departure_date = trip_date or departure_date()
        self.reference_fixture = self.departure_date == REFERENCE_DATE
        self.session_id = None
        self.current_stage = "initialization"
        self.report = {
            "schema_version": 1,
            "started_at": datetime.now(UTC).isoformat(),
            "scope": "running_gateway_http_backend_simulated_checkout",
            "mode": "resume_after_restart" if resume else "initial_run",
            "status": "failed",
            "integrated_ui_status": "not_run",
            "completed_stages": [],
            "stage_timings_seconds": {},
            "notice": "Backend HTTP only; simulated payment, no issued tickets or real bookings.",
            "departure_date": self.departure_date.isoformat(),
            "scenario": "recorded_reference_fixture"
            if self.reference_fixture
            else "future_demo",
            "runtime_network_isolation": "not_proven_by_http_check",
            "source_evidence_scope": "server_provenance_metadata_and_reference_fixture_prices",
        }

    @contextmanager
    def stage(self, name):
        self.current_stage = name
        started = time.perf_counter()
        try:
            yield
        finally:
            self.report["stage_timings_seconds"][name] = round(
                time.perf_counter() - started, 3
            )
        self.report["completed_stages"].append(name)

    def step(self, name):
        return self.stage(name)

    def restore_scenario(self, context):
        self.departure_date = date.fromisoformat(
            context.get("departure_date", REFERENCE_DATE.isoformat())
        )
        self.reference_fixture = self.departure_date == REFERENCE_DATE
        self.report.update(
            departure_date=self.departure_date.isoformat(),
            scenario="recorded_reference_fixture"
            if self.reference_fixture
            else "future_demo",
        )

    def request(self, method, path, body=None, *, capability=False, expected=200):
        headers = {"Accept": "application/json"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, ensure_ascii=False).encode()
        if capability:
            headers["X-Session-ID"] = self.session_id
        request = urllib.request.Request(
            self.base_url + path, data=data, headers=headers, method=method
        )
        try:
            response = self.opener.open(request, timeout=60)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            status, response_headers, content = (
                response.status,
                response.headers,
                response.read(),
            )
        if status != expected:
            raise HTTPStatusError(method, status, expected)
        return response_headers, content

    def json(self, method, path, body=None, **kwargs):
        headers, content = self.request(method, path, body, **kwargs)
        return json.loads(content), headers

    def chat(self, text):
        body = {"message": text}
        if self.session_id is not None:
            body["session_id"] = self.session_id
        headers, content = self.request("POST", "/api/chat", body)
        require(
            headers.get("Content-Type", "").startswith("text/event-stream"),
            "Expected SSE",
        )
        events = parse_sse(content)
        self.session_id = single(events, "done")["session_id"]
        return events

    def snapshot(self):
        return self.json("GET", f"/api/session/{self.session_id}")[0]

    def verify_package(self, selected, destination):
        """Do not count three repeated or misdated results as three requested routes."""
        snapshot = self.snapshot()
        brief = snapshot["brief"]
        require(
            brief.get("transport") == "flight"
            and brief.get("origin") == "MOW"
            and brief.get("destination") == destination
            and brief.get("start_date") == self.departure_date.isoformat()
            and brief.get("month") == self.departure_date.month
            and brief.get("year") == self.departure_date.year
            and brief.get("nights") == 7
            and {key: brief.get(key) for key in EXPECTED_TRAVELERS}
            == EXPECTED_TRAVELERS,
            "The server brief does not match the requested route, date, stay and group.",
        )
        require(
            snapshot.get("stage") == "packages_shown"
            and snapshot.get("stale") is False
            and snapshot.get("selected") is None
            and snapshot.get("booking") is None
            and snapshot["options"]["kind"] == "packages"
            and selected in snapshot["options"]["items"],
            "Selected package does not belong to the current unselected search results.",
        )
        require(
            selected.get("destination") == destination
            and selected.get("travelers") == EXPECTED_TRAVELERS,
            "The package route or group differs from the requested trip.",
        )
        outbound, inbound, hotel = (
            selected["outbound"],
            selected["inbound"],
            selected["hotel"],
        )
        out_departure, out_arrival, in_departure, in_arrival = (
            datetime.fromisoformat(leg[key])
            for leg, key in (
                (outbound, "departure_at"),
                (outbound, "arrival_at"),
                (inbound, "departure_at"),
                (inbound, "arrival_at"),
            )
        )
        check_in, check_out = (
            date.fromisoformat(hotel["check_in"]),
            date.fromisoformat(hotel["check_out"]),
        )
        require(
            all(
                moment.utcoffset() is not None
                for moment in (out_departure, out_arrival, in_departure, in_arrival)
            )
            and out_departure < out_arrival < in_departure < in_arrival
            and check_in == out_arrival.date()
            and check_out == check_in + timedelta(days=7)
            and hotel.get("nights") == 7,
            "Flight chronology and hotel check-in/check-out do not describe the requested seven-night stay.",
        )
        flight_source(
            outbound,
            origin=MOSCOW_AIRPORTS,
            destination={destination},
            on_date=self.departure_date,
        )
        flight_source(
            inbound,
            origin={destination},
            destination=MOSCOW_AIRPORTS,
            on_date=check_out,
        )

    def gateway_ui(self):
        """A backend-only port or an unrelated HTML landing page is not the app."""
        with self.stage("gateway_ui_entrypoint"):
            headers, body = self.request("GET", "/")
            require(
                "text/html" in headers.get("Content-Type", "")
                and b'id="app"' in body
                and b"./app.js" in body
                and b"./config.js" in body,
                "The browser entrypoint does not load the travel application.",
            )
            headers, body = self.request("GET", "/config.js")
            match = re.fullmatch(
                rb"window\.NA_MALDIVY_CONFIG = Object\.freeze\((\{.*\})\);", body
            )
            config = json.loads(match.group(1)) if match else None
            require(
                "javascript" in headers.get("Content-Type", "")
                and config
                == {
                    "mode": "api",
                    "apiEnabled": True,
                    "chatEndpoint": "/api/chat",
                    "healthEndpoint": "/health",
                },
                "The browser must use the same-origin API, not the standalone prototype.",
            )
            _, entry = self.request("GET", "/app.js")
            _, app = self.request("GET", "/api-app.js")
            require(
                b"mountApiApp" in entry and b"ApiTripController" in app,
                "Active UI assets are missing.",
            )

    def verify_order_privacy(self, order):
        for path in (
            f"/api/checkout/orders/{order['order_id']}",
            order["voucher_path"],
            order["calendar_path"],
        ):
            self.request("GET", path, expected=422)
            owner = self.session_id
            try:
                self.session_id = "synthetic-unrelated-session"
                self.request("GET", path, capability=True, expected=404)
            finally:
                self.session_id = owner

    def verify_trip_changes(self, order):
        """Exercise the bug-prone result/checkout transition through public HTTP."""
        before_hash = digest(order)
        with self.step("criteria_change_preserves_paid_order"):
            events = self.chat("Так а если отмена и я хочу в питер")
            options, done = single(events, "options"), single(events, "done")
            snapshot = self.snapshot()
            require(
                options["kind"] == "trains"
                and options["items"]
                and done["stage"] == snapshot["stage"] == "trains_shown"
                and snapshot["brief"]["destination"] == "LED"
                and snapshot["brief"]["transport"] == "train"
                and snapshot["selected"] is None
                and snapshot["booking"] is None
                and snapshot["stale"] is False
                and snapshot["options"]["kind"] == "trains"
                and snapshot["options"]["items"] == options["items"],
                "New destination did not replace old cards and clear the active checkout.",
            )
            require(
                not any(event["type"] == "booking" for event in events),
                "Train search created a checkout.",
            )
        with self.step("reverse_train_search"):
            events = self.chat(
                f"Теперь поездом из Петербурга в Москву {self.departure_date.isoformat()} плацкарт на двоих"
            )
            options = single(events, "options")
            require(
                options["kind"] == "trains" and options["items"],
                "Reverse train route has no options.",
            )
            for train in options["items"]:
                require(
                    train["origin"]["iata"] == "LED"
                    and train["destination"]["iata"] == "MOW"
                    and train["seat_class"] == "platzkart"
                    and train["is_synthetic"] is True
                    and train["price_rub"] > 0
                    and datetime.fromisoformat(train["departure_at"]).date()
                    == self.departure_date
                    and datetime.fromisoformat(train["arrival_at"])
                    > datetime.fromisoformat(train["departure_at"]),
                    "Train route, date, class, price or demo provenance is invalid.",
                )
            restored, _ = self.json(
                "GET", f"/api/checkout/orders/{order['order_id']}", capability=True
            )
            require(
                digest(restored) == before_hash, "A new search changed the paid order."
            )
        self.report["criteria_changes"] = {
            "paid_order_preserved": True,
            "train_directions_checked": 2,
        }

    def downloads(self, order):
        hashes = {}
        for key, name in (("voucher_path", "voucher"), ("calendar_path", "calendar")):
            headers, content = self.request("GET", order[key], capability=True)
            require(
                headers.get("Cache-Control") == "no-store", "Artifact must be private"
            )
            require(
                "attachment" in headers.get("Content-Disposition", ""),
                "Expected downloadable artifact",
            )
            text = content.decode()
            require("ДЕМО" in text, "Artifact lacks demo notice")
            if name == "voucher":
                require(
                    "билеты не выписаны" in text and "не забронированы" in text,
                    "Voucher must not claim real booking",
                )
                require("return_transfer" in text, "Unpriced return transfer omitted")
            else:
                unfolded = text.replace("\r\n ", "")
                require(unfolded.count("BEGIN:VEVENT") == 3, "Calendar event count")
                for direction in ("outbound", "inbound"):
                    leg = order["package"][direction]
                    start = (
                        datetime.fromisoformat(leg["departure_at"])
                        .astimezone(UTC)
                        .strftime("%Y%m%dT%H%M%SZ")
                    )
                    end = (
                        datetime.fromisoformat(leg["arrival_at"])
                        .astimezone(UTC)
                        .strftime("%Y%m%dT%H%M%SZ")
                    )
                    require(
                        "DTSTART:" + start in unfolded and "DTEND:" + end in unfolded,
                        "Calendar must retain UTC flight instants",
                    )
                require(
                    bool(re.search(r"DTSTART;VALUE=DATE:\d{8}", unfolded)),
                    "Hotel all-day event missing",
                )
            hashes[name + "_sha256"] = digest(content)
        return hashes

    def initial(self):
        self.gateway_ui()
        with self.stage("gateway_health"):
            health, _ = self.json("GET", "/health")
            require(
                health == {"status": "ok", "llm_provider": "fake"},
                "Expected offline fake model",
            )
            self.report["external_model_used"] = False

        with self.stage("search_with_source_evidence"):
            events = self.chat(
                f"Из Москвы в Анталью {self.departure_date.isoformat()} на неделю вдвоём"
            )
            options = single(events, "options")
            require(options["kind"] == "packages" and options["items"], "No packages")
            safe = [row for row in options["items"] if row.get("can_confirm") is True]
            require(safe, "No confirmable trip")
            selected = safe[0]
            self.verify_package(selected, "AYT")
            prices = []
            for direction, expected in (("outbound", 11251), ("inbound", 14147)):
                flight = selected[direction]
                source = flight_source(
                    flight, "provider_replay" if self.reference_fixture else None
                )
                if self.reference_fixture:
                    require(
                        source.get("seat_price_rub") == expected,
                        "Recorded fare changed",
                    )
                prices.append(source.get("seat_price_rub"))
            self.report.update(
                seat_quotes_rub=prices,
                known_total_rub=selected["known_total_rub"],
                package_snapshot_sha256=digest(selected),
            )

        with self.stage("d7_synthetic_review_evidence"):
            events = self.chat(
                f"Почему вариант {selected['index']} подходит семье? Что говорят отзывы?"
            )
            facts = single(events, "hotel_profile")
            profile = selected["hotel"]["review_profile"]
            require(
                facts.get("status") == "available"
                and facts.get("is_synthetic") is True
                and facts["hotel_id"] == selected["hotel"]["hotel_id"],
                "D7 wrong hotel or missing profile",
            )
            require(
                facts["extraction"]["live_model_run"] is False and facts["evidence"],
                "D7 unsupported model claim",
            )
            guest = profile["guest_profiles"]["family"]
            require(
                facts["segment_review_count"] == guest["review_count"],
                "D7 segment count",
            )
            evidence = [
                row
                for aspect in guest["aspects"].values()
                for row in aspect["evidence"]
            ]
            require(
                all(
                    any(
                        row["review_id"] == source["review_id"]
                        and row["quote"] == source["quote"]
                        for source in evidence
                    )
                    for row in facts["evidence"]
                ),
                "D7 non-literal evidence",
            )
            self.report["d7_profile_evidence_sha256"] = digest(facts)

        with self.stage("d8_comparison_arithmetic_and_state"):
            before = self.snapshot()
            events = self.chat(
                f"Для варианта {selected['index']}: если сдвинуть даты на 1–3 дня, можно сэкономить?"
            )
            facts = single(events, "date_savings")
            require(
                self.snapshot() == before and facts["state_changed"] is False,
                "D8 changed the trip",
            )
            quoted = [
                row
                for row in facts.get("checked_dates", [])
                if row["status"] == "quoted"
            ]
            require(
                facts["comparison_status"] == ("compared" if quoted else "unavailable"),
                "D8 inaccurate status",
            )
            baseline = facts["baseline"]
            by_date = {}
            for row in quoted:
                quote = row["quote"]
                require(
                    quote["hotel_id"] == baseline["hotel_id"]
                    and quote["travelers"] == baseline["travelers"]
                    and quote["nights"] == baseline["nights"] == 7,
                    "D8 incompatible quote",
                )
                require(
                    sum(
                        quote[key]
                        for key in (
                            "flight_total_rub",
                            "hotel_total_rub",
                            "other_total_rub",
                        )
                    )
                    == row["known_total_rub"],
                    "D8 component arithmetic",
                )
                by_date[row["departure_date"]] = row["known_total_rub"]
            for saving in facts.get("suggestions", []):
                require(
                    saving["suggested_departure"] in by_date
                    and saving["suggested_total_rub"]
                    == by_date[saving["suggested_departure"]],
                    "D8 missing quote",
                )
                require(
                    saving["original_total_rub"] == selected["known_total_rub"]
                    and saving["saving_rub"]
                    == saving["original_total_rub"] - saving["suggested_total_rub"]
                    and saving["saving_rub"] * 100 > saving["original_total_rub"] * 5,
                    "D8 incorrect savings",
                )
            require(len(quoted) == 6, "Frozen demo must compare six neighboring dates")
            expected_savings = [
                row
                for row in facts.get("suggestions", [])
                if row["suggested_departure"] == "2026-11-16"
                and row["suggested_total_rub"] == 71086
                and row["saving_rub"] == 12148
            ]
            if self.reference_fixture:
                require(len(expected_savings) == 1, "Frozen demo D8 saving missing")
            self.report["d8"] = {
                "status": facts["comparison_status"],
                "comparable_dates": len(quoted),
                "suggestions_count": len(facts.get("suggestions", [])),
                "state_preserved": True,
                "facts_sha256": digest(facts),
            }

        with self.stage("select_and_confirm_unpaid_cart"):
            events = self.chat(f"Беру вариант {selected['index']}")
            require(
                single(events, "done")["stage"] == "awaiting_confirmation",
                "Selection stage",
            )
            events = self.chat("Подтверждаю")
            cart = single(events, "booking")
            require(
                cart["status"] == "awaiting_travelers" and cart["paid"] is False,
                "Cart already marked paid",
            )
            cart_path = f"/api/checkout/carts/{cart['cart_id']}"

        with self.stage("synthetic_travelers"):
            travelers = [
                {
                    "full_name": "SYNTHETIC DEMO Traveler One",
                    "birth_date": "1990-01-01",
                },
                {
                    "full_name": "SYNTHETIC DEMO Traveler Two",
                    "birth_date": "1991-01-01",
                },
            ]
            result, _ = self.json(
                "PUT",
                cart_path + "/travelers",
                {"session_id": self.session_id, "travelers": travelers},
            )
            require(result["status"] == "ready_for_demo_payment", "Traveler stage")

        payment = {
            "session_id": self.session_id,
            "outcome": "declined",
            "idempotency_key": "gateway-decline-001",
            "acknowledge_demo": True,
            "acknowledge_unpriced": True,
        }
        with self.stage("deliberate_payment_decline"):
            result, _ = self.json("POST", cart_path + "/pay", payment)
            require(
                result["status"] == "payment_declined" and "order_id" not in result,
                "Decline created an order",
            )
        with self.stage("approved_simulated_payment_and_retry"):
            payment.update(outcome="approved", idempotency_key="gateway-approved-001")
            order, _ = self.json("POST", cart_path + "/pay", payment)
            require(
                order["status"] == "paid_demo"
                and order["is_simulated"] is True
                and order["package"] == selected,
                "Paid snapshot changed",
            )
            repeated, _ = self.json("POST", cart_path + "/pay", payment)
            require(repeated == order, "Payment retry changed the order")

        with self.stage("private_order_and_downloads"):
            restored, headers = self.json(
                "GET", f"/api/checkout/orders/{order['order_id']}", capability=True
            )
            require(
                restored == order and headers.get("Cache-Control") == "no-store",
                "Order read mismatch",
            )
            hashes = self.downloads(order)
            self.report.update(order_snapshot_sha256=digest(order), **hashes)
        with self.stage("order_capability_boundaries"):
            self.verify_order_privacy(order)
        self.verify_trip_changes(order)
        with self.stage("save_private_restart_context"):
            atomic_private_json(
                self.context_path,
                {
                    "schema_version": 1,
                    "base_url": self.base_url,
                    "departure_date": self.departure_date.isoformat(),
                    "session_id": self.session_id,
                    "order_id": order["order_id"],
                    "order_sha256": digest(order),
                    "download_hashes": hashes,
                },
            )
        self.report["status"] = "passed"

    def resume(self):
        with self.stage("load_private_context"):
            require(
                self.context_path.stat().st_mode & 0o077 == 0,
                "Private context permissions",
            )
            context = json.loads(self.context_path.read_text())
            require(
                context["base_url"] == self.base_url and context["schema_version"] == 1,
                "Context target mismatch",
            )
            self.session_id = context["session_id"]
            self.restore_scenario(context)
        with self.stage("gateway_health_after_restart"):
            health, _ = self.json("GET", "/health")
            require(
                health == {"status": "ok", "llm_provider": "fake"},
                "Restarted offline gateway health",
            )
            self.report["external_model_used"] = False
        with self.stage("volatile_session_absent_after_restart"):
            self.request("GET", f"/api/session/{self.session_id}", expected=404)
        with self.stage("durable_order_after_restart"):
            order, headers = self.json(
                "GET", f"/api/checkout/orders/{context['order_id']}", capability=True
            )
            require(
                headers.get("Cache-Control") == "no-store"
                and digest(order) == context["order_sha256"],
                "Persisted order changed",
            )
            self.report["order_snapshot_sha256"] = digest(order)
        with self.stage("identical_downloads_after_restart"):
            hashes = self.downloads(order)
            require(hashes == context["download_hashes"], "Persisted artifacts changed")
            self.report.update(**hashes)
        self.report["status"] = "passed"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url", default="http://localhost:8080", type=gateway_argument
    )
    parser.add_argument(
        "--departure-date",
        type=departure_date,
        help="Future YYYY-MM-DD; defaults to the reference fixture while future, then an honest generated demo.",
    )
    parser.add_argument(
        "--output", type=Path, default=Path("/tmp/na-maldivy-running-demo-report.json")
    )
    parser.add_argument(
        "--context",
        type=Path,
        default=Path("/tmp/na-maldivy-running-demo-context.json"),
    )
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    check = Check(args.base_url, args.context, args.resume, args.departure_date)
    started = time.perf_counter()
    try:
        check.resume() if args.resume else check.initial()
    # HTTP bodies and exception messages may contain session capabilities: expose only the class.
    except Exception as error:  # noqa: BLE001
        check.report.update(
            status="failed",
            failed_stage=check.current_stage,
            error_type=type(error).__name__,
        )
        if isinstance(error, (VerificationError, HTTPStatusError)):
            check.report["error"] = str(error)
        elif isinstance(error, (OSError, urllib.error.URLError)):
            check.report["hint"] = (
                "Start the default Docker Compose application and wait for http://localhost:8080/health."
            )
    check.report["duration_seconds"] = round(time.perf_counter() - started, 3)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(check.report, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps(check.report, ensure_ascii=False))
    raise SystemExit(0 if check.report["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
