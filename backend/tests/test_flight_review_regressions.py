import shutil

import httpx
import pytest
from test_flights import MockFlightHttpClient, aviasales_fixture, flight_query

from app.contracts.tools import FlightSearch, Travelers
from app.data_paths import DEFAULT_DATA_DIR
from app.tools.flights import AviasalesDataClient, FlightTool, group_fare
from app.tools.transport import ProviderResponseError


@pytest.mark.parametrize("age", [0, 1, 2])
async def test_infant_fare_and_baggage_boundary(age):
    query = flight_query(travelers=Travelers(adults=1))
    solo = await FlightTool().run(query)
    family = await FlightTool().run(query.model_copy(update={"travelers": Travelers(adults=1, children_ages=[age])}))
    for adult, child in zip(solo, family, strict=True):
        assert (
            child.price_rub == (adult.price_rub * 11 + 5) // 10 if age < 2 else child.price_rub == adult.price_rub * 2
        )
        assert child.baggage_price_rub == (2000 if age < 2 else 4000)
        assert child.id != adult.id
    assert group_fare(15, Travelers(adults=1, children_ages=[0], pets=1)) == 1517


@pytest.mark.parametrize("status", [401, 403, 429, 503])
async def test_http_failure_has_common_safe_error(status):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(status, text="private-body"))
    ) as source:
        with pytest.raises(ProviderResponseError) as error:
            await AviasalesDataClient(MockFlightHttpClient(source), token="private-key").prices_for_dates(
                flight_query()
            )
    assert "private" not in str(error.value)
    assert error.value.__suppress_context__


async def test_cached_provider_quote_keeps_single_seat_price_and_models_infant_separately():
    payload = aviasales_fixture()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as source:
        query = flight_query(travelers=Travelers(adults=1, children_ages=[1]))
        result = await FlightTool(
            mode="provider", http=MockFlightHttpClient(source), token="TEST"
        ).search_with_provenance(query)
    assert result.flights and result.quotes
    assert result.flights[0].price_rub == (result.quotes[0].price * 11 + 5) // 10
    assert result.flights[0].baggage_price_rub == 2000
    assert "10%" in result.provenance[0]["group_price"]


async def test_custom_data_root_cannot_fall_back_to_ordinary_routes(tmp_path):
    shutil.copytree(DEFAULT_DATA_DIR / "routes", tmp_path / "routes")
    (tmp_path / "routes/network.json").write_text('{"airports": {}, "routes": []}', encoding="utf-8")
    result = await FlightTool(data_dir=tmp_path).run(
        FlightSearch(
            origin="SVO",
            destination="MLE",
            departure_date="2026-11-18",
        )
    )
    assert result == []
