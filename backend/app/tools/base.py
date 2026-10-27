"""Общий интерфейс инструмента, не зависящий от источника данных."""

from typing import Protocol

from pydantic import BaseModel, TypeAdapter

from app.llm.base import ToolSpec
from app.tools.schema_refs import inline_local_refs


class Tool[Input: BaseModel, Output](Protocol):
    name: str
    description: str
    input_model: type[Input]
    output_adapter: TypeAdapter[Output]

    async def run(self, input: Input) -> Output: ...


def tool_spec[Input: BaseModel, Output](tool: Tool[Input, Output]) -> ToolSpec:
    """Схема аргументов для существующего LLMClient.complete(tools=...)."""
    return ToolSpec(
        name=tool.name,
        description=tool.description,
        parameters=inline_local_refs(tool.input_model.model_json_schema()),
    )
