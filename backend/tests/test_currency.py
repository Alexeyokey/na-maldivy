from dataclasses import FrozenInstanceError
from datetime import date, datetime
from decimal import Decimal, localcontext

import httpx
import pytest

from app.tools.currency import (
    CBR_DAILY_URL,
    CurrencyDataError,
    CurrencyFetchError,
    CurrencyRate,
    CurrencyRates,
    load_cbr_rates,
)

# Deliberately invented values: tests exercise the format and arithmetic,
# not current market rates. JPY/AMD catch confusing a nominal with one unit.
XML = """<?xml version="1.0" encoding="windows-1251"?>
<ValCurs Date="23.10.2026" name="Foreign Currency Market">
  <Valute ID="R01235"><NumCode>840</NumCode><CharCode>USD</CharCode>
    <Nominal>1</Nominal><Name>Доллар США</Name><Value>90,5000</Value></Valute>
  <Valute ID="R01239"><NumCode>978</NumCode><CharCode>EUR</CharCode>
    <Nominal>1</Nominal><Name>Евро</Name><Value>100,0000</Value></Valute>
  <Valute ID="R01820"><NumCode>392</NumCode><CharCode>JPY</CharCode>
    <Nominal>100</Nominal><Name>Иена</Name><Value>60,2500</Value></Valute>
  <Valute ID="R01060"><NumCode>051</NumCode><CharCode>AMD</CharCode>
    <Nominal>100</Nominal><Name>Армянский драм</Name><Value>20,0000</Value></Valute>
</ValCurs>"""


@pytest.fixture
def rates() -> CurrencyRates:
    return CurrencyRates.from_xml(XML.encode("cp1251"), requested_date=date(2026, 10, 25))


def test_parse_original_cp1251_bytes_and_honor_nominal(rates: CurrencyRates) -> None:
    assert rates.effective_date == date(2026, 10, 23)
    assert rates.requested_date == date(2026, 10, 25)
    assert rates.source_url == CBR_DAILY_URL
    assert rates.rates["JPY"].nominal == 100
    assert rates.rates["JPY"].value_rub == Decimal("60.2500")
    assert rates.rates["JPY"].unit_rub == Decimal("0.6025")
    assert rates.convert(100, "JPY").exact_amount == Decimal("60.2500")
    assert rates.convert(100, "JPY").amount_rub == 60
    assert rates.convert(1_000, "AMD").amount_rub == 200


@pytest.mark.parametrize("amount", [Decimal("1"), 1, "1.000"])
def test_rub_rounding_uses_half_up_once(rates: CurrencyRates, amount: Decimal | int | str) -> None:
    result = rates.convert(amount, "USD")
    assert result.source_amount == Decimal(1)
    assert result.exact_amount == Decimal("90.5000")
    assert result.rounded_amount == result.amount_rub == 91
    assert result.from_currency == "USD"
    assert result.to_currency == "RUB"
    assert result.effective_date == rates.effective_date
    assert result.requested_date == rates.requested_date
    assert result.source_url == CBR_DAILY_URL


def test_fractional_source_is_not_rounded_before_conversion(rates: CurrencyRates) -> None:
    result = rates.convert("0.5", "USD")
    assert result.exact_amount == Decimal("45.25000")
    assert result.amount_rub == 45


def test_identity_reverse_and_cross_currency_conversions(rates: CurrencyRates) -> None:
    assert rates.rates["RUB"] == CurrencyRate("RUB", 1, Decimal(1))
    assert rates.convert("123.5", "RUB").amount_rub == 124
    assert rates.convert(0, "RUB").amount_rub == 0
    assert rates.convert(100, "EUR", "EUR").exact_amount == 100
    assert rates.convert(181, "RUB", "USD").rounded_amount == 2
    result = rates.convert(200, "EUR", "USD")
    assert result.rounded_amount == 221
    assert abs(result.exact_amount - Decimal(20_000) / Decimal("90.5")) < Decimal("1e-24")
    with pytest.raises(CurrencyDataError, match="RUB target"):
        _ = result.amount_rub


