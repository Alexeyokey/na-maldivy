import hashlib
import json
from datetime import date

import httpx
import pytest

from app.u2_cli import demo_payload


async def test_five_data_runs_are_identical_with_network_disabled(monkeypatch):
    """D9 data preflight only: this is not five chat-to-payment E2E runs."""

    async def no_network(*args, **kwargs):
        pytest.fail("The autonomous U2 data demo attempted a network connection")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", no_network)
    hashes = []
    for _ in range(5):
        payload = await demo_payload()
        assert payload["scope"] == "data_only_no_chat_or_booking"
        assert payload["matching_hotels_count"] >= 3
        assert payload["selected_quote"] is not None
        assert payload["currency"]["is_historical_snapshot"]
        assert payload["flights"] and all(item["is_synthetic"] for item in payload["flights"])
        for hotel in payload["hotels"]:
            assert hotel["profile"]["is_synthetic"]
            assert not hotel["offer"]["adults_only"]
            assert hotel["offer"]["refundable"]
            assert hotel["total_nights"] == 7
            assert hotel["facts"]["source_url"].startswith("https://www.openstreetmap.org/")
        hashes.append(hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest())
    assert len(set(hashes)) == 1


async def test_demo_rejects_calendar_overflow_before_search():
    with pytest.raises(ValueError, match="семи календарных дней"):
        await demo_payload(departure=date.max)
