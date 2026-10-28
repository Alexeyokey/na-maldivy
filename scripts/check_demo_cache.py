"""Verify the portable public Aviasales snapshot without .env, keys, or HTTP."""

import asyncio
import base64
import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.tools.http_cache import CachedHttpClient

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data/demo/aviasales"


async def check_cache(cache_dir: Path = CACHE) -> dict:
    manifest = json.loads((cache_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["source"] == "aviasales_data_api"
    assert manifest["credential_required_for_replay"] is False
    assert manifest["is_live_availability"] is False
    entries = manifest["entries"]
    assert entries, "Portable snapshot is empty"
    async with CachedHttpClient(mode="replay", cache_dir=cache_dir) as client:
        for record in entries:
            name = record["file"]
            assert (
                len(name) == 69
                and name.endswith(".json")
                and all(c in "0123456789abcdef" for c in name[:-5])
            )
            raw = (cache_dir / name).read_bytes()
            assert hashlib.sha256(raw).hexdigest() == record["sha256"]
            entry = json.loads(raw)
            request = entry["request"]
            url = urlsplit(request["url"])
            assert url.scheme == "https" and url.hostname == "api.travelpayouts.com"
            assert url.path == "/aviasales/v3/prices_for_dates"
            assert url.username is None and url.password is None
            assert not any(
                "token" in key.casefold() or "key" in key.casefold()
                for key, _ in parse_qsl(url.query)
            )
            assert not any(
                key.casefold()
                in {"authorization", "x-access-token", "cookie", "set-cookie"}
                for key, _ in request["headers"] + entry["response"]["headers"]
            )
            body = base64.b64decode(entry["response"]["body_base64"], validate=True)
            assert hashlib.sha256(body).hexdigest() == entry["response"]["body_sha256"]
            response = await client.get(
                request["url"], headers=dict(request["headers"])
            )
            assert response.extensions["cache_hit"] is True
            assert response.status_code == 200 and response.json()["success"] is True
    return {
        "entries_checked": len(entries),
        "mode": "replay",
        "credentials_used": False,
        "network_used": False,
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(check_cache()), ensure_ascii=False))
