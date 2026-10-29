"""Independent expanded-data checks fail before runtime on corrupt provenance/IDs."""

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
SCRIPT = ROOT / "scripts/check_expanded_demo.py"
spec = importlib.util.spec_from_file_location("expanded_demo_checker", SCRIPT)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)
PRIVATE = ("cache/aviasales/", "cache/yandex/", "hotel_profiles_llm/")


def public_paths(directory):
    return [
        path
        for path in directory.rglob("*")
        if path.is_file() and not any(path.relative_to(directory).as_posix().startswith(prefix) for prefix in PRIVATE)
    ]


def snapshot(directory):
    return {
        path.relative_to(directory).as_posix(): (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
        for path in public_paths(directory)
    }


def copy_data(target):
    for source in public_paths(DATA):
        destination = target / source.relative_to(DATA)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    return target


def test_complete_public_inventory_has_no_data_writes_or_network():
    before = snapshot(DATA)
    with checker.forbid_external_network() as attempts:
        result = checker.check_inventory(DATA)
    assert result["status"] == "passed", result["failures"]
    assert result["counts"] == {
        "destinations": 48,
        "hotels": 420,
        "fictional_hotels": 360,
        "reviews": 10080,
        "profiles": 420,
    }
    assert attempts == [] and snapshot(DATA) == before


@pytest.mark.parametrize(
    "mutation", ["route_source", "airport_timezone", "transfer_destination", "profile_identity", "fictional_osm"]
)
async def test_corrupted_supplement_fails_before_registry_and_never_echoes_payload(tmp_path, monkeypatch, mutation):
    directory = copy_data(tmp_path / "data")
    if mutation == "route_source":
        path = directory / "demo/routes.generated.json"
        value = json.loads(path.read_bytes())
        value["routes"][0]["sources"] = ["private-test-sentinel"]
    elif mutation == "airport_timezone":
        path = directory / "demo/routes.generated.json"
        value = json.loads(path.read_bytes())
        value["airports"]["CMB"]["timezone"] = "Europe/Moscow"
    elif mutation == "transfer_destination":
        path = directory / "demo/transfers.generated.json"
        value = json.loads(path.read_bytes())
        value["transfers"][0]["airport"] = "NRT" if value["transfers"][0]["airport"] != "NRT" else "CMB"
    elif mutation == "profile_identity":
        path = next((directory / "demo/hotel_profiles").glob("*.json"))
        value = json.loads(path.read_bytes())
        value["hotel_id"] = "demo:hotel/ZZZ-01"
    else:
        path = directory / "demo/hotels.generated.json"
        value = json.loads(path.read_bytes())
        value["hotels"][0]["osm_id"] = 123456
    path.write_bytes(json.dumps(value).encode())
    before = snapshot(directory)

    def forbidden(*args, **kwargs):
        pytest.fail("Corrupted expanded data must fail before constructing tools")

    monkeypatch.setattr(checker, "create_tools", forbidden)
    report = await checker.check_coverage(directory)
    assert report["status"] == "failed" and report["failures"]
    assert report["case_counts"]["total"] == 0
    assert "private-test-sentinel" not in json.dumps(report)
    assert report["network_attempts_blocked"] == 0 and snapshot(directory) == before


def test_matrix_has_all_months_and_expected_self_route_emptiness_without_replacing_baseline_constraints():
    destinations = {row.iata for row in checker.load_catalogue(DATA / "destinations.yaml").destinations}
    cases = checker.coverage_cases(destinations)
    calendar = [case for case in cases if case["matrix"] == "A"]
    portfolio = [case for case in cases if case["matrix"] == "B"]
    assert len(calendar) == 1152 and len(portfolio) == 630
    assert len({case["case"] for case in cases}) == len(cases)
    assert sum(case["expected"] == "empty" for case in calendar) == 12
    for case in calendar:
        assert case["brief"]["start_date"].year == 2027
        assert case["brief"]["start_date"].day == 15
        assert (case["expected"] == "empty") == (case["brief"]["origin"] == case["brief"]["destination"])
    assert all(case["brief"]["destination"] not in {"AYT", "DXB", "MLE"} for case in portfolio)


async def test_real_representative_planner_ignores_host_configuration_and_preserves_data(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("TOOLS_MODE", "stub")
    monkeypatch.setenv("DATA_DIR", "/does-not-exist-expanded-check")
    monkeypatch.setenv("LLM_TIMEOUT_S", "private-test-sentinel")
    monkeypatch.setenv("AVIASALES_API_TOKEN", "private-test-sentinel")
    before = snapshot(DATA)
    original_cases = checker.coverage_cases

    def representative(destinations):
        # Real tools, all major groups, fractional timezone and the self-route.
        return [
            case
            for case in original_cases(destinations)
            if case["case"]
            in {
                "calendar/MOW/CMB/2027-06",
                "calendar/LED/LED/2027-06",
                "mixed_family/MOW/CMB",
                "twins/LED/PKC",
                "four_adults/MOW/BEG",
                "late/MOW/CMB",
                "budget_impossible/MOW/CMB",
                "duration_impossible/MOW/CMB",
            }
        ]

    monkeypatch.setattr(checker, "coverage_cases", representative)
    report = await checker.check_coverage(DATA)
    # The full-matrix sentinel remains visible when intentionally testing a subset.
    assert report["failures"] == ["case_matrix_size"]
    assert report["case_counts"] == {"passed": 8, "failed": 0, "total": 8}, report["failed_cases"]
    assert report["failed_cases"] == []
    assert report["credentials_used"] is report["network_used"] is False
    assert report["model_calls"] == report["network_attempts_blocked"] == 0
    assert not any(report["acceptance"].values())
    assert "private-test-sentinel" not in json.dumps(report)
    assert snapshot(DATA) == before


def test_cli_corrupt_data_emits_safe_report_and_refuses_output_under_data(tmp_path):
    directory = tmp_path / "data"
    directory.mkdir()
    report_path = tmp_path / "report.json"
    command = [sys.executable, "-B", str(SCRIPT), "--data-dir", str(directory), "--output", str(report_path)]
    result = subprocess.run(command, cwd=tmp_path, env={"PATH": os.defpath}, capture_output=True, text=True, timeout=10)
    assert result.returncode == 1
    report = json.loads(report_path.read_bytes())
    assert report["status"] == "failed" and report["case_counts"]["total"] == 0
    assert report["model_calls"] == report["network_attempts_blocked"] == 0
    result = subprocess.run(command[:-1] + [str(directory / "unsafe.json")], capture_output=True, text=True, timeout=10)
    assert result.returncode == 2 and not (directory / "unsafe.json").exists()
