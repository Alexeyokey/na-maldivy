"""Explicit offline demo: replay provider quotes, labelled mocks for absent data.

The strict Aviasales and synthetic tools keep their original behaviour. This
adapter is opt-in, never opens the network, and never conceals invalid/failed
provider responses. A generated DXB route is a mock, not a renamed DWC quote.
"""

from pathlib import Path

from app.contracts.tools import Flight, FlightDemoSource, FlightSearch
from app.tools.flights import FlightHttpClient, FlightSearchResult, FlightTool
from app.tools.http_cache import CacheMiss

PROVIDER_NOTICE = "Aviasales Data API: сохранённая котировка; наличие мест и цена покупки не подтверждены."
MOCK_NOTICE = "ДЕМО-МОК: расписание и цена сгенерированы; это не котировка Aviasales и не наличие мест."


class DemoFlightTool:
    name = FlightTool.name
    description = (
        "Явное офлайн-демо: сохранённые котировки Aviasales; при отсутствии подходящих данных — "
        "подписанные синтетические моки. Источник каждого рейса передаётся отдельно."
    )
    input_model = FlightTool.input_model
    output_adapter = FlightTool.output_adapter
    mode = "demo"

    def __init__(self, *, http: FlightHttpClient, data_dir: Path | None = None, seed: int = 42):
        if http.mode != "replay":
            raise ValueError("Demo flights require an offline replay client")
        self.http = http
        self.provider = FlightTool(mode="provider", http=http, token=None, data_dir=data_dir)
        self.synthetic = FlightTool(mode="synthetic", seed=seed, data_dir=data_dir)
        self.routes_path = self.synthetic.routes_path

    async def search_with_provenance(self, query: FlightSearch) -> FlightSearchResult:
        if self.http.mode != "replay":
            raise ValueError("Demo flights require an offline replay client")
        reason = "no_matching_quotes"
        try:
            recorded = await self.provider.search_with_provenance(query)
        except CacheMiss:
            reason = "cache_miss"
        else:
            if recorded.flights:
                source = FlightDemoSource(kind="provider_replay", notice=PROVIDER_NOTICE)
                return FlightSearchResult(
                    tuple(
                        Flight.model_validate(flight.model_copy(update={"demo_source": source.model_copy(deep=True)}))
                        for flight in recorded.flights
                    ),
                    recorded.quotes,
                    tuple({**row, "demo_source": source.model_dump(mode="json")} for row in recorded.provenance),
                )
            if recorded.quotes:
                # Matching quotes exist but fail a user constraint (e.g. group budget).
                # An invented cheaper offer must not bypass that real quote evidence.
                return recorded
        generated = await self.synthetic.search_with_provenance(query)
        source = FlightDemoSource(kind="deterministic_mock", reason=reason, notice=MOCK_NOTICE)
        flights = tuple(
            Flight.model_validate(
                flight.model_copy(
                    update={
                        "id": "demo-mock-" + flight.id,
                        "carrier": "ДЕМО-МОК (синтетические расписание и тариф)",
                        "demo_source": source.model_copy(deep=True),
                    }
                )
            )
            for flight in generated.flights
        )
        return FlightSearchResult(
            flights,
            (),
            tuple(
                {**row, "offer_id": flight.id, "demo_source": source.model_dump(mode="json")}
                for row, flight in zip(generated.provenance, flights, strict=True)
            ),
        )

    async def run(self, input: FlightSearch) -> list[Flight]:
        return list((await self.search_with_provenance(input)).flights)

    def public_metadata(self, result: list[Flight]) -> dict:
        return {
            "source": "offline_demo",
            "cache_mode": "replay",
            "contains_mock": any(row.demo_source.kind == "deterministic_mock" for row in result if row.demo_source),
            "flights": [
                {
                    "id": row.id,
                    "demo_source": row.demo_source.model_dump(mode="json") if row.demo_source else None,
                    "price_source": row.price_source.model_dump(mode="json") if row.price_source else None,
                }
                for row in result
            ],
        }
