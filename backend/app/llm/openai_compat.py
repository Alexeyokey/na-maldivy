"""Клиент для любого OpenAI-совместимого API: OpenAI, OpenRouter, YandexGPT, vLLM, Ollama."""

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import openai

from app.llm.base import LLMError, LLMResponse, Message, Tier, ToolCall, ToolSpec, Usage


class OpenAICompatClient:
    """Таймаут и повторы делает SDK, любая ошибка провайдера превращается в LLMError."""

    def __init__(
        self,
        *,
        api_key: str,
        models: dict[Tier, str],
        base_url: str | None = None,
        timeout_s: float = 60.0,
        max_retries: int = 2,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._models = models
        self._client = openai.AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_s,
            max_retries=max_retries,
            http_client=http_client,
        )

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
        params = self._params(messages, tier, temperature, max_tokens)
        model = params["model"]
        if tools:
            params["tools"] = [_tool_to_openai(tool) for tool in tools]
        if json_mode:
            params["response_format"] = {"type": "json_object"}
        try:
            resp = await self._client.chat.completions.create(**params)
        except openai.OpenAIError:
            # Provider bodies can echo credentials, prompts or private request URLs.
            # This exception can reach chat warnings and application logs.
            raise LLMError(f"{model}: провайдер не ответил или вернул ошибку.") from None
        except (ValueError, TypeError, AttributeError):
            raise LLMError(f"{model}: провайдер вернул некорректный ответ.") from None
        try:
            if not resp.choices:
                raise LLMError(f"{model}: ответ без choices")
            msg = resp.choices[0].message
            usage = Usage()
            if resp.usage:
                usage = Usage(input_tokens=resp.usage.prompt_tokens, output_tokens=resp.usage.completion_tokens)
            return LLMResponse(
                text=msg.content,
                tool_calls=[_parse_tool_call(tc, model) for tc in msg.tool_calls or []],
                model=resp.model or model,
                usage=usage,
            )
        except (ValueError, TypeError, AttributeError):
            # SDKs may accept malformed fields in a 200 response. Pydantic's
            # validation error includes their raw values unless it is replaced.
            raise LLMError(f"{model}: провайдер вернул некорректный ответ.") from None

    async def stream(
        self,
        messages: list[Message],
        *,
        tier: Tier = "main",
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        params = self._params(messages, tier, temperature, max_tokens)
        try:
            async with await self._client.chat.completions.create(**params, stream=True) as chunks:
                async for chunk in chunks:
                    if chunk.choices and chunk.choices[0].delta.content:
                        if not isinstance(chunk.choices[0].delta.content, str):
                            raise LLMError(f"{params['model']}: провайдер вернул некорректный ответ.")
                        yield chunk.choices[0].delta.content
        except openai.OpenAIError:
            raise LLMError(f"{params['model']}: провайдер не ответил или вернул ошибку.") from None
        except (ValueError, TypeError, AttributeError):
            raise LLMError(f"{params['model']}: провайдер вернул некорректный ответ.") from None

    async def aclose(self) -> None:
        await self._client.close()

    def _params(
        self, messages: list[Message], tier: Tier, temperature: float | None, max_tokens: int | None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": self._models[tier],
            "messages": [_message_to_openai(m) for m in messages],
        }
        if temperature is not None:
            params["temperature"] = temperature
        if max_tokens is not None:
            params["max_tokens"] = max_tokens
        return params


def _message_to_openai(message: Message) -> dict[str, Any]:
    out: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_calls:
        out["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)},
            }
            for tc in message.tool_calls
        ]
    if message.tool_call_id:
        out["tool_call_id"] = message.tool_call_id
    return out


def _tool_to_openai(tool: ToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {"name": tool.name, "description": tool.description, "parameters": tool.parameters},
    }


def _parse_tool_call(tc: Any, model: str) -> ToolCall:
    function = getattr(tc, "function", None)
    if function is None:
        raise LLMError(f"{model}: неподдерживаемый формат вызова инструмента")
    try:
        arguments = json.loads(function.arguments or "{}")
    except json.JSONDecodeError:
        raise LLMError(f"{model}: невалидный JSON в аргументах инструмента") from None
    if not isinstance(arguments, dict):
        raise LLMError(f"{model}: аргументы инструмента должны быть объектом")
    return ToolCall(id=tc.id, name=function.name, arguments=arguments)
