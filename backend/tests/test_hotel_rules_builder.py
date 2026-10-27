"""D3 rules rebuild deterministically and reject invalid source identities."""

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("hotel_rules_builder", ROOT / "scripts/build_hotel_rules.py")
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


@pytest.fixture
def project(tmp_path):
    for relative in ("data/hotels/catalog.json", "data/hotels/synthetic_rules.json", "scripts/build_hotel_rules.py"):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    return tmp_path


def test_checked_rules_reproduce_all_sixty_policies_and_provenance():
    expected = BUILDER.build_output()
    saved = json.loads((ROOT / "data/hotels/synthetic_rules.json").read_text())
    assert expected == saved
    assert len(expected["hotels"]) == 60
    assert expected["generator"] == "synthetic-hotel-rules-v3"
    assert expected["is_synthetic"] and expected["not_real_hotel_policies"]
    assert all(row["version"] == "synthetic-hotel-v3" and row["is_synthetic"] for row in expected["hotels"])


@pytest.mark.parametrize("mutation", ["duplicate", "invalid_identity", "unsupported_destination"])
def test_invalid_catalogue_cannot_generate_ambiguous_or_unsupported_policies(project, mutation):
    path = project / "data/hotels/catalog.json"
    document = json.loads(path.read_text())
    if mutation == "duplicate":
        document["hotels"].append(document["hotels"][0])
    elif mutation == "invalid_identity":
        document["hotels"][0]["hotel_id"] = "osm:node/../1"
    else:
        document["hotels"][0]["destination"] = "NRT"
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="identity|destination"):
        BUILDER.build_output(project)


@pytest.mark.parametrize("mutation", ["policy", "metadata"])
def test_check_rejects_stale_policy_or_metadata_without_writing_any_file(project, mutation):
    script = project / "scripts/build_hotel_rules.py"
    valid = subprocess.run([sys.executable, str(script), "--check"], capture_output=True, text=True, check=False)
    assert valid.returncode == 0, valid.stderr
    path = project / "data/hotels/synthetic_rules.json"
    document = json.loads(path.read_text())
    if mutation == "policy":
        document["hotels"][0]["base_room_night_rub"] += 1
    else:
        document["generator"] = "unverified-generator"
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n")
    before = {path: path.read_bytes() for path in project.rglob("*") if path.is_file()}
    result = subprocess.run([sys.executable, str(script), "--check"], capture_output=True, text=True, check=False)
    assert result.returncode != 0
    assert "rules are stale" in result.stderr
    assert {path: path.read_bytes() for path in project.rglob("*") if path.is_file()} == before
