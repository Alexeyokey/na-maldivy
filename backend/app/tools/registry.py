"""Реестр D1: валидация аргументов и результатов, выбор источника данных."""

from collections.abc import Mapping
from typing import Any

from app.config import Settings
from app.llm.base import ToolSpec
from app.tools.base import Tool, tool_spec
from app.tools.stubs import create_stub_tools


class ToolRegistry(dict[str, Tool[Any, Any]]):
    """Tools plus resources owned by their factory; injected resources remain caller-owned."""

    def __init__(self, tools, *, owned_clients=()):
        super().__init__(tools)
        self._owned_clients = tuple(owned_clients)

    async def aclose(self) -> None:
        for client in self._owned_clients:
            await client.aclose()


def create_tools(settings: Settings, *, flight_http=None, train_http=None) -> ToolRegistry:
    if settings.tools_mode == "stub":
        return ToolRegistry(create_stub_tools())
    # Imports stay lazy: the default stub mode also works before implementation PRs are merged.
    try:
        from app.tools.d5_adapter import DestinationTool
        from app.tools.flights import FlightTool
        from app.tools.hotels import HotelSearchTool
        from app.tools.price_calendar import PriceCalendarTool
        from app.tools.s4_adapter import S4TransferTool
        from app.tools.trains import TrainTool
    except ModuleNotFoundError as exc:
        if exc.name not in {
            "app.tools.d5_adapter",
            "app.tools.flights",
            "app.tools.hotels",
            "app.tools.trains",
            "app.tools.price_calendar",
        }:
            raise
        raise RuntimeError("TOOLS_MODE=real requires the D2, D3, D5, D6 and D8 implementation PRs") from None
    owned_clients = []
    if settings.flights_mode in {"aviasales", "demo"}:
        # The explicit demo never forwards a configured credential, including in replay.
        token = (
            settings.aviasales_api_token.get_secret_value()
            if settings.flights_mode == "aviasales" and settings.aviasales_api_token
            else None
        )
        if flight_http is None:
            from app.tools.http_cache import CachedHttpClient

            default_cache = "demo/aviasales" if settings.flights_mode == "demo" else "cache/aviasales/runtime"
            flight_http = CachedHttpClient(
                mode=settings.cache_mode,
                cache_dir=settings.aviasales_cache_dir or settings.data_dir / default_cache,
                secret_values=(token,) if token else (),
            )
            owned_clients.append(flight_http)
        if settings.flights_mode == "demo":
            from app.tools.demo_flights import DemoFlightTool

            flights = DemoFlightTool(http=flight_http, data_dir=settings.data_dir)
        else:
            flights = FlightTool(mode="provider", http=flight_http, token=token, data_dir=settings.data_dir)
    else:
        flights = FlightTool(data_dir=settings.data_dir)
    if settings.trains_mode == "yandex":
        key = settings.yandex_rasp_api_key.get_secret_value() if settings.yandex_rasp_api_key else None
        if train_http is None:
            from app.tools.http_cache import CachedHttpClient

            train_http = CachedHttpClient(
                mode=settings.cache_mode,
                cache_dir=settings.yandex_cache_dir or settings.data_dir / "cache/yandex/runtime",
                secret_values=(key,) if key else (),
            )
            owned_clients.append(train_http)
        trains = TrainTool(mode="provider", http=train_http, key=key, data_dir=settings.data_dir)
    else:
        trains = TrainTool(data_dir=settings.data_dir)
    tools = (
        flights,
        HotelSearchTool(data_dir=settings.data_dir, include_demo=settings.flights_mode == "demo"),
        DestinationTool(data_dir=settings.data_dir),
        S4TransferTool(data_dir=settings.data_dir, include_demo=settings.flights_mode == "demo"),
        trains,
        PriceCalendarTool(flights=flights),
    )
    return ToolRegistry({tool.name: tool for tool in tools}, owned_clients=owned_clients)


# Готовый реестр для разработки без Settings/.env и без сети.
# Приложение с конфигурацией должно вызывать create_tools(settings).
TOOLS: ToolRegistry = ToolRegistry(create_stub_tools())


def tool_specs(tools: Mapping[str, Tool[Any, Any]] | None = None) -> list[ToolSpec]:
    return [tool_spec(tool) for tool in (TOOLS if tools is None else tools).values()]


async def run_tool(
    name: str,
    arguments: Mapping[str, Any],
    *,
    tools: Mapping[str, Tool[Any, Any]] | None = None,
) -> Any:
    """Граница диспетчеризации для A2: проверяет JSON-аргументы до запуска инструмента.

    Неизвестное имя — KeyError, неверные аргументы/ответ — ValidationError.
    Преобразование этих ошибок в события диалога остаётся в A2.
    """
    tool = (TOOLS if tools is None else tools)[name]
    input = tool.input_model.model_validate(dict(arguments))
    result = await tool.run(input)
    return tool.output_adapter.validate_python(result)
