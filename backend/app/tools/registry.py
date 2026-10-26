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
    raise NotImplementedError("TOOLS_MODE=real ещё не реализован: адаптеры появятся в D2, D3, D5, D6 и S4")


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
