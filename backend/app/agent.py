"""Пустой агент: один вызов модели без инструментов. Граф этапов и роутер появятся в A2 (#5)."""

import logging
from collections.abc import AsyncIterator
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.llm import LLMClient, LLMError, Message

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "Ты помощник по путешествиям «О!дин запрос». Отвечай по-русски и коротко. "
    "Инструментов поиска у тебя пока нет, поэтому не придумывай рейсы, отели и цены."
)


class AgentEvent(BaseModel):
    """Событие для интерфейса. Формат временный, до контрактов O1 (#3)."""

    type: Literal["delta", "done", "error"]
    data: dict[str, Any] = Field(default_factory=dict)


class EmptyAgent:
    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    async def run(self, message: str) -> AsyncIterator[AgentEvent]:
        messages = [Message(role="system", content=SYSTEM_PROMPT), Message(role="user", content=message)]
        try:
            async for text in self._llm.stream(messages):
                yield AgentEvent(type="delta", data={"text": text})
        except LLMError:
            log.exception("Модель не ответила")
            yield AgentEvent(type="error", data={"message": "Модель сейчас не отвечает, попробуйте ещё раз."})
            return
        yield AgentEvent(type="done")
