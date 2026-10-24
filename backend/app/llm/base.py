"""Типы LLM-клиента, не зависящие от провайдера."""

from collections.abc import AsyncIterator
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

Role = Literal["system", "user", "assistant", "tool"]
Tier = Literal["main", "fast"]


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any]


class Message(BaseModel):
    role: Role
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)  # у ассистента, который вызвал инструменты
    tool_call_id: str | None = None  # у ответа инструмента, role="tool"


class ToolSpec(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema аргументов


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0


class LLMResponse(BaseModel):
    text: str | None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    model: str
    usage: Usage = Field(default_factory=Usage)


class LLMError(Exception):
    """Модель не ответила: таймаут, сеть, ошибка API или ответ, который не удалось разобрать."""


class LLMClient(Protocol):
    async def complete(
        self,
        messages: list[Message],
        *,
        tier: Tier = "main",
        tools: list[ToolSpec] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> LLMResponse:
        """Ответ целиком: текст или вызовы инструментов."""
        ...

    def stream(
        self,
        messages: list[Message],
        *,
        tier: Tier = "main",
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """Текст ответа по кускам, без инструментов."""
        ...

    async def aclose(self) -> None: ...