def test_conversion_ignores_low_ambient_decimal_precision(rates: CurrencyRates) -> None:
    with localcontext() as context:
        context.prec = 3
        result = rates.convert("123456789012345678901234567890.125", "USD")
    assert result.exact_amount == Decimal("11172839405617283940561728394056.3125")
    assert result.amount_rub == 11172839405617283940561728394056


def test_rates_snapshot_and_conversion_are_immutable(rates: CurrencyRates) -> None:
    with pytest.raises(TypeError):
        rates.rates["USD"] = CurrencyRate("USD", 1, Decimal(1))
    with pytest.raises(FrozenInstanceError):
        rates.rates["USD"].nominal = 100
    with pytest.raises(FrozenInstanceError):
        rates.convert(1, "USD").rounded_amount = 1
    original = {"USD": CurrencyRate("USD", 1, Decimal(90))}
    snapshot = CurrencyRates(date(2026, 10, 23), original)
    original.clear()
    assert snapshot.convert(1, "USD").amount_rub == 90


def test_age_is_transparent_and_does_not_claim_weekend_data_is_current(rates: CurrencyRates) -> None:
    result = rates.convert(1, "USD")
    for item in (rates, result):
        assert item.age_days(date(2026, 10, 25)) == 2
        assert not item.is_stale(date(2026, 10, 26), max_age_days=3)
        assert item.is_stale(date(2026, 10, 27), max_age_days=3)
        assert item.age_days(date(2026, 10, 22)) == -1
        assert not item.is_stale(date(2026, 10, 22))
        with pytest.raises(CurrencyDataError):
            item.is_stale(date(2026, 10, 25), max_age_days=-1)


@pytest.mark.parametrize("amount", [1.5, True, "NaN", "sNaN", "Infinity", "-1", "bad-secret", "1,50"])
def test_invalid_amount_has_safe_error(rates: CurrencyRates, amount: object) -> None:
    with pytest.raises(CurrencyDataError) as error:
        rates.convert(amount, "USD")
    assert "bad-secret" not in str(error.value)


@pytest.mark.parametrize("code", ["usd", "ZZZ", "US", " USD", "USД", "secret-token"])
def test_unknown_or_invalid_currency_is_never_assumed_rub(rates: CurrencyRates, code: str) -> None:
    with pytest.raises(CurrencyDataError) as error:
        rates.convert(100, code)
    assert "secret-token" not in str(error.value)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("<Nominal>1</Nominal>", ""),
        ("<Nominal>1</Nominal>", "<Nominal>0</Nominal>"),
        ("<Nominal>1</Nominal>", "<Nominal>-1</Nominal>"),
        ("<Nominal>1</Nominal>", "<Nominal>1.5</Nominal>"),
        ("<Nominal>1</Nominal>", "<Nominal>1</Nominal><Nominal>100</Nominal>"),
        ("<Value>90,5000</Value>", "<Value>0</Value>"),
        ("<Value>90,5000</Value>", "<Value>-90,5</Value>"),
        ("<Value>90,5000</Value>", "<Value>NaN</Value>"),
        ("<Value>90,5000</Value>", "<Value>Infinity</Value>"),
        ("<Value>90,5000</Value>", "<Value>9,0,5</Value>"),
        ("<Value>90,5000</Value>", "<Value />"),
        ("<CharCode>EUR</CharCode>", "<CharCode>USD</CharCode>"),
        ("<CharCode>USD</CharCode>", "<CharCode>RUB</CharCode>"),
        ("<CharCode>USD</CharCode>", "<CharCode>usd</CharCode>"),
        ("<CharCode>USD</CharCode>", "<CharCode><Code>USD</Code></CharCode>"),
        ('Date="23.10.2026"', 'Date="2026-10-23"'),
        ('Date="23.10.2026"', 'Date="31.02.2026"'),
        ('Date="23.10.2026"', ""),
    ],
)
def test_reject_invalid_or_ambiguous_cbr_records(old: str, new: str) -> None:
    with pytest.raises(CurrencyDataError):
        CurrencyRates.from_xml(XML.replace(old, new, 1))


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"<ValCurs Date='23.10.2026'>",
        b"<html><body>secret-token</body></html>",
        b"<ValCurs Date='23.10.2026'></ValCurs>",
        b"<ValCurs Date='23.10.2026'><Error>secret-token</Error></ValCurs>",
        b"<!DOCTYPE ValCurs [<!ENTITY x 'secret-token'>]><ValCurs>&x;</ValCurs>",
        "<!DOCTYPE ValCurs><ValCurs />".encode("utf-16"),
    ],
)
def test_non_cbr_invalid_or_dtd_xml_has_safe_error(payload: bytes) -> None:
    with pytest.raises(CurrencyDataError) as error:
        CurrencyRates.from_xml(payload)
    assert "secret-token" not in str(error.value)


