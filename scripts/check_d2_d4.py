#!/usr/bin/env python3
"""Check D2 with D4, optionally loading its HTTP client from a local Git ref.

No fetch, install or checkout changes are performed. The selected D4 source is
loaded in this test process from a temporary directory and removed on exit.
Provider payloads in the tests are explicitly synthetic; no partner API runs.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_checks() -> int:
    import pytest

    return int(pytest.main([str(ROOT / "backend/tests/test_flights_d4.py"), "-q"]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--d4-ref", help="Use the HTTP client from this already fetched local Git ref"
    )
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT / "backend"))
    if args.d4_ref is None:
        try:
            importlib.import_module("app.tools.http_cache")
        except ModuleNotFoundError as error:
            if error.name != "app.tools.http_cache":
                raise
            print(
                "D4 is absent: merge #56 or pass --d4-ref for an already fetched local ref.",
                file=sys.stderr,
            )
            return 2
        return run_checks()
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--verify", args.d4_ref + "^{commit}"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        source = subprocess.run(
            ["git", "show", commit + ":backend/app/tools/http_cache.py"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        ).stdout
    except subprocess.CalledProcessError:
        print(
            "Cannot read D4 from the selected local Git ref; no network request was attempted.",
            file=sys.stderr,
        )
        return 2
    with tempfile.TemporaryDirectory(prefix="na-maldivy-d2-d4-") as temporary:
        path = Path(temporary) / "http_cache.py"
        path.write_bytes(source)
        spec = importlib.util.spec_from_file_location("app.tools.http_cache", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        print(f"Checking D2 with the D4 HTTP client from commit {commit}", flush=True)
        return run_checks()


if __name__ == "__main__":
    raise SystemExit(main())
