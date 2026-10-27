from pathlib import Path

import yaml


def test_ten_distinct_u2_scenarios_have_explicit_properties():
    cases = yaml.safe_load((Path(__file__).resolve().parents[2] / "eval/cases/u2.yaml").read_text(encoding="utf-8"))
    assert len(cases) == 10
    assert len({item["id"] for item in cases}) == 10
    assert len({item["text"] for item in cases}) == 10
    for item in cases:
        assert item["id"].startswith("u2-") and item["text"].strip()
        assert isinstance(item["checks"], dict) and len(item["checks"]) >= 3
        assert item["tags"]
    assert {"train", "climate", "family", "pets", "impossible", "provocation", "date_shift"} <= {
        tag for item in cases for tag in item["tags"]
    }


def test_all_zone_yaml_files_have_valid_ids_and_no_duplicate_request_texts():
    directory = Path(__file__).resolve().parents[2] / "eval/cases"
    all_cases = []
    for path in directory.glob("*.yaml"):
        cases = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(cases, list) and cases
        for case in cases:
            assert {"id", "text", "checks", "tags"} <= set(case)
            assert all(isinstance(key, str) and "." in key for key in case["checks"])
            assert isinstance(case["text"], str) and case["text"].strip()
        all_cases.extend(cases)
    assert len({case["id"] for case in all_cases}) == len(all_cases)
    assert len({case["text"].strip().casefold() for case in all_cases}) == len(all_cases)


def test_followup_has_explicit_context_and_cancellation_does_not_invent_a_deadline():
    cases = yaml.safe_load((Path(__file__).resolve().parents[2] / "eval/cases/u2.yaml").read_text(encoding="utf-8"))
    by_id = {case["id"]: case for case in cases}
    assert len(by_id["u2-08"]["context"]) >= 2
    assert "hotels.all.cancellation_deadline.present" not in by_id["u2-06"]["checks"]
    assert by_id["u2-06"]["checks"]["response.unknown_cancellation_deadline_explicit"] is True
