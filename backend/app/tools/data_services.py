"""Configured data services before the shared O1 tool-registry adapter exists."""

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import Literal

from app.config import Settings
from app.tools.flights import FlightTool
from app.tools.hotels import HotelSearchTool
from app.tools.http_cache import CachedHttpClient
from app.tools.trains import TrainTool
from app.tools.transport import DATA_DIR


@dataclass(frozen=True)
class DataServices:
    flights: FlightTool
    hotels: HotelSearchTool
    trains: TrainTool
    currency_http: CachedHttpClient


@asynccontextmanager
async def open_data_services(
    settings: Settings,
    *,
    transport_mode: Literal["synthetic", "provider"] = "synthetic",
) -> AsyncIterator[DataServices]:
    """Explicit provider selection; replay is the safe default in Settings.

    Provider responses use separate cache directories. Use a separate cache root
    for each account/tenant because credentials intentionally aren't cache keys.
    A missing key/record never turns into a synthetic provider response.
    This factory does not change A2/SSE or mark D1 contracts approved.
    """
    root = settings.data_cache_dir or DATA_DIR / "cache"
    async with AsyncExitStack() as stack:
        flight_http = await stack.enter_async_context(
            CachedHttpClient(mode=settings.cache_mode, cache_dir=root / "aviasales")
        )
        train_http = await stack.enter_async_context(
            CachedHttpClient(mode=settings.cache_mode, cache_dir=root / "yandex-rasp")
        )
        currency_http = await stack.enter_async_context(
            CachedHttpClient(mode=settings.cache_mode, cache_dir=root / "cbr")
        )
        yield DataServices(
            flights=FlightTool(
                mode=transport_mode,
                seed=settings.data_seed,
                http=flight_http,
                token=settings.travelpayouts_token.get_secret_value() if settings.travelpayouts_token else None,
            ),
            hotels=HotelSearchTool(),
            trains=TrainTool(
                mode=transport_mode,
                seed=settings.data_seed,
                http=train_http,
                key=settings.yandex_rasp_api_key.get_secret_value() if settings.yandex_rasp_api_key else None,
            ),
            currency_http=currency_http,
        )
