#!/usr/bin/env python3
"""Run five repeatable DATA rehearsals; never labels them end-to-end passes."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def source_commit() -> str | None:
    # Source archives have no Git metadata. Do not inherit an enclosing repo's
    # commit or turn an otherwise successful offline rehearsal into a failure.
    if not (ROOT / ".git").exists():
        return None
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    required = (
        "currency",
        "destinations",
        "flights",
        "hotels",
        "hotel_profiles",
        "http_cache",
        "price_calendar",
        "trains",
    )
    if any(importlib.util.find_spec("app.tools." + name) is None for name in required):
        print(
            "Full data rehearsal requires D2/D3/D4/D5/D6/D7/D8 and the six-tool registry; no run recorded.",
            file=sys.stderr,
        )
        return 2
    import socket
    from unittest.mock import patch

    import httpx
    from app.demo_rehearsal import demo_payload

    def forbidden(*args, **kwargs):
        raise AssertionError("Offline data rehearsal attempted a network connection")

    runs = []
    with (
        patch.object(httpx.AsyncClient, "send", forbidden),
        patch.object(socket, "create_connection", forbidden),
        patch.object(socket.socket, "connect", forbidden),
    ):
        for number in range(1, 6):
            started = time.perf_counter()
            payload = asyncio.run(demo_payload())
            duration = time.perf_counter() - started
            if (
                not payload["selected_quote"]
                or not payload["date_savings"]
                or len(payload["calendar"]["days"]) != 30
            ):
                raise AssertionError(
                    "Missing complete comparison or saving/calendar data"
                )
            if len(payload["trains"]) < 1 or payload["currency"]["usd_100_rub"] != 8054:
                raise AssertionError("Train/currency rehearsal changed")
            digest = hashlib.sha256(
                json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest()
            runs.append(
                {
                    "run": number,
                    "status": "passed",
                    "scope": "data_only_no_chat_or_booking",
                    "payload_sha256": digest,
                    "duration_seconds": round(duration, 4),
                    "hotel_count": payload["matching_hotels_count"],
                    "date_saving_count": len(payload["date_savings"]),
                    "registry_tools": payload["registry_tools"],
                }
            )
    if len({run["payload_sha256"] for run in runs}) != 1:
        raise AssertionError("Data rehearsals are not reproducible")
    commit = source_commit()
    source_files = sorted((ROOT / "backend/app").rglob("*.py"))
    source_hash = hashlib.sha256(
        b"".join(
            str(f.relative_to(ROOT)).encode() + f.read_bytes() for f in source_files
        )
    ).hexdigest()
    report = {
        "code_commit": commit,
        "backend_source_sha256": source_hash,
        "schema_version": 1,
        "scope": "data_only",
        "end_to_end_payment_status": "not_run",
        "network": "forbidden",
        "runs": runs,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {"data_runs": 5, "identical": True, "end_to_end_payment_status": "not_run"}
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
