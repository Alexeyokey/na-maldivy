"""Capabilities of the configured data tools, independent of model claims."""

from collections.abc import Mapping
from typing import Any

from app.agent.places import FULL_TRIP_DESTINATIONS


def full_trip_destinations(tools: Mapping[str, Any]) -> frozenset[str]:
    """Only advertise airports covered by flights, hotels and transfers together.

    Original stub/injected tools have no capability metadata and retain their
    finite baseline. A broken configured source cannot authorize new coverage.
    """
    coverage = []
    for name in ("search_flights", "search_hotels", "get_transfer"):
        tool = tools.get(name)
        if tool is None:
            return frozenset()
        try:
            supported = getattr(tool, "supported_destinations", FULL_TRIP_DESTINATIONS)
            coverage.append(frozenset(supported))
        except (OSError, ValueError, TypeError, KeyError):
            return frozenset()
    return frozenset.intersection(*coverage)
