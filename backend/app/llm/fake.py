"""Фейковая модель без сети и ключей: для тестов, CI и запуска без .env."""

import re
from collections.abc import AsyncIterator

from app.llm.base import LLMResponse, Message, Tier, ToolSpec


class FakeLLMClient:
    """Отдаёт заготовленные ответы по очереди, а когда они кончились, повторяет последнее сообщение пользователя."""

    def __init__(self, responses: list[LLMResponse] | None = None) -> None:
        self._responses = list(responses or [])
        self.calls: list[list[Message]] = []

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
        self.calls.append(messages)
        if self._responses:
            return self._responses.pop(0)
        return LLMResponse(text=_echo(messages), model=f"fake-{tier}")

    async def stream(
        self,
        messages: list[Message],
        *,
        tier: Tier = "main",
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        self.calls.append(messages)
        text = self._responses.pop(0).text if self._responses else _echo(messages)
        for word in re.findall(r"\S+\s*", text or ""):
            yield word

    async def aclose(self) -> None:
        pass


def _echo(messages: list[Message]) -> str:
    last = next((m.content for m in reversed(messages) if m.role == "user" and m.content), "")
    return f"Я пока пустой агент и умею только повторять: {last}"
