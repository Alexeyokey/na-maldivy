"""The portable manifest must describe the whole bundle and its actual provenance."""

import importlib.util
import json
import shutil
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("check_demo_cache", ROOT / "scripts/check_demo_cache.py")
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


@pytest.fixture
def bundle(tmp_path):
    source = ROOT / "data/demo/aviasales"
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    manifest["entries"] = manifest["entries"][:1]
    record = manifest["entries"][0]
    shutil.copyfile(source / record["file"], tmp_path / record["file"])
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return tmp_path, manifest


async def test_complete_published_bundle_replays_without_an_http_transport(monkeypatch):
    async def forbidden(*_args, **_kwargs):
        pytest.fail("Public replay must not open a network transport")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    report = await CHECKER.check_cache()
    assert report == {"entries_checked": 47, "mode": "replay", "credentials_used": False, "network_used": False}


@pytest.mark.parametrize("drift", ["duplicate", "orphan"])
async def test_incomplete_or_double_counted_snapshot_cannot_pass_verification(bundle, drift):
    directory, manifest = bundle
    if drift == "duplicate":
        manifest["entries"].append(manifest["entries"][0])
        message = "duplicate files"
    else:
        (directory / ("0" * 64 + ".json")).write_text("{}", encoding="utf-8")
        message = "cover every response file exactly once"
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(AssertionError, match=message):
        await CHECKER.check_cache(directory)


@pytest.mark.parametrize("field", ["origin", "destination", "departure_date", "recorded_at", "body_sha256"])
async def test_correct_file_hash_cannot_hide_a_false_route_date_or_provenance(bundle, field):
    directory, manifest = bundle
    manifest["entries"][0][field] = "incorrect-provenance"
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(AssertionError, match="Manifest"):
        await CHECKER.check_cache(directory)
