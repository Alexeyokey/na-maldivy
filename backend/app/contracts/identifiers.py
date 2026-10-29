"""Stable hotel identities shared by D3, S4 and trip repair.

OSM identity is the feature type and positive feature ID, independent of an
offer's dates, occupants and tariff. Legacy colon-separated IDs are accepted
at boundaries and serialized in the canonical slash form.
"""

import re

_OSM = re.compile(r"osm:(node|way|relation)[/:]([1-9][0-9]*)\Z")
_FICTIONAL_HOTEL = re.compile(r"demo:hotel/[A-Z]{3}-(?:0[1-9]|[1-9][0-9])\Z")
_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]*\Z")


def canonical_hotel_id(value: str) -> str:
    """Return canonical OSM identity or a safe opaque demo/provider slug.

    No path components, URL escapes, whitespace or traversal are accepted for
    opaque IDs. This does not turn an ID into an authorized filesystem path.
    """
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("Hotel ID must be a nonempty identifier without whitespace")
    if value.casefold().startswith("osm:"):
        match = _OSM.fullmatch(value)
        if match is None:
            raise ValueError("OSM hotel ID must be osm:node/ID, osm:way/ID or osm:relation/ID with positive ID")
        return f"osm:{match[1]}/{match[2]}"
    if value.casefold().startswith("demo:hotel/"):
        if _FICTIONAL_HOTEL.fullmatch(value) is None:
            raise ValueError("Fictional hotel ID must be demo:hotel/IATA-NN with a positive two-digit ordinal")
        return value
    if _SLUG.fullmatch(value) is None or ".." in value:
        raise ValueError("Opaque hotel ID must be a safe slug without path traversal")
    return value
