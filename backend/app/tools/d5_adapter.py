"""Project the sourced D5 catalogue onto the shared D1 boundary."""

from pathlib import Path

from pydantic import TypeAdapter

from app.contracts.tools import Destination, DestinationQuery, Place
from app.data_paths import resolve_data_dir
from app.tools.destinations import Catalogue, Preferences, suggest_destinations


class DestinationTool:
    name = "suggest_destinations"
    description = "Направления по климату и визовой справке; неизвестные цены и факты возвращаются как null."
    input_model = DestinationQuery
    output_adapter = TypeAdapter(list[Destination])

    def __init__(self, *, data_dir: Path | None = None, catalogue: Catalogue | None = None):
        self.data_dir = resolve_data_dir(data_dir)
        self.catalogue = catalogue

    async def run(self, input: DestinationQuery) -> list[Destination]:
        response = suggest_destinations(
            input.month,
            Preferences(min_water_temp_c=input.min_water_temperature_c, visa_free_only=input.visa_free_only),
            origin=input.origin,
            budget=input.budget_rub,
            nights=input.nights,
            travelers=input.travelers.count,
            limit=100,
            catalogue=self.catalogue,
            data_path=self.data_dir / "destinations.yaml" if self.catalogue is None else None,
        )
        result = []
        for item in response.candidates:
            record = item.destination
            visa = record.visa
            known_exemption = visa.regime == "domestic" or (
                visa.regime == "exempt" and visa.verification_status == "official_verified"
            )
            required = (
                False if known_exemption else True if visa.regime in {"required", "evisa", "on_arrival"} else None
            )
            risks = [*item.warnings, *(risk.message for risk in item.risks), visa.conditions]
            risks.append(
                "Время перелёта — оценка расстояния, не подтверждение рейса. Цена не включает трансферы и доплаты."
            )
            if input.travelers.pets or input.travelers.children_ages:
                risks.append("Справочная оценка цены не учитывает индивидуальные детские тарифы и доплаты за животных.")
            result.append(
                Destination(
                    place=Place(
                        kind="city", id=record.id, iata=record.iata, name=record.name, **record.location.model_dump()
                    ),
                    country=record.country,
                    month=input.month,
                    air_temperature_c=item.climate.air_temp_c,
                    water_temperature_c=item.climate.water_temp_c,
                    flight_duration_minutes=item.flight_minutes_estimate,
                    visa_required=required,
                    estimated_total_price_rub=item.trip_cost_estimate_rub,
                    tags=list(record.tags),
                    risks=risks,
                    # Geographic flight duration and group/room estimates are modelled.
                    is_synthetic=True,
                )
            )
        return result
