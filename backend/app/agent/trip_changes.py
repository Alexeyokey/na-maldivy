"""Public comparisons of searched options; never an order, quote or savings claim."""

from typing import Any

from app.agent.brief import FIELD_LABELS, SEARCH_FIELDS, TripBrief


def money(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def option_summary(option: dict[str, Any] | None, brief: TripBrief) -> dict[str, Any] | None:
    if not option:
        return None
    kind = option.get("kind")
    common = {
        "kind": kind,
        "origin": brief.origin,
        "destination": brief.destination,
        "start_date": brief.start_date.isoformat() if brief.start_date else None,
        "travelers": brief.travelers.model_dump(mode="json"),
    }
    if kind == "train":
        source = option.get("schedule_source")
        return common | {
            "name": f"Поезд {option.get('number', '—')}",
            "label": option.get("seat_class"),
            "hotel": None,
            "check_in": None,
            "check_out": None,
            "total_rub": money(option.get("price_rub")),
            "known_total_rub": money(option.get("price_rub")),
            "price_scope": "group_one_way_train",
            "components": [
                {
                    "key": "train",
                    "label": "Поезд в одну сторону",
                    "known_cost_rub": money(option.get("price_rub")),
                    "source": {
                        "kind": "modelled" if option.get("is_synthetic") is True else "unknown",
                        "schedule_source": source,
                        "cache_mode": option.get("schedule_cache_mode"),
                    },
                }
            ],
            "unpriced_components": ["return_train", "other_required_extras"],
        }
    if kind != "package":
        return common | {
            "name": option.get("name"),
            "label": None,
            "hotel": None,
            "total_rub": None,
            "known_total_rub": None,
            "price_scope": "destination_reference",
            "check_in": None,
            "check_out": None,
            "components": [],
            "unpriced_components": ["complete_trip"],
        }
    # Flexible search cards have independent destinations and dates.
    common.update(
        origin=(option.get("outbound") or {}).get("from") or brief.origin,
        destination=option.get("destination") or brief.destination,
        start_date=((option.get("outbound") or {}).get("departure_at") or "")[:10] or common["start_date"],
    )
    components = []
    for key, label in (("outbound", "Перелёт туда"), ("inbound", "Перелёт обратно")):
        flight = option.get(key) or {}
        quote, demo = flight.get("price_source") or {}, flight.get("demo_source") or {}
        source = {
            "kind": "provider_quote_with_modelled_extras"
            if quote
            else "synthetic"
            if demo.get("kind") == "deterministic_mock" or option.get("is_synthetic") is True
            else "unknown"
        }
        if quote:
            source.update(
                provider=quote.get("provider"), cache_mode=quote.get("cache_mode"), recorded_at=quote.get("recorded_at")
            )
        components.append(
            {"key": key, "label": label, "known_cost_rub": money(flight.get("price_rub")), "source": source}
        )
        components.append(
            {
                "key": key + "_baggage",
                "label": "Багаж " + ("туда" if key == "outbound" else "обратно"),
                "known_cost_rub": money(flight.get("baggage_price_rub")),
                "source": {"kind": "modelled"},
            }
        )
    hotel, transfer = option.get("hotel") or {}, option.get("transfer")
    components += [
        {
            "key": "hotel",
            "label": "Отель за весь срок",
            "known_cost_rub": money(hotel.get("price_rub")),
            "source": {"kind": "synthetic" if option.get("is_synthetic") is True else "unknown"},
        },
        {
            "key": "arrival_transfer",
            "label": "Трансфер по прибытии",
            "known_cost_rub": money(transfer.get("price_rub")) if transfer else None,
            "source": {"kind": "modelled" if transfer else "unknown"},
        },
    ]
    return common | {
        "name": hotel.get("name"),
        "label": option.get("label"),
        "hotel": hotel.get("name"),
        "check_in": hotel.get("check_in"),
        "check_out": hotel.get("check_out"),
        "total_rub": money(option.get("total_rub")),
        "known_total_rub": money(option.get("known_total_rub")),
        "price_scope": option.get("price_scope"),
        "components": components,
        "unpriced_components": list(
            dict.fromkeys([*(option.get("unpriced_components") or []), *([] if transfer else ["arrival_transfer"])])
        ),
    }


def build_diff(
    *,
    before_brief: dict,
    after_brief: TripBrief,
    before: dict | None,
    after: dict | None,
    sources: dict,
    version: int,
    status: str,
    notes: list[str],
) -> dict:
    current = after_brief.model_dump(mode="json")
    changes = [
        {
            "field": field,
            "label": FIELD_LABELS[field],
            "before": before_brief.get(field),
            "after": current[field],
            "source": sources.get(field),
        }
        for field in FIELD_LABELS
        if field in SEARCH_FIELDS and before_brief.get(field) != current[field]
    ]
    differences = []
    if before and after:
        for label, keys in (
            ("transport", ("kind",)),
            ("hotel", ("hotel",)),
            ("route", ("origin", "destination")),
            ("dates", ("start_date", "check_in", "check_out")),
            ("travelers", ("travelers",)),
            ("components", ("price_scope", "unpriced_components")),
        ):
            if any(before.get(key) != after.get(key) for key in keys):
                differences.append(label)

        def known_components(side):
            return {row["key"]: row.get("known_cost_rub") is not None for row in side.get("components", [])}

        if known_components(before) != known_components(after) and "components" not in differences:
            differences.append("components")
    old_cost = money(before.get("known_total_rub")) if before else None
    new_cost = money(after.get("known_total_rub")) if after else None
    return {
        "schema_version": 1,
        "version": version,
        "status": status,
        "criteria_changes": changes,
        "before": before,
        "after": after,
        "comparison": {
            "delta_known_total_rub": new_cost - old_cost if old_cost is not None and new_cost is not None else None,
            "differences": differences,
            "same_basis": bool(
                before and after and not differences and before.get("price_scope") == after.get("price_scope")
            ),
            "savings_guaranteed": False,
            "notice": (
                "Сравниваются первые показанные варианты и известные расходы за указанную группу. "
                "Отель, даты, состав группы и компоненты могут различаться. "
                "Разница не гарантирует экономию; наличие и неоценённые доплаты не подтверждены."
            ),
        },
        "notes": list(notes),
    }
