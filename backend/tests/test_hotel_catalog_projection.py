"""Keep the shared hotel selection reproducible and reject catalogue drift."""

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("hotel_projection", ROOT / "scripts/fetch_hotels.py")
PROJECTOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROJECTOR)


@pytest.fixture
def project(tmp_path):
    for relative in ("data/hotels/osm_hotels.json", "data/transfers/transfers.json", "scripts/fetch_hotels.py"):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    return tmp_path


def test_saved_projection_matches_shared_snapshot_and_leaves_unknown_metadata_unknown():
    for path, expected in PROJECTOR.build_outputs().items():
        assert json.loads(path.read_text()) == expected
    rows = PROJECTOR.build_outputs()[ROOT / "data/hotels/catalog.json"]["hotels"]
    assert all(row["osm_version"] is row["osm_timestamp"] is row["raw_stars_tag"] is None for row in rows)
    # The original tag and a raw response date cannot be reconstructed from an
    # integer star rating in a normalized snapshot.
    assert any(row["stars"] is not None for row in rows)


def test_duplicate_physical_identity_is_rejected_before_projection(project):
    path = project / "data/hotels/osm_hotels.json"
    data = json.loads(path.read_text())
    data["hotels"].append(data["hotels"][0])
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="duplicate hotel identity"):
        PROJECTOR.build_outputs(project)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "wrong_airport"])
def test_transfer_coverage_must_match_physical_hotels_exactly(project, mutation):
    path = project / "data/transfers/transfers.json"
    data = json.loads(path.read_text())
    if mutation == "missing":
        data["transfers"].pop()
    elif mutation == "duplicate":
        data["transfers"].append(data["transfers"][0])
    else:
        data["transfers"][0]["airport"] = "MLE"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="transfer records disagree"):
        PROJECTOR.build_outputs(project)


def test_check_reports_stale_projection_without_rewriting_any_artifact(project):
    for path, data in PROJECTOR.build_outputs(project).items():
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    script = project / "scripts/fetch_hotels.py"
    valid = subprocess.run([sys.executable, str(script), "--check"], capture_output=True, text=True, check=False)
    assert valid.returncode == 0, valid.stderr
    stale = project / "data/hotels/catalog.json"
    stale.write_text(stale.read_text() + " ")
    before = {path: path.read_bytes() for path in project.rglob("*") if path.is_file()}
    result = subprocess.run([sys.executable, str(script), "--check"], capture_output=True, text=True, check=False)
    assert result.returncode != 0
    assert "projection is stale" in result.stderr
    assert {path: path.read_bytes() for path in project.rglob("*") if path.is_file()} == before
