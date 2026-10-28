import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_integrated_demo_protocol_requires_evidence_for_completed_runs():
    report = json.loads((ROOT / "eval/demo-runs.json").read_text(encoding="utf-8"))
    assert report["scope"] == "integrated_product_through_demo_payment_and_voucher"
    assert report["target_seconds"] == 300 and report["required_consecutive_passes"] == 5
    assert [row["run"] for row in report["runs"]] == [1, 2, 3, 4, 5]
    for row in report["runs"]:
        assert row["status"] in {"not_run", "passed", "failed"}
        if row["status"] == "not_run":
            assert row["duration_seconds"] is None and row["trace"] is None
            continue
        assert isinstance(row["duration_seconds"], (int, float)) and row["duration_seconds"] >= 0
        trace_path = (ROOT / row["trace"]).resolve()
        assert trace_path.is_relative_to((ROOT / "eval/reports").resolve()) and trace_path.is_file()
        evidence = json.loads(trace_path.read_text(encoding="utf-8"))
        assert evidence["scope"] == report["scope"] and evidence["status"] == row["status"]
        assert evidence["integrated_ui_status"] == "performed"
        if row["status"] == "passed":
            assert row["duration_seconds"] <= report["target_seconds"]


def test_missing_implementations_cannot_create_a_successful_data_report(tmp_path):
    import shutil

    sandbox = tmp_path / "standalone"
    (sandbox / "scripts").mkdir(parents=True)
    (sandbox / "backend/app/tools").mkdir(parents=True)
    shutil.copy(ROOT / "scripts/rehearse_demo.py", sandbox / "scripts/rehearse_demo.py")
    for path in (sandbox / "backend/app/__init__.py", sandbox / "backend/app/tools/__init__.py"):
        path.write_text("", encoding="utf-8")
    output = tmp_path / "report.json"
    result = subprocess.run(
        [sys.executable, str(sandbox / "scripts/rehearse_demo.py"), "--output", str(output)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "requires D2/D3" in result.stderr
    assert not output.exists()
