"""Run O2 from the repository root; no .env, external LLM, credentials or network."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.u2_eval import main

if __name__ == "__main__":
    main()
