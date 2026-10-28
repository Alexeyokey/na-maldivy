"""Export compact, reproducible public evidence without private runtime capabilities."""

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

PRIVATE_FIELDS = {
    "session_id": "SESSION",
    "cart_id": "CART",
    "order_id": "ORDER",
    "idempotency_key": "RETRY",
    "full_name": "DEMO_PERSON",
    "birth_date": "DEMO_BIRTH_DATE",
    "x-session-id": "SESSION",
}
OMITTED = [
    "turns",
    "trace",
    "api",
    "observation",
    "original_context",
    "original_followups",
    "original_actions",
]


def canonical_digest(value: Any) -> str:
    data = json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def counts(cases: list[dict[str, Any]]) -> dict[str, int]:
    return {
        status: sum(case["status"] == status for case in cases)
        for status in ("passed", "failed", "blocked")
    }


def export_report(source: Path, destination: Path) -> dict[str, Any]:
    if source.resolve() == destination.resolve():
        raise ValueError(
            "Keep the private raw report and public export at different paths"
        )
    raw_bytes = source.read_bytes()
    report = json.loads(raw_bytes)
    cases = report["cases"]
    proposed = [
        case for case in cases if case["corpus_role"] == "our_proposed_addition"
    ]
    original = [case for case in cases if case["corpus_role"] == "original_u2"]
    if len(cases) != 50 or len(proposed) != 40 or len(original) != 10:
        raise ValueError("Expected 40 proposed cases plus the 10 original U2 cases")
    for key, rows in (
        ("summary", cases),
        ("proposed_summary", proposed),
        ("original_u2_summary", original),
    ):
        if report[key] != counts(rows):
            raise ValueError(
                "Raw report summary differs from its observed case statuses"
            )
    if len({case["id"] for case in cases}) != 50:
        raise ValueError("Duplicate case IDs")

    private: dict[str, set[str]] = {}

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key.lower() in PRIVATE_FIELDS and isinstance(item, str) and item:
                    private.setdefault(PRIVATE_FIELDS[key.lower()], set()).add(item)
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(cases)
    aliases = {
        value: f"[{kind}_{index:03d}]"
        for kind, values in sorted(private.items())
        for index, value in enumerate(sorted(values), 1)
    }

    def redact(value: Any) -> Any:
        if isinstance(value, str):
            for private_value in sorted(aliases, key=len, reverse=True):
                value = value.replace(private_value, aliases[private_value])
            return value
        if isinstance(value, dict):
            return {key: redact(item) for key, item in value.items()}
        if isinstance(value, list):
            return [redact(item) for item in value]
        return value

    public_cases = []
    for case in cases:
        kept = {
            key: case[key]
            for key in (
                "id",
                "corpus_role",
                "approval_status",
                "status",
                "original_text",
                "tags",
                "checks",
                "elapsed_ms",
            )
        }
        kept["fixture"] = case.get("fixture")
        kept["blocked_error_type"] = case.get("blocked_error_type")
        public_case = redact(kept)
        public_case["full_raw_case_sha256"] = canonical_digest(case)
        if len(public_case["checks"]) != len(case["checks"]):
            raise ValueError("Public export changed the number of acceptance checks")
        if [item["status"] for item in public_case["checks"]] != [
            item["status"] for item in case["checks"]
        ]:
            raise ValueError("Public export changed acceptance check statuses")
        public_cases.append(public_case)
    public = redact({key: value for key, value in report.items() if key != "cases"})
    public["cases"] = public_cases
    public["public_export"] = {
        "schema_version": 1,
        "raw_report_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "raw_report_bytes": len(raw_bytes),
        "full_case_digest_algorithm": "SHA256 of UTF-8 JSON with sort_keys=True, ensure_ascii=False, separators=(',', ':')",
        "omitted_case_fields": OMITTED,
        "privacy": "Runtime IDs, session capabilities, names and birth dates omitted; any check references use stable aliases.",
        "retained_check_count": sum(len(case["checks"]) for case in public_cases),
        "case_counts": {
            "all": 50,
            "our_proposed_not_team_approved": 40,
            "original_u2": 10,
        },
        "reproduction": [
            "uv run --project backend python scripts/run_submission_eval.py --today 2026-10-28 --output /tmp/submission-50-raw.json",
            "The evaluator exits 1 because original u2-03 remains failed; retain the report and continue with export.",
            "python3 scripts/export_submission_report.py --input /tmp/submission-50-raw.json --output eval/reports/submission-proposed-2026-10-28.json",
            "Fresh runs have new runtime IDs and timestamps, so raw digests differ; recompute the matching public export.",
        ],
    }
    serialized = json.dumps(public, ensure_ascii=False, indent=2) + "\n"
    if any(private_value in serialized for private_value in aliases):
        raise ValueError("A private runtime value remained in the public export")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(serialized, encoding="utf-8")
    return public


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    public = export_report(args.input, args.output)
    print(
        json.dumps(
            {
                "summary": public["summary"],
                "checks": public["public_export"]["retained_check_count"],
                "public_bytes": args.output.stat().st_size,
                "raw_sha256": public["public_export"]["raw_report_sha256"],
            }
        )
    )


if __name__ == "__main__":
    main()
