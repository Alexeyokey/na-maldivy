import json
from datetime import UTC, date, timedelta

import httpx
import pytest

from app.contracts.tools import TrainSearch, Travelers
from app.tools.trains import TrainTool, YandexTimetableClient, generate_trains, train_price
from app.tools.transport import DATA_DIR, ProviderNotConfigured, ProviderResponseError


def train_query(**kwargs):
    return TrainSearch(origin="LED", destination="MOW", departure_date=date(2030, 11, 18), **kwargs)


def yandex_fixture():
    return json.loads((DATA_DIR / "trains" / "yandex_search.synthetic.json").read_text())["response"]


def cache_contents(path):
    return [item.read_text() for item in path.rglob("*.json")]


def test_night_route_rolls_date_and_train_points_are_not_airports():
    result = generate_trains(train_query())
    assert result == generate_trains(train_query())
    assert len(result) == 1
    assert len(generate_trains(train_query(seat_class="seated"))) == 2
    night = next(train for train in result if train.number.endswith("NIGHT"))
    assert night.arrival_at.date() == date(2030, 11, 19)
    assert night.arrival_at.astimezone(UTC) - night.departure_at.astimezone(UTC) == timedelta(hours=8)
    assert night.origin.latitude > 59.9  # Main station, not Pulkovo (59.8).
    assert night.destination.longitude > 37.6  # Leningradsky, not Sheremetyevo (37.4).
    assert all(train.is_synthetic for train in result)
    assert generate_trains(train_query().model_copy(update={"destination": "AYT"})) == []
    assert len(generate_trains(train_query().model_copy(update={"origin": "SVO", "destination": "LED"}))) == 1


def test_class_distance_season_group_and_pet_tariff():
    prices = [train_price(train_query(seat_class=seat)) for seat in ("seated", "platzkart", "coupe", "sv")]
    assert prices == sorted(prices) and len(set(prices)) == 4
    ordinary = train_price(train_query())
    assert train_price(train_query(), distance_km=1300) >= ordinary * 2
    summer = train_query().model_copy(update={"departure_date": date(2030, 7, 18)})
    assert 1.24 < train_price(summer) / ordinary < 1.26
    newyear = train_query().model_copy(update={"departure_date": date(2030, 12, 31)})
    assert 1.34 < train_price(newyear) / ordinary < 1.36
    family = train_query(travelers=Travelers(adults=2, children_ages=[4], pets=1))
    assert train_price(family) == ordinary * 3 + 500
    with pytest.raises(ValueError):
        train_price(train_query(), distance_km=0)


async def test_provider_pagination(tmp_path):
    calls = []

    def respond(request):
        calls.append(int(request.url.params["offset"]))
        payload = yandex_fixture()
        payload["pagination"]["total"] = 2
        payload["pagination"]["offset"] = calls[-1]
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as source:
        http = MockTrainHttpClient(source)
        assert len(await YandexTimetableClient(http, "TEST").segments(train_query())) == 2
    assert calls == [0, 1]


@pytest.mark.parametrize("error", ["duration", "naive", "failed", "pagination"])
async def test_reject_provider_errors_without_synthetic_fallback(error, tmp_path):
    payload = yandex_fixture()
    if error == "duration":
        payload["segments"][0]["duration"] = 60
    elif error == "naive":
        payload["segments"][0]["arrival"] = "2030-11-19T07:55:00"
    elif error == "failed":
        payload = {"error": {"text": "invalid key"}}
    else:
        payload["pagination"] = {}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as source:
        http = MockTrainHttpClient(source)
        with pytest.raises(ProviderResponseError):
            await TrainTool(mode="provider", http=http, key="TEST").run(train_query())


async def test_skip_unmapped_station_or_wrong_departure_date(tmp_path):
    payload = yandex_fixture()
    payload["segments"][0]["from"]["code"] = "unknown-station"
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as source:
        http = MockTrainHttpClient(source)
        assert await TrainTool(mode="provider", http=http, key="TEST").run(train_query()) == []


async def test_named_sapsan_cannot_be_sold_as_a_sleeper_class(tmp_path):
    payload = yandex_fixture()
    payload["segments"][0]["thread"]["vehicle"] = "Сапсан"
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as source:
        http = MockTrainHttpClient(source)
        tool = TrainTool(mode="provider", http=http, key="TEST")
        assert await tool.run(train_query(seat_class="coupe")) == []
        assert len(await tool.run(train_query(seat_class="seated"))) == 1


def test_train_fixture_is_not_a_real_provider_recording():
    data = json.loads((DATA_DIR / "trains" / "yandex_search.synthetic.json").read_text())
    assert data["fixture_metadata"]["kind"] == "synthetic"
    assert data["fixture_metadata"]["is_captured_provider_response"] is False


async def test_provider_train_offer_identity_changes_with_synthetic_fare_seed(tmp_path):
    payload = yandex_fixture()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as source:
        http = MockTrainHttpClient(source)
        first = await TrainTool(mode="provider", seed=42, http=http, key="TEST").run(train_query())
        second = await TrainTool(mode="provider", seed=43, http=http, key="TEST").run(train_query())
    assert first[0].price_rub != second[0].price_rub
    assert first[0].id != second[0].id


class MockTrainHttpClient:
    mode = "live"

    def __init__(self, source):
        self.source = source

    async def get(self, url, **kwargs):
        return await self.source.get(url, **kwargs)


async def test_missing_configuration_fails_before_network():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: pytest.fail("Unexpected HTTP"))) as source:
        with pytest.raises(ProviderNotConfigured):
            await TrainTool(mode="provider", http=MockTrainHttpClient(source)).run(train_query())
    with pytest.raises(ProviderNotConfigured):
        await TrainTool(mode="provider").run(train_query())
