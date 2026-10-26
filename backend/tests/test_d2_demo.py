import json
import socket
from datetime import date

import httpx
import pytest

from app.contracts.tools import Flight
from app.d2_cli import demo_payload, main


async def test_five_identical_demo_runs_need_no_http_client_or_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("The synthetic D2 demo must not construct an HTTP client or access the network")

    monkeypatch.setattr(httpx, "Client", forbidden)
    monkeypatch.setattr(httpx, "AsyncClient", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    payloads = [await demo_payload() for _ in range(5)]
    assert all(payload == payloads[0] for payload in payloads)
    payload = payloads[0]
    assert payload["scope"] == "d2_flights_only"
    assert payload["mode"] == "synthetic"
    assert payload["one_way"] is True
    assert payload["query"]["travelers"] == {"adults": 2, "children_ages": [5], "pets": 0}
    assert len(payload["flights"]) == len(payload["provenance"]) == 2
    for flight, source in zip(payload["flights"], payload["provenance"], strict=True):
        assert Flight.model_validate(flight).is_synthetic
        assert source["offer_id"] == flight["id"]
        assert source["schedule"] == source["prices"] == "synthetic"
        assert source["route_sources"]
        assert source["seed"] == 42
        assert flight["baggage_price_rub"] == 6000


@pytest.mark.parametrize("origin", ["SVO", "LED"])
@pytest.mark.parametrize("destination", ["AYT", "DXB", "MLE"])
async def test_demo_covers_three_destinations_from_both_origins(origin, destination):
    payload = await demo_payload(origin=origin, destination=destination, departure_date=date(2026, 11, 18))
    assert len(payload["flights"]) == 2
    assert all(flight["origin"]["iata"] == origin for flight in payload["flights"])
    assert all(flight["destination"]["iata"] == destination for flight in payload["flights"])
    assert any(flight["is_overnight"] for flight in payload["flights"])
    if (origin, destination) == ("LED", "MLE"):
        assert all(flight["stops"] == 1 for flight in payload["flights"])


async def test_demo_seed_changes_offers_but_not_query():
    first = await demo_payload(seed=42)
    changed = await demo_payload(seed=43)
    assert first["query"] == changed["query"]
    assert [flight["price_rub"] for flight in first["flights"]] != [
        flight["price_rub"] for flight in changed["flights"]
    ]


def test_cli_json_keeps_contract_and_explicit_price_basis(capsys):
    assert main(["--json", "--origin", "LED", "--destination", "MLE", "--seed", "7"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["query"]["origin"] == "LED"
    assert payload["query"]["destination"] == "MLE"
    assert payload["query"]["departure_date"] == "2026-11-18"
    assert payload["seed"] == 7
    assert "entire group" in payload["price_basis"]
    assert "separate" in payload["price_basis"]


def test_cli_text_explains_synthetic_group_prices_and_local_dates(capsys):
    assert main([]) == 0
    output = capsys.readouterr().out
    assert "синтетические расписание и цены" in output
    assert "2 взрослых и ребёнок 5 лет" in output
    assert "в одну сторону за всю группу" in output
    assert "багаж оплачивается отдельно" in output
    assert "2026-11-18" in output
    assert "Europe/Moscow" in output and "Asia/Dubai" in output
    assert "следующую местную дату: 1" in output


@pytest.mark.parametrize(
    "arguments",
    [
        ["--departure-date", "2026-02-30"],
        ["--departure-date", "yesterday"],
        ["--seed", "not-an-integer"],
        ["--origin", "INVALID"],
    ],
)
def test_cli_invalid_inputs_fail_safely(arguments, capsys):
    assert main(arguments) == 2
    output = capsys.readouterr()
    assert not output.out
    assert output.err
    assert "Traceback" not in output.err


def test_cli_unsupported_route_is_explicit_empty_search(capsys):
    assert main(["--json", "--destination", "JFK"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["flights"] == payload["provenance"] == []
