"""One data root for every tool, independent of the working directory."""

import os
from pathlib import Path

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def resolve_data_dir(value: str | Path | None = None) -> Path:
    return Path(value or os.environ.get("DATA_DIR") or DEFAULT_DATA_DIR).expanduser().resolve()
