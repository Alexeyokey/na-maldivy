"""S4 airport → hotel quotes exposed through the public D1 tool contract.

Place facts come from the checked-in OSM catalogues on the server. S4 prices,
hours and commercial restrictions are synthetic, so every quote stays marked
is_synthetic even when its route was measured by OSRM.
"""

import json
from datetime import UTC, timedelta
from pathlib import Path

from pydantic import TypeAdapter

from app.contracts.tools import Place, Transfer, TransferHours, TransferQuery, TransferRequest
from app.data_paths import resolve_data_dir
from app.tools.errors import UnsupportedTransfer
from app.tools.transfers import TransferBase, default_base, quote_transfers

MODE_MAP = {"private_minivan": "minivan", "speedboat": "boat"}


class S4TransferTool:
    name = "get_transfer"
    description = "Трансферы аэропорт → отель S4: все варианты, цена за группу, расписание и ограничения"
    input_model = TransferRequest
    output_adapter = TypeAdapter(list[Transfer])

    def __init__(self, *, base: TransferBase | None = None, data_dir: Path | None = None) -> None:
        self.base = base
        self.data_dir = resolve_data_dir(data_dir)

    async def run(self, input: TransferRequest) -> list[Transfer]:
        base = self.base if self.base is not None else default_base(self.data_dir)
        record = base.get(input.hotel_id, input.airport)
        airports = json.loads((self.data_dir / "transfers/airports.json").read_text(encoding="utf-8"))["airports"]
        hotels = json.loads((self.data_dir / "hotels/osm_hotels.json").read_text(encoding="utf-8"))["hotels"]
        airport = next((point for point in airports if point["iata"] == input.airport), None)
        hotel = next((point for point in hotels if point["hotel_id"] == input.hotel_id), None)
        if airport is None or hotel is None or hotel["destination"] != input.airport:
            raise UnsupportedTransfer(f"Нет точки в каталоге S4: {input.airport} → {input.hotel_id}")
        query = TransferQuery(
            origin=Place(
                kind="airport",
                iata=input.airport,
                name=airport["name"],
                latitude=airport["lat"],
                longitude=airport["lon"],
                timezone=airport["tz"],
            ),
            destination=Place(
                kind="hotel",
                id=input.hotel_id,
                iata=input.airport,
                name=hotel["name"],
                latitude=hotel["lat"],
                longitude=hotel["lon"],
                timezone=record.tz,
            ),
            pickup_at=input.pickup_at,
            travelers=input.travelers,
        )
        options = {option.id: option for option in record.options}
        result = []
        for quote in quote_transfers(input.hotel_id, input.airport, query.pickup_at, query.travelers, base=base):
            option = options[quote.option_id]
            result.append(
                Transfer(
                    id=quote.option_id,
                    origin=query.origin,
                    destination=query.destination,
                    pickup_at=query.pickup_at,
                    hours=TransferHours.model_validate(option.hours.model_dump(by_alias=True))
                    if option.hours
                    else None,
                    available=quote.available,
                    depart_at=quote.depart_at,
                    vehicles=quote.vehicles,
                    duration_minutes=quote.duration_min,
                    price_rub=quote.price_rub,
                    mode=MODE_MAP.get(quote.mode, quote.mode),
                    pets=option.pets,
                    child_seat=option.child_seat,
                    luggage_limit=option.luggage_limit,
                    prebook_hours=option.prebook_hours,
                    distance_km=quote.distance_km,
                    issues=list(quote.issues),
                    is_synthetic=True,
                )
            )
        return sorted(
            result,
            key=lambda item: (
                not item.available,
                bool({"pets_forbidden", "child_seat_missing"}.intersection(item.issues)),
                item.price_rub,
                item.id,
            ),
        )


def transfer_leg(transfer: Transfer):
    """Bridge a validated D1 answer to the existing S3 timeline/S5 validator."""
    from app.booking.timeline import TransferLeg

    if transfer.depart_at is None:
        raise ValueError("Нельзя построить участок поездки без известного времени отправления")
    reverse_modes = {"minivan": "private_minivan", "boat": "speedboat"}
    return TransferLeg(
        option_id=transfer.id,
        mode=reverse_modes.get(transfer.mode, transfer.mode),
        depart_at=transfer.depart_at,
        arrive_at=(transfer.depart_at.astimezone(UTC) + timedelta(minutes=transfer.duration_minutes)).astimezone(
            transfer.depart_at.tzinfo
        ),
        price_rub=transfer.price_rub,
        issues=list(transfer.issues),
    )
