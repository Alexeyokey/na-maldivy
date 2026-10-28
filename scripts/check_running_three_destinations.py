"""Check a running three-destination mixed demo; --resume verifies orders after restart.

Pure stdlib, using the adjacent check_running_demo.py transport/privacy helpers.
No .env, application imports, credentials, access IDs or traveler data in reports.
"""

import argparse
import json
import time
from pathlib import Path

from check_running_demo import (
    Check,
    HTTPStatusError,
    VerificationError,
    atomic_private_json,
    departure_date,
    digest,
    gateway_argument,
    require,
    single,
)
from check_running_demo import (
    flight_source as source,
)

ROUTES = (
    ("AYT", "Анталью", 83234, ("provider_replay", "provider_replay")),
    ("DXB", "Дубай", 131161, ("deterministic_mock", "provider_replay")),
    ("MLE", "Мале", 216983, ("provider_replay", "provider_replay")),
)
TRAVELERS = [
    {"full_name": "SYNTHETIC DEMO Adult One", "birth_date": "1990-01-01"},
    {"full_name": "SYNTHETIC DEMO Adult Two", "birth_date": "1991-01-01"},
]


class ThreeCheck(Check):
    def __init__(self, base_url, context_path, resume, trip_date=None):
        super().__init__(base_url, context_path, resume, trip_date)
        self.report.update(
            scope="running_gateway_three_destination_mixed_backend_simulated_checkout",
            full_d9_acceptance_status="not_completed_by_this_report",
            destinations=[],
        )
        self.route = "gateway"

    def step(self, name):
        return self.stage(self.route + "." + name)

    def health(self):
        with self.stage("gateway.health"):
            health, _ = self.json("GET", "/health")
            require(
                health == {"status": "ok", "llm_provider": "fake"},
                "Offline rules/model configuration changed",
            )
            self.report["external_model_used"] = False

    def downloads(self, order):
        hashes = super().downloads(order)
        _, content = self.request("GET", order["voucher_path"], capability=True)
        text = content.decode()
        for title, leg in (("Туда", "outbound"), ("Обратно", "inbound")):
            section = text.split(f"<li>{title}:", 1)[1].split("</li>", 1)[0]
            if source(order["package"][leg])["kind"] == "deterministic_mock":
                require(
                    "ДЕМО-МОК" in section
                    and "не котировка Aviasales" in section
                    and "Aviasales D4" not in section,
                    "Voucher labels a mock as provider data",
                )
            else:
                require(
                    "Aviasales D4" in section and "не подтверждены" in section,
                    "Voucher source missing",
                )
        return hashes

    def profile(self, selected):
        before = self.snapshot()
        facts = single(
            self.chat(
                f"Почему вариант {selected['index']} подходит семье? Что говорят отзывы?"
            ),
            "hotel_profile",
        )
        require(self.snapshot() == before, "D7 changed trip state")
        require(
            facts.get("status") == "available"
            and facts.get("is_synthetic") is True
            and facts["hotel_id"] == selected["hotel"]["hotel_id"]
            and facts["extraction"]["live_model_run"] is False,
            "Wrong D7 profile or model-run claim",
        )
        guest = selected["hotel"]["review_profile"]["guest_profiles"]["family"]
        require(
            facts["counts"]
            and facts["evidence"]
            and facts["segment_review_count"] == guest["review_count"],
            "D7 profile evidence must be nonempty",
        )
        for row in facts["counts"]:
            expected = guest["aspects"][row["aspect"]]
            require(
                row["positive_count"] == expected["positive_count"]
                and row["negative_count"] == expected["negative_count"],
                "D7 count changed",
            )
        evidence = [
            row for aspect in guest["aspects"].values() for row in aspect["evidence"]
        ]
        require(
            all(
                any(
                    row["quote"] == original["quote"]
                    and row["review_id"] == original["review_id"]
                    for original in evidence
                )
                for row in facts["evidence"]
            ),
            "D7 literal evidence changed",
        )
        return {
            "is_synthetic": True,
            "live_model_run": False,
            "facts_sha256": digest(facts),
        }

    def comparison(self, selected):
        before = self.snapshot()
        events = self.chat(
            f"Для варианта {selected['index']}: если сдвинуть даты на 1–3 дня, можно сэкономить?"
        )
        facts = single(events, "date_savings")
        require(
            self.snapshot() == before
            and facts["state_changed"] is False
            and facts["demo_mode"] is True,
            "D8 changed trip state or lacks demo mode",
        )
        quoted = [
            row for row in facts.get("checked_dates", []) if row["status"] == "quoted"
        ]
        require(
            quoted and facts["comparison_status"] == "compared",
            "D8 cannot pass with no comparable dates",
        )
        require(
            facts["baseline_known_total_rub"] == selected["known_total_rub"]
            and facts["price_scope"] == selected["price_scope"]
            and facts["unpriced_components"] == selected["unpriced_components"],
            "D8 price scope changed",
        )
        baseline, by_date = facts["baseline"], {}
        baseline_mock = any(
            source(selected[leg])["kind"] == "deterministic_mock"
            for leg in ("outbound", "inbound")
        )
        for row in quoted:
            quote = row["quote"]
            require(
                quote["hotel_id"]
                == baseline["hotel_id"]
                == selected["hotel"]["hotel_id"]
                and quote["travelers"]
                == baseline["travelers"]
                == {"adults": 2, "children_ages": [], "pets": 0}
                and quote["nights"] == baseline["nights"] == 7,
                "D8 incompatible party/hotel/stay",
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
                "D8 component arithmetic changed",
            )
            kinds = [source(leg)["kind"] for leg in row["flight_sources"].values()]
            require(
                len(kinds) == 2
                and row["contains_mock"] == ("deterministic_mock" in kinds),
                "D8 sources changed",
            )
            by_date[row["departure_date"]] = row
        contains_mock = baseline_mock or any(row["contains_mock"] for row in quoted)
        require(
            facts["contains_mock"] == contains_mock, "D8 overall source flag changed"
        )
        if contains_mock:
            require(
                facts["savings_kind"] == "demonstration_estimate",
                "Mixed D8 must be labelled a demo estimate",
            )
            require(
                "мок"
                in "".join(
                    row["data"]["text"] for row in events if row["type"] == "delta"
                ).casefold(),
                "D8 text omitted mock disclosure",
            )
        for saving in facts["suggestions"]:
            require(
                saving["suggested_departure"] in by_date,
                "D8 saving has no complete quote",
            )
            candidate = by_date[saving["suggested_departure"]]
            require(
                saving["suggested_total_rub"] == candidate["known_total_rub"]
                and saving["original_total_rub"] == selected["known_total_rub"]
                and saving["saving_rub"]
                == saving["original_total_rub"] - saving["suggested_total_rub"]
                and saving["saving_rub"] * 100 > saving["original_total_rub"] * 5,
                "D8 saving arithmetic changed",
            )
            mock = baseline_mock or candidate["contains_mock"]
            require(
                saving["contains_mock"] == mock
                and (not mock or saving["savings_kind"] == "demonstration_estimate"),
                "D8 saving lacks mixed-source disclosure",
            )
        return {
            "comparable_dates": len(quoted),
            "suggestions_count": len(facts["suggestions"]),
            "contains_mock": contains_mock,
            "state_preserved": True,
            "facts_sha256": digest(facts),
        }

    def initial(self):
        self.gateway_ui()
        self.health()
        contexts = []
        for code, destination, total, expected in ROUTES:
            self.route, self.session_id = code, None
            public = {"destination": code, "status": "failed"}
            self.report["destinations"].append(public)
            with self.step("search_sources"):
                options = single(
                    self.chat(
                        f"Из Москвы в {destination} {self.departure_date.isoformat()} на неделю вдвоём"
                    ),
                    "options",
                )
                require(
                    options["kind"] == "packages" and options["items"], "No trip cards"
                )
                safe = [
                    row
                    for row in options["items"]
                    if row.get("can_confirm") is True
                    and not any(
                        issue["severity"] == "critical" for issue in row["issues"]
                    )
                ]
                require(safe, "Unsafe checkout cannot be forced")
                selected = safe[0]
                self.verify_package(selected, code)
                require(
                    selected["known_total_rub"] == selected["total_rub"] > 0,
                    "Known primary price is missing",
                )
                if self.reference_fixture:
                    require(
                        selected["known_total_rub"] == total,
                        "Frozen primary price changed",
                    )
                sources = [
                    source(selected[leg], kind if self.reference_fixture else None)
                    for leg, kind in zip(("outbound", "inbound"), expected, strict=True)
                ]
                if code == "AYT" and self.reference_fixture:
                    require(
                        [row["seat_price_rub"] for row in sources] == [11251, 14147],
                        "Antalya seat prices changed",
                    )
                public.update(
                    known_total_rub=selected["known_total_rub"],
                    leg_sources=sources,
                    package_sha256=digest(selected),
                )
            with self.step("d7_evidence"):
                public["d7"] = self.profile(selected)
            with self.step("d8_comparison"):
                public["d8"] = self.comparison(selected)
            with self.step("confirm_unpaid_cart"):
                require(
                    single(self.chat(f"Беру вариант {selected['index']}"), "done")[
                        "stage"
                    ]
                    == "awaiting_confirmation",
                    "Selection did not require confirmation",
                )
                cart = single(self.chat("Подтверждаю"), "booking")
                require(
                    cart["status"] == "awaiting_travelers" and cart["paid"] is False,
                    "Confirmation paid prematurely",
                )
                path = f"/api/checkout/carts/{cart['cart_id']}"
                result, _ = self.json(
                    "PUT",
                    path + "/travelers",
                    {"session_id": self.session_id, "travelers": TRAVELERS},
                )
                require(
                    result["status"] == "ready_for_demo_payment",
                    "Traveler data did not match group",
                )
            with self.step("decline_approve_retry"):
                payment = {
                    "session_id": self.session_id,
                    "outcome": "declined",
                    "idempotency_key": "three-http-declined-001",
                    "acknowledge_demo": True,
                    "acknowledge_unpriced": True,
                }
                result, _ = self.json("POST", path + "/pay", payment)
                require(
                    result["status"] == "payment_declined" and "order_id" not in result,
                    "Decline created an order",
                )
                payment.update(
                    outcome="approved", idempotency_key="three-http-approved-001"
                )
                order, _ = self.json("POST", path + "/pay", payment)
                require(
                    order["status"] == "paid_demo"
                    and order["is_simulated"] is True
                    and order["package"] == selected,
                    "Simulated order changed its server snapshot",
                )
                repeated, _ = self.json("POST", path + "/pay", payment)
                require(repeated == order, "Payment retry produced a changed order")
            with self.step("private_downloads"):
                restored, headers = self.json(
                    "GET", f"/api/checkout/orders/{order['order_id']}", capability=True
                )
                require(
                    restored == order and headers.get("Cache-Control") == "no-store",
                    "Private order read changed",
                )
                hashes = self.downloads(order)
                public.update(order_sha256=digest(order), **hashes)
                contexts.append(
                    {
                        "destination": code,
                        "session_id": self.session_id,
                        "order_id": order["order_id"],
                        "order_sha256": digest(order),
                        "download_hashes": hashes,
                    }
                )
            with self.step("order_capability_boundaries"):
                self.verify_order_privacy(order)
            self.verify_trip_changes(order)
            public["status"] = "passed"
        with self.stage("gateway.save_private_context"):
            atomic_private_json(
                self.context_path,
                {
                    "schema_version": 1,
                    "base_url": self.base_url,
                    "departure_date": self.departure_date.isoformat(),
                    "orders": contexts,
                },
            )
        self.report.update(status="passed", destinations_passed=3)

    def resume(self):
        with self.stage("gateway.load_private_context"):
            require(
                self.context_path.stat().st_mode & 0o077 == 0, "Context must be private"
            )
            context = json.loads(self.context_path.read_text())
            require(
                context["schema_version"] == 1 and context["base_url"] == self.base_url,
                "Context target mismatch",
            )
            require(
                len(context["orders"]) == 3
                and {row["destination"] for row in context["orders"]}
                == {row[0] for row in ROUTES},
                "Context must contain all three orders",
            )
        self.restore_scenario(context)
        self.health()
        for row in context["orders"]:
            self.route, self.session_id = row["destination"], row["session_id"]
            with self.step("restart_order_and_artifacts"):
                self.request("GET", f"/api/session/{self.session_id}", expected=404)
                order, headers = self.json(
                    "GET", f"/api/checkout/orders/{row['order_id']}", capability=True
                )
                require(
                    headers.get("Cache-Control") == "no-store"
                    and digest(order) == row["order_sha256"],
                    "Durable order changed after restart",
                )
                hashes = self.downloads(order)
                require(
                    hashes == row["download_hashes"], "Downloads changed after restart"
                )
                self.report["destinations"].append(
                    {
                        "destination": self.route,
                        "status": "passed",
                        "old_session_absent": True,
                        "order_sha256": digest(order),
                        **hashes,
                    }
                )
        self.report.update(status="passed", destinations_passed=3)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url", default="http://localhost:8080", type=gateway_argument
    )
    parser.add_argument(
        "--departure-date",
        type=departure_date,
        help="Future YYYY-MM-DD; generated data is always labelled honestly.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("/tmp/na-maldivy-three-destinations-report.json"),
    )
    parser.add_argument(
        "--context",
        type=Path,
        default=Path("/tmp/na-maldivy-three-destinations-private.json"),
    )
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    check = ThreeCheck(args.base_url, args.context, args.resume, args.departure_date)
    started = time.perf_counter()
    try:
        check.resume() if args.resume else check.initial()
    # Exception text and HTTP bodies may contain capabilities: expose only the class.
    except Exception as error:  # noqa: BLE001
        check.report.update(
            status="failed",
            failed_stage=check.current_stage,
            error_type=type(error).__name__,
        )
        if isinstance(error, (VerificationError, HTTPStatusError)):
            check.report["error"] = str(error)
        elif isinstance(error, OSError):
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
