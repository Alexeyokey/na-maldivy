"""Реестр D1: валидация аргументов и результатов, выбор источника данных."""

from collections.abc import Mapping
from typing import Any

from app.config import Settings
from app.llm.base import ToolSpec
from app.tools.base import Tool, tool_spec
from app.tools.stubs import create_stub_tools

type ToolRegistry = dict[str, Tool[Any, Any]]


def create_tools(settings: Settings) -> ToolRegistry:
    if settings.tools_mode == "stub":
        return create_stub_tools()
    # Imports stay lazy: the default stub mode also works before D2/D3 are merged.
    try:
        from app.tools.d5_adapter import DestinationTool
        from app.tools.flights import FlightTool
        from app.tools.hotels import HotelSearchTool
        from app.tools.s4_adapter import S4TransferTool
    except ModuleNotFoundError as exc:
        if exc.name not in {"app.tools.d5_adapter", "app.tools.flights", "app.tools.hotels"}:
            raise
        raise RuntimeError("TOOLS_MODE=real requires the D2, D3 and D5 implementation PRs") from None
    tools = (
        FlightTool(data_dir=settings.data_dir),
        HotelSearchTool(data_dir=settings.data_dir),
        DestinationTool(data_dir=settings.data_dir),
        S4TransferTool(data_dir=settings.data_dir),
    )
    return {tool.name: tool for tool in tools}


# Готовый реестр для разработки без Settings/.env и без сети.
# Приложение с конфигурацией должно вызывать create_tools(settings).
TOOLS: ToolRegistry = create_stub_tools()


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
