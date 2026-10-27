import json
import shutil

import httpx
import pytest
from test_trains import MockTrainHttpClient, train_query, yandex_fixture

from app.data_paths import DEFAULT_DATA_DIR
from app.tools.registry import run_tool
from app.tools.trains import TrainTool, train_price
from app.tools.transport import ProviderResponseError


@pytest.mark.parametrize("seat", ["seated", "platzkart", "coupe", "sv"])
async def test_d1_dispatch_returns_valid_group_quotes_for_every_class(seat):
    tool = TrainTool()
    result = await run_tool(tool.name, train_query(seat_class=seat).model_dump(), tools={tool.name: tool})
    assert result and all(item.seat_class == seat and item.is_synthetic for item in result)
    assert all(item.origin.kind == "city" and item.destination.kind == "city" for item in result)


def test_train_type_changes_only_disclosed_synthetic_tariff():
    regular = train_price(train_query())
    assert train_price(train_query(), train_type="fast") > regular
    assert train_price(train_query(), train_type="branded") > regular
    with pytest.raises(ValueError):
        train_price(train_query(), train_type="unknown")


@pytest.mark.parametrize("status", [401, 429, 503])
async def test_http_errors_are_safe_common_errors(status):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(status, text="private-secret"))
    ) as source:
        with pytest.raises(ProviderResponseError) as error:
            await TrainTool(mode="provider", http=MockTrainHttpClient(source), key="private-key").run(train_query())
    assert "private" not in str(error.value)


@pytest.mark.parametrize("change", ["thread", "station", "duration", "nan", "pagination", "segment"])
async def test_malformed_provider_values_do_not_escape_as_type_errors(change):
    payload = yandex_fixture()
    if change == "thread":
        payload["segments"][0]["thread"] = None
    if change == "station":
        payload["segments"][0]["from"] = None
    if change == "duration":
        payload["segments"][0]["duration"] = "bad"
    if change == "nan":
        payload["segments"][0]["duration"] = "NaN"
    if change == "pagination":
        payload["pagination"] = []
    if change == "segment":
        payload["segments"] = [None]
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as source:
        with pytest.raises(ProviderResponseError):
            await TrainTool(mode="provider", http=MockTrainHttpClient(source), key="TEST").run(train_query())


async def test_explicit_data_root_changes_model_without_using_global_cache(tmp_path):
    shutil.copytree(DEFAULT_DATA_DIR / "trains", tmp_path / "trains")
    path = tmp_path / "trains/model.json"
    model = json.loads(path.read_text(encoding="utf-8"))
    model["pet_fee_rub"] = 7654
    path.write_text(json.dumps(model), encoding="utf-8")
    from app.contracts.tools import Travelers

    request = train_query(travelers=Travelers(adults=1, pets=1))
    usual = await TrainTool().run(request)
    changed = await TrainTool(data_dir=tmp_path).run(request)
    assert changed[0].price_rub - usual[0].price_rub == 7154
