"""Shared mechanics for D2/D6; no provider credentials or network defaults."""

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.contracts.tools import Place, Travelers

DATA_DIR = Path(os.environ.get("NA_MALDIVY_DATA_DIR") or Path(__file__).resolve().parents[3] / "data")


class ProviderNotConfigured(RuntimeError):
    """A live/record provider was requested without its required key."""


class ProviderResponseError(ValueError):
    """The provider failed or returned data that cannot safely be normalised."""


def stable_number(*parts: Any) -> int:
    value = json.dumps(parts, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return int.from_bytes(hashlib.sha256(value.encode()).digest()[:8], "big")


def party_id(travelers: Travelers) -> str:
    return f"a{travelers.adults}-c{'.'.join(map(str, travelers.children_ages)) or 'none'}-p{travelers.pets}"


def arrival_after(departure: datetime, minutes: int, destination: Place) -> datetime:
    """Elapsed duration belongs to UTC, not to wall-clock arithmetic across DST."""
    return (departure.astimezone(UTC) + timedelta(minutes=minutes)).astimezone(ZoneInfo(destination.timezone))


def provider_datetime(value: str, place: Place) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProviderResponseError("Provider timestamp must include an explicit UTC offset")
    return parsed.astimezone(ZoneInfo(place.timezone))
