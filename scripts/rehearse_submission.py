"""Run five backend rehearsals from the repository root without secrets or network."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.submission_rehearsal import main

if __name__ == "__main__":
    main()
