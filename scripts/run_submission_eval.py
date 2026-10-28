"""Run our proposed backend acceptance corpus from the repository root."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.submission_eval import main

if __name__ == "__main__":
    main()
