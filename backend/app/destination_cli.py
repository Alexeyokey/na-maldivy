"""Подбор направлений D5 по сохранённому справочнику, без сети и LLM."""

import argparse
import json
from datetime import date

from app.tools.destinations import Preferences, load_catalogue, resolve_iata, suggest_destinations


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--month", type=int)
    parser.add_argument(
        "--departure-date", type=date.fromisoformat, help="YYYY-MM-DD: проверка визовых сроков на даты поездки"
    )
    parser.add_argument("--origin", default="MOW")
    parser.add_argument("--tag", action="append", default=[])
    parser.add_argument("--min-water", type=float)
    parser.add_argument("--max-rain", type=float)
    parser.add_argument("--max-flight-minutes", type=int)
    parser.add_argument("--visa-free", action="store_true")
    parser.add_argument("--budget", type=int)
    parser.add_argument("--nights", type=int, default=7)
    parser.add_argument("--travelers", type=int, default=1)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--data-path")
    parser.add_argument("--resolve", help="Найти IATA по названию/alias")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.resolve is not None:
        print(json.dumps(resolve_iata(args.resolve, data_path=args.data_path), ensure_ascii=False))
        return
    if args.month is None:
        parser.error("Укажите --month или --resolve")
    response = suggest_destinations(
        args.month,
        Preferences(
            tags=tuple(args.tag),
            min_water_temp_c=args.min_water,
            max_precipitation_mm=args.max_rain,
            max_flight_minutes=args.max_flight_minutes,
            visa_free_only=args.visa_free,
        ),
        origin=args.origin,
        budget=args.budget,
        nights=args.nights,
        travelers=args.travelers,
        limit=args.limit,
        departure_date=args.departure_date,
        data_path=args.data_path,
    )
    if args.json:
        print(response.model_dump_json(indent=2))
        return
    catalogue = load_catalogue(args.data_path)
    print(f"Справочник: {len(catalogue.destinations)} направлений; срез {response.catalogue_as_of}.")
    print(f"Подходит: {response.matched_count}; показано: {len(response.candidates)}. Месяц: {args.month}.")
    print("Направление | IATA | Воздух °C | Вода °C | Осадки мм | Полёт ≈мин | Виза | Риски | Балл")
    visa_labels = {"domestic": "РФ", "exempt": "безвиз*", "unknown": "неизвестно"}
    for item in response.candidates:
        air = "—" if item.climate.air_temp_c is None else f"{item.climate.air_temp_c:.1f}"
        water = "—" if item.climate.water_temp_c is None else f"{item.climate.water_temp_c:.1f}"
        flight = "—" if item.flight_minutes_estimate is None else str(item.flight_minutes_estimate)
        rain = "—" if item.climate.precipitation_mm is None else f"{item.climate.precipitation_mm:.0f}"
        visa = visa_labels.get(item.destination.visa.regime, item.destination.visa.regime)
        risks = ",".join(risk.code for risk in item.risks) or "—"
        print(
            f"{item.destination.name} | {item.destination.iata} | {air} | {water} | {rain} | "
            f"{flight} | {visa} | {risks} | {item.score}"
        )
    if not response.candidates:
        counts: dict[str, int] = {}
        for excluded in response.excluded:
            for reason in excluded.reasons:
                counts[reason] = counts.get(reason, 0) + 1
        print("Причины исключения: " + json.dumps(counts, ensure_ascii=False, sort_keys=True))
    print("Климат — нормы 1991–2020; время полёта — географическая оценка, наличие рейса не подтверждено.")
    print("Цены ожидают D2/D3. Визовые правила и условия приведены в --json; неизвестное не означает безвизовый въезд.")


if __name__ == "__main__":
    main()
