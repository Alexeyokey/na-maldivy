"""Decimal conversions using a dated snapshot of official Bank of Russia rates.

``Value`` is the RUB cost of ``Nominal`` currency units, not necessarily one
unit. The response's ``ValCurs/@Date`` is authoritative even when it differs
from the requested day (for example on a weekend). These indicative official
rates are not a bank's exchange/booking quote.

Source format: https://www.cbr.ru/development/SXML/
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from types import MappingProxyType
from typing import Protocol
from xml.etree import ElementTree

import httpx

from app.tools.http_cache import CacheError

CBR_DAILY_URL = "https://www.cbr.ru/scripts/XML_daily.asp"
_MAX_XML_BYTES = 1_000_000
_CODE = re.compile(r"[A-Z]{3}\Z")
_NUMBER = re.compile(r"[0-9]+(?:[,.][0-9]+)?\Z")


class CurrencyDataError(ValueError):
    """Invalid/missing currency data; messages never include response bodies."""


class CurrencyFetchError(RuntimeError):
    """The official endpoint could not return a successful response."""


class CurrencyHttpClient(Protocol):
    """Both httpx.AsyncClient and the record/replay client satisfy this API."""

    async def get(self, url: str, *, params: dict[str, str] | None = None) -> httpx.Response: ...


def _date(value: Date, label: str) -> Date:
    if type(value) is not Date:
        raise CurrencyDataError(f"{label} must be a calendar date")
    return value


def _currency(code: str) -> str:
    if not isinstance(code, str) or _CODE.fullmatch(code) is None:
        raise CurrencyDataError("Currency code must contain three uppercase ASCII letters")
    return code


def _source_url(value: str) -> str:
    # Persist only public provenance. Credentials, fragments and query strings
    # must not leak into conversion metadata (requested_date has its own field).
    if not isinstance(value, str) or value not in {CBR_DAILY_URL, "https://cbr.ru/scripts/XML_daily.asp"}:
        raise CurrencyDataError("Source must be the public HTTPS CBR daily XML endpoint without query parameters")
    return value


def _amount(value: Decimal | int | str) -> Decimal:
    # In particular, reject binary floats and bool (a subclass of int).
    if not isinstance(value, Decimal | int | str) or isinstance(value, bool):
        raise CurrencyDataError("Amount must be Decimal, integer or a decimal string")
    try:
        amount = value if isinstance(value, Decimal) else Decimal(value)
    except (InvalidOperation, ValueError):
        raise CurrencyDataError("Amount must be a finite nonnegative decimal") from None
    if not amount.is_finite() or amount < 0:
        raise CurrencyDataError("Amount must be a finite nonnegative decimal")
    return amount


@dataclass(frozen=True, slots=True)
class CurrencyRate:
    code: str
    nominal: int
    value_rub: Decimal

    def __post_init__(self) -> None:
        _currency(self.code)
        if type(self.nominal) is not int or self.nominal <= 0:
            raise CurrencyDataError("Currency nominal must be a positive integer")
        if not isinstance(self.value_rub, Decimal) or not self.value_rub.is_finite() or self.value_rub <= 0:
            raise CurrencyDataError("Currency rate must be a positive finite Decimal")

    @property
    def unit_rub(self) -> Decimal:
        with localcontext() as context:
            context.prec = max(50, len(self.value_rub.as_tuple().digits) + len(str(self.nominal)) + 20)
            return self.value_rub / self.nominal


@dataclass(frozen=True, slots=True)
class Conversion:
    source_amount: Decimal
    from_currency: str
    to_currency: str
    exact_amount: Decimal
    rounded_amount: int
    effective_date: Date
    requested_date: Date | None
    source_url: str

    @property
    def amount_rub(self) -> int:
        """Convenience for the default RUB target; cross conversions use rounded_amount."""
        if self.to_currency != "RUB":
            raise CurrencyDataError("This conversion does not have a RUB target")
        return self.rounded_amount

    def age_days(self, as_of: Date) -> int:
        """Signed calendar age; a negative value makes future-effective data visible."""
        return (_date(as_of, "Reference date") - self.effective_date).days

    def is_stale(self, as_of: Date, *, max_age_days: int = 3) -> bool:
        if type(max_age_days) is not int or max_age_days < 0:
            raise CurrencyDataError("Maximum age must be a nonnegative integer")
        return self.age_days(as_of) > max_age_days


@dataclass(frozen=True, slots=True)
class CurrencyRates:
    effective_date: Date
    rates: Mapping[str, CurrencyRate]
    requested_date: Date | None = None
    source_url: str = CBR_DAILY_URL

    def __post_init__(self) -> None:
        _date(self.effective_date, "Effective date")
        if self.requested_date is not None:
            _date(self.requested_date, "Requested date")
        _source_url(self.source_url)
        rates = dict(self.rates)
        if not rates or any(not isinstance(rate, CurrencyRate) or code != rate.code for code, rate in rates.items()):
            raise CurrencyDataError("Currency rates must be a nonempty mapping keyed by their codes")
        rub = CurrencyRate("RUB", 1, Decimal(1))
        if "RUB" in rates and rates["RUB"] != rub:
            raise CurrencyDataError("RUB identity rate must equal one")
        rates["RUB"] = rub
        object.__setattr__(self, "rates", MappingProxyType(rates))

    @classmethod
    def from_xml(
        cls,
        xml: bytes | str,
        source_url: str = CBR_DAILY_URL,
        *,
        requested_date: Date | None = None,
    ) -> CurrencyRates:
        """Parse original response bytes, preserving its declared encoding and date."""
        _source_url(source_url)
        if requested_date is not None:
            _date(requested_date, "Requested date")
        if not isinstance(xml, bytes | str):
            raise CurrencyDataError("CBR XML must be bytes or text")
        raw = xml.encode("utf-8") if isinstance(xml, str) else xml
        # No external entities or DTD are needed by the documented CBR format.
        # Remove NULs so UTF-16/32 declarations are covered by the same guard.
        markup = raw.replace(b"\x00", b"").upper()
        if len(raw) > _MAX_XML_BYTES or b"<!DOCTYPE" in markup or b"<!ENTITY" in markup:
            raise CurrencyDataError("Unsupported CBR XML document")
        try:
            root = ElementTree.fromstring(xml)
        except (ElementTree.ParseError, ValueError, LookupError):
            raise CurrencyDataError("Invalid CBR XML document") from None
        if root.tag != "ValCurs":
            raise CurrencyDataError("Expected a CBR ValCurs response")
        date_text = root.get("Date", "")
        if re.fullmatch(r"[0-9]{2}\.[0-9]{2}\.[0-9]{4}", date_text) is None:
            raise CurrencyDataError("Invalid CBR effective date")
        try:
            effective_date = datetime.strptime(date_text, "%d.%m.%Y").date()
        except ValueError:
            raise CurrencyDataError("Invalid CBR effective date") from None

        rates: dict[str, CurrencyRate] = {}
        for entry in root:
            if entry.tag != "Valute":
                raise CurrencyDataError("Unexpected element in CBR rates")
            code = _single_text(entry, "CharCode")
            _currency(code)
            if code in rates or code == "RUB":
                raise CurrencyDataError("Duplicate currency code or unexpected RUB entry")
            nominal_text = _single_text(entry, "Nominal")
            if re.fullmatch(r"[0-9]+", nominal_text) is None:
                raise CurrencyDataError("Invalid CBR currency nominal")
            value_text = _single_text(entry, "Value")
            if _NUMBER.fullmatch(value_text) is None:
                raise CurrencyDataError("Invalid CBR currency value")
            try:
                rates[code] = CurrencyRate(code, int(nominal_text), Decimal(value_text.replace(",", ".")))
            except (InvalidOperation, ValueError):
                raise CurrencyDataError("Invalid CBR currency rate") from None
        if not rates:
            raise CurrencyDataError("CBR response contains no currency rates")
        return cls(effective_date, rates, requested_date, source_url)

    def convert(self, amount: Decimal | int | str, from_currency: str, to_currency: str = "RUB") -> Conversion:
        source_amount = _amount(amount)
        source = self.rates.get(_currency(from_currency))
        target = self.rates.get(_currency(to_currency))
        if source is None or target is None:
            raise CurrencyDataError("Currency is absent from this CBR snapshot")
        # Retain sufficient significant digits even if the caller lowered the
        # global Decimal precision. Round once, after the complete conversion.
        precision = (
            sum(
                len(value.as_tuple().digits) + abs(value.as_tuple().exponent)
                for value in (source_amount, source.value_rub, target.value_rub)
            )
            + len(str(source.nominal))
            + len(str(target.nominal))
            + 30
        )
        try:
            with localcontext() as context:
                context.prec = max(50, precision)
                exact = source_amount * source.value_rub * target.nominal / source.nominal / target.value_rub
                rounded = int(exact.quantize(Decimal(1), rounding=ROUND_HALF_UP))
        except (InvalidOperation, OverflowError):
            raise CurrencyDataError("Amount cannot be converted with these rates") from None
        return Conversion(
            source_amount,
            from_currency,
            to_currency,
            exact,
            rounded,
            self.effective_date,
            self.requested_date,
            self.source_url,
        )

    def age_days(self, as_of: Date) -> int:
        return (_date(as_of, "Reference date") - self.effective_date).days

    def is_stale(self, as_of: Date, *, max_age_days: int = 3) -> bool:
        if type(max_age_days) is not int or max_age_days < 0:
            raise CurrencyDataError("Maximum age must be a nonnegative integer")
        return self.age_days(as_of) > max_age_days


def _single_text(entry: ElementTree.Element, field: str) -> str:
    elements = entry.findall(field)
    if len(elements) != 1 or elements[0].text is None or len(elements[0]):
        raise CurrencyDataError("Missing or repeated field in CBR currency entry")
    return elements[0].text.strip()


async def load_cbr_rates(client: CurrencyHttpClient, date: Date | None = None) -> CurrencyRates:
    """Fetch via the supplied client's mode; there is no live-network fallback.

    With no date, CBR returns the latest registered snapshot, which can be old
    when replaying a recording. Consumers must inspect effective_date/age_days.
    """
    if date is not None:
        _date(date, "Requested date")
    params = {"date_req": f"{date.day:02}/{date.month:02}/{date.year:04}"} if date is not None else None
    try:
        response = await client.get(CBR_DAILY_URL, params=params)
        response.raise_for_status()
    except (httpx.HTTPError, CacheError):
        raise CurrencyFetchError("Could not load official CBR currency rates") from None
    return CurrencyRates.from_xml(response.content, requested_date=date)