@pytest.mark.parametrize(
    "source",
    [
        "https://example.com/scripts/XML_daily.asp",
        "http://www.cbr.ru/scripts/XML_daily.asp",
        "https://user:secret-token@www.cbr.ru/scripts/XML_daily.asp",
        CBR_DAILY_URL + "?token=secret-token",
        CBR_DAILY_URL + "#secret-token",
        "https://www.cbr.ru:broken/scripts/XML_daily.asp",
    ],
)
def test_provenance_cannot_store_secrets_or_claim_another_provider(source: str) -> None:
    with pytest.raises(CurrencyDataError) as error:
        CurrencyRates.from_xml(XML, source_url=source)
    assert "secret-token" not in str(error.value)


@pytest.mark.parametrize("value", [Decimal(0), Decimal(-1), Decimal("NaN"), Decimal("Infinity"), 90.5])
def test_manual_rates_need_positive_finite_decimals(value: Decimal) -> None:
    with pytest.raises(CurrencyDataError):
        CurrencyRate("USD", 1, value)


def test_requested_dates_do_not_accept_datetime(rates: CurrencyRates) -> None:
    with pytest.raises(CurrencyDataError):
        CurrencyRates.from_xml(XML, requested_date=datetime(2026, 10, 25))
    with pytest.raises(CurrencyDataError):
        rates.age_days(datetime(2026, 10, 25))


async def test_loader_requests_dd_mm_yyyy_and_keeps_effective_xml_date() -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=XML.encode("cp1251"), headers={"content-type": "application/xml"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        snapshot = await load_cbr_rates(client, date(2026, 10, 25))
        latest = await load_cbr_rates(client)

    assert str(seen[0].url.copy_with(query=None)) == CBR_DAILY_URL
    assert seen[0].url.params["date_req"] == "25/10/2026"
    assert not seen[1].url.params
    assert snapshot.requested_date == date(2026, 10, 25)
    assert snapshot.effective_date == latest.effective_date == date(2026, 10, 23)
    assert latest.requested_date is None


@pytest.mark.parametrize("failure", ["status", "network"])
async def test_fetch_errors_do_not_expose_provider_body(failure: str) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if failure == "network":
            raise httpx.ConnectError("secret-token", request=request)
        return httpx.Response(500, text="secret-token")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(CurrencyFetchError) as error:
            await load_cbr_rates(client)
    assert "secret-token" not in str(error.value)


async def test_loader_preserves_replay_miss_instead_of_falling_back_to_network() -> None:
    class ReplayMiss(Exception):
        pass

    class ReplayOnlyClient:
        async def get(self, url: str, *, params: dict[str, str] | None = None) -> httpx.Response:
            raise ReplayMiss("No recording")

    with pytest.raises(ReplayMiss):
        await load_cbr_rates(ReplayOnlyClient(), date(2026, 10, 25))
