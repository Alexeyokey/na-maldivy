import pytest
from pydantic import ValidationError

from app.config import Settings
from app.llm import FakeLLMClient, LLMResponse, Message, OpenAICompatClient, ToolCall, create_llm_client


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


async def test_fake_echoes_last_user_message() -> None:
    llm = FakeLLMClient()
    messages = [Message(role="system", content="s"), Message(role="user", content="Хочу на море")]

    resp = await llm.complete(messages, tier="fast")
    streamed = "".join([text async for text in llm.stream(messages)])

    assert resp.model == "fake-fast"
    assert resp.text == streamed
    assert streamed.endswith("Хочу на море")
    assert len(llm.calls) == 2


async def test_fake_returns_scripted_responses_in_order() -> None:
    call = ToolCall(id="1", name="search_hotels", arguments={"city": "Male"})
    llm = FakeLLMClient(
        [LLMResponse(text=None, tool_calls=[call], model="fake-main"), LLMResponse(text="Готово", model="fake-main")]
    )
    hello = [Message(role="user", content="Привет")]

    assert (await llm.complete(hello)).tool_calls == [call]
    assert (await llm.complete(hello)).text == "Готово"
    assert (await llm.complete(hello)).text.endswith("Привет")


def test_fake_provider_by_default() -> None:
    settings = Settings(_env_file=None)

    assert settings.llm_provider == "fake"
    assert isinstance(create_llm_client(settings), FakeLLMClient)


@pytest.mark.parametrize(
    ("kwargs", "missing"),
    [
        ({}, "LLM_API_KEY, LLM_MODEL_MAIN"),
        ({"llm_api_key": "k"}, "LLM_MODEL_MAIN"),
        ({"llm_model_main": "m"}, "LLM_API_KEY"),
    ],
)
def test_openai_provider_requires_key_and_model(kwargs: dict[str, str], missing: str) -> None:
    with pytest.raises(ValidationError, match=missing):
        Settings(_env_file=None, llm_provider="openai", **kwargs)


async def test_openai_fast_model_falls_back_to_main() -> None:
    settings = Settings(_env_file=None, llm_provider="openai", llm_api_key="k", llm_model_main="m")

    client = create_llm_client(settings)

    assert isinstance(client, OpenAICompatClient)
    assert client._models == {"main": "m", "fast": "m"}
    await client.aclose()


def test_empty_env_values_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "")
    monkeypatch.setenv("LLM_API_KEY", "")

    settings = Settings(_env_file=None)

    assert settings.llm_base_url is None
    assert settings.llm_api_key is None


def test_cors_origins_list() -> None:
    settings = Settings(_env_file=None, cors_origins="http://a, http://b,")

    assert settings.cors_origin_list == ["http://a", "http://b"]
