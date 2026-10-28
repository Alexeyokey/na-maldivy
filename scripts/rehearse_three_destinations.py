"""Run mixed-source AYT/DXB/MLE backend rehearsals without credentials or network."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.three_destination_rehearsal import main

if __name__ == "__main__":
    main()
