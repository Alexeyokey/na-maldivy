#!/usr/bin/env python3
"""Build/check checked-in D5 demo transport supplements without network access."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.tools.demo_transport import build_routes, build_transfers, canonical_json  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check reproducibility without writing files")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    args = parser.parse_args()
    outputs = {
        "routes.generated.json": build_routes(args.data_dir),
        "transfers.generated.json": build_transfers(args.data_dir),
    }
    stale = []
    for name, value in outputs.items():
        path, content = args.data_dir / "demo" / name, canonical_json(value).encode("utf-8")
        if args.check:
            if not path.is_file() or path.read_bytes() != content:
                stale.append(name)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
    if stale:
        print("Demo transport supplements need regeneration: " + ", ".join(stale), file=sys.stderr)
        return 1
    print("Demo transport supplements are reproducible." if args.check else "Demo transport supplements generated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
