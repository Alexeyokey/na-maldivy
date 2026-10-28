"""Validate recorded D9 UI evidence; never execute or manufacture a rehearsal.

This checks consistency and required evidence, not whether a human actually
performed the recorded actions. Evidence must come from the real visible UI.
"""

import argparse
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCOPE = "integrated_product_through_demo_payment_and_voucher"
METHODS = {"computer_use_ui_automation", "human"}
STEPS = (
    "search",
    "D7",
    "D8",
    "select",
    "server_cart",
    "fictitious_travelers",
    "two_acknowledgments",
    "payment_declined",
    "payment_approved",
    "voucher_download",
    "calendar_download",
)


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.utcoffset() is not None else None
    except ValueError:
        return None


def _duration(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and 0 < value <= 300


def _evidence_path(value: Any, root: Path) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    relative = Path(value)
    if relative.is_absolute():
        return None
    resolved = (root / relative).resolve()
    if (
        not resolved.is_relative_to((root / "eval/reports").resolve())
        or not resolved.is_file()
    ):
        return None
    return resolved


def validate_report(path: Path, *, root: Path = ROOT) -> list[str]:
    """Read-only validation; failed/not_run/backend-only records cannot pass."""
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ["UI report is missing or invalid JSON"]
    if not isinstance(report, dict):
        return ["UI report must be a JSON object"]
    errors = []
    if report.get("scope") != SCOPE:
        errors.append(
            "scope must identify the integrated product through demo payment and voucher"
        )
    if (
        report.get("target_seconds") != 300
        or report.get("required_consecutive_passes") != 5
    ):
        errors.append(
            "protocol requires five consecutive passes with target_seconds=300"
        )
    runs = report.get("runs")
    if (
        not isinstance(runs, list)
        or len(runs) != 5
        or not all(isinstance(row, dict) for row in runs)
    ):
        return [*errors, "runs must contain exactly five objects"]
    if any(type(row.get("run")) is not int for row in runs) or [
        row.get("run") for row in runs
    ] != [1, 2, 3, 4, 5]:
        errors.append("run numbers must be 1,2,3,4,5 in execution order")
    previous_end, versions, evidence_paths = None, set(), set()
    for index, row in enumerate(runs, 1):
        prefix = f"run {index}: "
        if row.get("status") != "passed":
            errors.append(
                prefix + "status must be passed; not_run or failed does not count"
            )
        if not isinstance(row.get("method"), str) or row["method"] not in METHODS:
            errors.append(
                prefix
                + "method must transparently identify human or computer_use_ui_automation"
            )
        if not _duration(row.get("duration_seconds")):
            errors.append(prefix + "duration_seconds must be finite and within (0,300]")
        start, end = _time(row.get("started_at")), _time(row.get("finished_at"))
        if start is None or end is None or end <= start:
            errors.append(
                prefix
                + "started_at/finished_at must be increasing ISO times with explicit UTC offsets"
            )
        else:
            elapsed = (end - start).total_seconds()
            if not _duration(elapsed):
                errors.append(prefix + "actual timestamp interval exceeds 300 seconds")
            if (
                _duration(row.get("duration_seconds"))
                and abs(elapsed - row["duration_seconds"]) > 1
            ):
                errors.append(
                    prefix + "duration differs from timestamps by more than one second"
                )
            if previous_end is not None and start < previous_end:
                errors.append(
                    prefix + "UI runs overlap or are not recorded in execution order"
                )
            previous_end = end
        version = row.get("app_version")
        if not isinstance(version, str) or not version.strip():
            errors.append(prefix + "app_version must identify the actual tested build")
        else:
            versions.add(version)
        trace = _evidence_path(row.get("trace"), root)
        if trace is None or trace.suffix.lower() != ".json":
            errors.append(
                prefix
                + "trace must be an existing relative JSON file inside eval/reports"
            )
            continue
        if trace in evidence_paths:
            errors.append(prefix + "each performed UI run needs its own evidence file")
        evidence_paths.add(trace)
        try:
            evidence = json.loads(trace.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            errors.append(prefix + "trace JSON cannot be read")
            continue
        if not isinstance(evidence, dict):
            errors.append(prefix + "trace must be a JSON object")
            continue
        for field in (
            "run",
            "status",
            "method",
            "started_at",
            "finished_at",
            "duration_seconds",
            "app_version",
        ):
            if evidence.get(field) != row.get(field):
                errors.append(prefix + f"trace and protocol disagree on {field}")
        if (
            evidence.get("scope") != SCOPE
            or evidence.get("integrated_ui_status") != "performed"
        ):
            errors.append(
                prefix
                + "trace must describe a performed integrated UI run; backend reports do not qualify"
            )
        if (
            evidence.get("chat_mode") != "api"
            or evidence.get("local_demo_fallback_used") is not False
        ):
            errors.append(
                prefix + "actual API mode with no local demo fallback must be explicit"
            )
        observed = evidence.get("observed_steps", [])
        if not isinstance(observed, list) or any(
            step not in observed for step in STEPS
        ):
            errors.append(prefix + "observed_steps must contain every required UI step")
        elif [observed.index(step) for step in STEPS] != sorted(
            observed.index(step) for step in STEPS
        ):
            errors.append(
                prefix
                + "required UI steps must be observed in their actual logical order"
            )
        # Network logs are optional: CUA can observe visible UI, while separate
        # backend checks establish the proxy/API boundary. Neither replaces a UI run.
        backend_report = evidence.get("backend_report")
        if backend_report is not None and _evidence_path(backend_report, root) is None:
            errors.append(
                prefix
                + "optional backend_report must be an existing relative file inside eval/reports"
            )
        artifacts = evidence.get("artifacts", [])
        if not isinstance(artifacts, list) or any(
            _evidence_path(item, root) is None for item in artifacts
        ):
            errors.append(
                prefix
                + "artifact references must be existing relative files inside eval/reports"
            )
    if len(versions) > 1:
        errors.append(
            "all five consecutive passes must use the same app_version; restart after a code change"
        )
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=ROOT / "eval/demo-runs.json")
    args = parser.parse_args()
    errors = validate_report(args.report)
    result = {
        "status": "failed" if errors else "passed",
        "criterion": "five_consecutive_integrated_ui_runs_within_300s",
        "errors": errors,
        "notice": "Validation checks recorded evidence; it does not manufacture or perform UI runs.",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(1 if errors else 0)


if __name__ == "__main__":
    main()
