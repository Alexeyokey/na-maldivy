"""Standalone D4 demo: public HTTP cache and dated official currency rates."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from pydantic import ValidationError

from app.config import Settings
from app.tools.currency import CurrencyDataError, CurrencyFetchError, load_cbr_rates
from app.tools.http_cache import CachedHttpClient, CacheError


async def run_demo(*, settings: Settings | None = None, amount: str = "100", currency: str = "USD") -> dict[str, Any]:
    """Use configured cache mode without connecting the chat or supplier tools."""
    settings = settings if settings is not None else Settings()
    cache_root = settings.data_dir / "cache"
    async with CachedHttpClient(mode=settings.cache_mode, cache_dir=cache_root / "cbr") as client:
        rates = await load_cbr_rates(client)
    conversion = rates.convert(amount, currency)
    return {
        "scope": "d4_cache_currency_only",
        "cache_mode": settings.cache_mode,
        "effective_date": rates.effective_date.isoformat(),
        "requested_date": rates.requested_date.isoformat() if rates.requested_date is not None else None,
        "currency_count": len(rates.rates),
        "conversion": {
            "source_amount": str(conversion.source_amount),
            "from_currency": conversion.from_currency,
            "to_currency": conversion.to_currency,
            "exact_amount": str(conversion.exact_amount),
            "amount_rub": conversion.amount_rub,
            "source_url": conversion.source_url,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="D4: HTTP-кэш и справочные курсы Банка России")
    parser.add_argument("--json", action="store_true", help="вывести воспроизводимый JSON")
    parser.add_argument("--amount", default="100", help="сумма десятичной строкой, например 100.50")
    parser.add_argument("--currency", default="USD", help="исходная валюта: трёхбуквенный код, например USD")
    args = parser.parse_args(argv)
    try:
        payload = asyncio.run(run_demo(amount=args.amount, currency=args.currency))
    except (CacheError, CurrencyDataError, CurrencyFetchError) as error:
        print(f"Ошибка D4: {error}", file=sys.stderr)
        return 2
    except ValidationError:
        # Settings validation can include secret-bearing input; never echo it.
        print("Ошибка D4: некорректные параметры настроек.", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        conversion = payload["conversion"]
        print("Демонстрация D4: только HTTP-кэш и конвертация валют.")
        print(f"Режим кэша: {payload['cache_mode']}; дата действия курса ЦБ: {payload['effective_date']}.")
        if payload["cache_mode"] == "replay":
            print("Используется сохранённый исторический снимок; его дата не означает актуальность сегодня.")
        print(f"{conversion['source_amount']} {conversion['from_currency']} → {conversion['amount_rub']} RUB")
        print("Курсы ЦБ справочные; это не коммерческая цена обмена или бронирования.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
