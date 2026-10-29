"""Coverage is an offline data/planner check, not another checkout acceptance run."""

import asyncio
import hashlib
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/check_demo_coverage.py"
DATA = ROOT / "data"
spec = importlib.util.spec_from_file_location("demo_coverage_checker", SCRIPT)
checker = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = checker
spec.loader.exec_module(checker)


def snapshot(directory):
    return {
        path.relative_to(directory).as_posix(): (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
        for path in directory.rglob("*")
        if path.is_file()
    }


def test_cli_runs_real_data_and_146_planner_cases_without_env_network_or_data_writes(tmp_path):
    before = snapshot(DATA)
    report_path = tmp_path / "coverage.json"
    (tmp_path / ".env").write_text("LLM_PROVIDER=openai\nTOOLS_MODE=stub\nLLM_TIMEOUT_S=invalid\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--data-dir", str(DATA), "--output", str(report_path)],
        cwd=tmp_path,
        env={
            "PATH": os.defpath,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
            "LLM_PROVIDER": "openai",
            "TOOLS_MODE": "stub",
            "FLIGHTS_MODE": "aviasales",
            "CACHE_MODE": "live",
            "DATA_DIR": str(tmp_path / "absent-data"),
            "LLM_API_KEY": "coverage-test-private-value",
            "AVIASALES_API_TOKEN": "coverage-test-private-value",
        },
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(report_path.read_bytes())
    assert report["status"] == "passed" and report["scope"] == "data_and_planner_only"
    assert report["case_counts"] == {"passed": 146, "total": 146}
    assert report["inventory"]["counts"] == {"hotels": 60, "reviews": 1440, "profiles": 60, "cache_entries": 47}
    assert report["inventory"]["status"] == "passed"
    assert report["network_used"] is report["credentials_used"] is False
    assert report["network_attempts_blocked"] == report["model_calls"] == 0
    assert not any(report["acceptance"].values())
    assert report["seconds"] < 20
    cases = {row["case"]: row for row in report["cases"]}
    for name in ("monthly/DXB/limit395", "monthly/AYT/limit305"):
        assert cases[name]["status"] == "passed" and cases[name]["option_count"] > 0
    for name in ("direct/LED/MLE", "late/MOW/MLE", "budget/AYT/1000", "budget/MLE/10000"):
        assert cases[name]["expected"] == "empty" and cases[name]["option_count"] == 0
    assert snapshot(DATA) == before, "Read-only coverage cannot rewrite data or touch its mtimes"
    assert "coverage-test-private-value" not in report_path.read_text() + result.stdout + result.stderr


def corrupt_data(directory, mutation):
    if mutation == "missing_profile":
        next((directory / "hotel_profiles").glob("*.json")).unlink()
        return "hotel_profiles_ids"
    if mutation == "mismatched_rules_id":
        path = directory / "hotels/synthetic_rules.json"
        value = json.loads(path.read_bytes())
        value["hotels"][0]["hotel_id"] = "osm:node/99999999999999"
        path.write_text(json.dumps(value), encoding="utf-8")
        return "rules_ids"
    if mutation in {"contradictory_children", "contradictory_pets"}:
        rules = json.loads((directory / "hotels/synthetic_rules.json").read_bytes())["hotels"]
        rule = (
            next(row for row in rules if row["adults_only"])
            if mutation == "contradictory_children"
            else next(row for row in rules if not row["pets_allowed"])
        )
        name = rule["hotel_id"].replace(":", "-").replace("/", "-") + ".json"
        path = directory / "hotel_profiles" / name
        value = json.loads(path.read_bytes())
        aspect, segment = ("children", "family") if mutation == "contradictory_children" else ("pets", "pets")
        value["aspects"][aspect]["positive_count"] = 1
        value["guest_profiles"][segment]["aspects"][aspect]["positive_count"] = 1
        path.write_text(json.dumps(value), encoding="utf-8")
        return "profile_children_policy" if mutation == "contradictory_children" else "profile_pets_policy"
    path = next(path for path in (directory / "demo/aviasales").glob("*.json") if path.name != "manifest.json")
    value = json.loads(path.read_bytes())
    value["response"]["body_base64"] = "e30="
    path.write_text(json.dumps(value), encoding="utf-8")
    return "cache_file_hash"


@pytest.mark.parametrize(
    "mutation", ["missing_profile", "mismatched_rules_id", "contradictory_children", "contradictory_pets", "bad_cache"]
)
async def test_corrupt_data_fails_closed_before_planner_without_rewriting_it(tmp_path, monkeypatch, mutation):
    data = tmp_path / "data"
    shutil.copytree(DATA, data)
    expected_failure = corrupt_data(data, mutation)
    before = snapshot(data)

    def forbidden_registry(*args, **kwargs):
        pytest.fail("Invalid input data must fail before building the planner registry")

    monkeypatch.setattr(checker, "create_tools", forbidden_registry)
    report = await checker.check_coverage(data)
    assert report["status"] == "failed"
    assert expected_failure in report["failures"]
    assert report["cases"] == [] and report["case_counts"]["total"] == 0
    assert snapshot(data) == before


async def test_offline_guard_blocks_sync_async_http_dns_and_tcp_before_any_connection():
    with checker.forbid_external_network() as attempts:
        with pytest.raises(checker.NetworkBlocked):
            await asyncio.to_thread(httpx.get, "https://example.invalid")
        async with httpx.AsyncClient() as client:
            with pytest.raises(checker.NetworkBlocked):
                await client.get("https://example.invalid")
        with pytest.raises(checker.NetworkBlocked):
            socket.getaddrinfo("example.invalid", 443)
        with socket.socket() as client:
            with pytest.raises(checker.NetworkBlocked):
                client.connect(("127.0.0.1", 9))
    assert len(attempts) == 4


@pytest.mark.parametrize("field", ["alias_values", "destination"])
async def test_unhashable_data_shapes_fail_with_a_sanitized_report(tmp_path, field):
    data = tmp_path / "data"
    shutil.copytree(DATA, data)
    if field == "alias_values":
        path = data / "hotels/id_aliases.json"
        value = {"aliases": {"old": ["coverage-test-private-value"]}}
    else:
        path = data / "hotels/catalog.json"
        value = json.loads(path.read_bytes())
        value["hotels"][0]["destination"] = ["coverage-test-private-value"]
    path.write_text(json.dumps(value), encoding="utf-8")
    report = await checker.check_coverage(data)
    assert report["status"] == "failed" and "malformed_data_shape" in report["failures"]
    assert report["cases"] == []
    assert "coverage-test-private-value" not in json.dumps(report)


async def test_empty_planner_matrix_cannot_be_declared_passed(monkeypatch):
    monkeypatch.setattr(checker, "coverage_cases", lambda: [])
    report = await checker.check_coverage(DATA)
    assert report["status"] == "failed" and "no_planner_cases" in report["failures"]
    assert report["case_counts"]["total"] == 0


def test_report_path_cannot_overwrite_the_read_only_dataset():
    before = snapshot(DATA)
    with pytest.raises(SystemExit) as error:
        checker.main(["--data-dir", str(DATA), "--output", str(DATA / "coverage.json")])
    assert error.value.code == 2
    assert snapshot(DATA) == before
