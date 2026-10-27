"""The recorded CBR sample is real, dated and usable offline, not a fake quote."""

import hashlib
import json
from datetime import date
from pathlib import Path

from app.tools.currency import CurrencyRates, load_cbr_rates
from app.tools.http_cache import CachedHttpClient

ROOT = Path(__file__).resolve().parents[2]


def test_cbr_raw_snapshot_matches_provenance() -> None:
    provenance = json.loads((ROOT / "data/sources/cbr-provenance.json").read_text())
    body = (ROOT / provenance["raw_file"]).read_bytes()
    rates = CurrencyRates.from_xml(body)

    assert hashlib.sha256(body).hexdigest() == provenance["body_sha256"]
    assert rates.effective_date.isoformat() == provenance["effective_date"]
    assert len(rates.rates) - 1 == provenance["currency_count_excluding_RUB"] == 54
    assert provenance["source_url"] == rates.source_url
    assert rates.rates["JPY"].nominal > 1


async def test_recorded_cbr_replays_offline_and_preserves_actual_effective_date() -> None:
    # Default replay does not even construct an HTTP transport.
    async with CachedHttpClient(cache_dir=ROOT / "data/cache/cbr") as client:
        rates = await load_cbr_rates(client)

    assert rates.effective_date == date(2026, 10, 24)
    assert rates.requested_date is None
    assert rates.convert("1", "RUB").amount_rub == 1
    assert rates.convert("1.00", "USD").amount_rub > 0
    assert rates.age_days(date(2026, 10, 26)) == 2
    assert not rates.is_stale(date(2026, 10, 26), max_age_days=3)
    assert rates.is_stale(date(2026, 10, 28), max_age_days=3)
