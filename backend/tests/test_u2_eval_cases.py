from pathlib import Path

import yaml


def test_ten_distinct_u2_scenarios_have_explicit_properties():
    cases = yaml.safe_load((Path(__file__).resolve().parents[2] / "eval/cases/u2.yaml").read_text())
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
