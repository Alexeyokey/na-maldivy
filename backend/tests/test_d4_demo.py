"""Acceptance checks for the independently packaged D4 command."""

# ruff: noqa: ASYNC240 - assertions inspect small local fixtures, never service I/O.

import hashlib
import json
import shutil
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.d4_cli import main, run_demo
from app.tools.currency import CurrencyFetchError, CurrencyRates
from app.tools.http_cache import CachedHttpClient

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def configured_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("CACHE_MODE", "replay")
    monkeypatch.delenv("DATA_DIR", raising=False)


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    async def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("D4 replay tried to access an HTTP transport")

    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)


def snapshot_hashes(cache_root: Path) -> dict[str, str]:
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (cache_root / "cbr").glob("*.json")}


def test_real_snapshot_demo_is_repeatable_without_network_or_cache_writes(
    no_network: None, capsys: pytest.CaptureFixture[str]
) -> None:
    cache_root = ROOT / "data/cache"
    before = snapshot_hashes(cache_root)
    outputs = []
    for _ in range(5):
        assert main(["--json", "--amount", "100.50", "--currency", "JPY"]) == 0
        captured = capsys.readouterr()
        assert captured.err == ""
        outputs.append(captured.out)
    assert len(set(outputs)) == 1
    assert snapshot_hashes(cache_root) == before

    payload = json.loads(outputs[0])
    rates = CurrencyRates.from_xml((ROOT / "data/sources/cbr-daily.xml").read_bytes())
    expected = rates.convert("100.50", "JPY")
    assert payload["scope"] == "d4_cache_currency_only"
    assert payload["cache_mode"] == "replay"
    assert payload["effective_date"] == "2026-10-24"
    assert payload["requested_date"] is None
    assert payload["currency_count"] == 55
    assert payload["conversion"] == {
        "source_amount": "100.50",
        "from_currency": "JPY",
        "to_currency": "RUB",
        "exact_amount": str(expected.exact_amount),
        "amount_rub": expected.amount_rub,
        "source_url": rates.source_url,
    }


async def test_settings_environment_selects_cache_root_and_replay(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, no_network: None
) -> None:
    shutil.copytree(ROOT / "data/cache/cbr", tmp_path / "provider-cache/cache/cbr")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "provider-cache"))
    settings = Settings(_env_file=None)
    assert settings.cache_mode == "replay"
    assert settings.data_dir == tmp_path / "provider-cache"
    payload = await run_demo(settings=settings, amount="1", currency="RUB")
    assert payload["conversion"]["amount_rub"] == 1
    assert sorted(path.name for path in tmp_path.iterdir()) == ["provider-cache"]


async def test_replay_miss_from_configured_directory_never_creates_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, no_network: None
) -> None:
    missing_root = tmp_path / "not-recorded"
    monkeypatch.setenv("DATA_DIR", str(missing_root))
    with pytest.raises(CurrencyFetchError):
        await run_demo(settings=Settings(_env_file=None))
    assert not missing_root.exists()


def test_cli_reports_safe_miss_and_nonzero_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, no_network: None, capsys: pytest.CaptureFixture[str]
) -> None:
    missing_root = tmp_path / "secret-in-path"
    monkeypatch.setenv("DATA_DIR", str(missing_root))
    assert main(["--json"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Could not load official CBR currency rates" in captured.err
    assert "secret-in-path" not in captured.err
    assert not missing_root.exists()


@pytest.mark.parametrize("mode", ["record", "live"])
async def test_settings_mode_reaches_real_http_wrapper(
    mode: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app import d4_cli

    xml = (ROOT / "data/sources/cbr-daily.xml").read_bytes()
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=xml, headers={"Content-Type": "text/xml; charset=windows-1251"})

    monkeypatch.setenv("CACHE_MODE", mode)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:

        def configured_client(**kwargs: object) -> CachedHttpClient:
            return CachedHttpClient(client=transport, **kwargs)

        monkeypatch.setattr(d4_cli, "CachedHttpClient", configured_client)
        payload = await run_demo(settings=Settings(_env_file=None), amount="100.50", currency="USD")
        assert not transport.is_closed
    assert payload["cache_mode"] == mode
    assert len(seen) == 1
    assert str(seen[0].url) == "https://www.cbr.ru/scripts/XML_daily.asp"
    assert bool(list((tmp_path / "cache/cbr").glob("*.json"))) == (mode == "record")
    assert Decimal(payload["conversion"]["exact_amount"]) > 0


@pytest.mark.parametrize("args", [["--amount", "NaN"], ["--currency", "secret-token"]])
def test_cli_rejects_invalid_conversion_without_echoing_input(
    args: list[str], no_network: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(args) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Ошибка D4:" in captured.err
    assert args[-1] not in captured.err


def test_plaintext_explains_scope_and_historical_snapshot(no_network: None, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "только HTTP-кэш и конвертация валют" in captured.out
    assert "исторический снимок" in captured.out
    assert "2026-10-24" in captured.out


def test_invalid_settings_error_omits_pydantic_input(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CACHE_MODE", "private-secret-value")
    assert main(["--json"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Ошибка D4: некорректные параметры настроек.\n"
