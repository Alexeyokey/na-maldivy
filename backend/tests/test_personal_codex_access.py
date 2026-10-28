"""Personal account access stays behind a trusted proxy and server-only token."""

import httpx
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.llm import CodexLocalClient, create_llm_client
from app.main import create_app

TOKEN = "synthetic-private-token-1234567890-test-only"


def test_codex_configuration_requires_personal_launcher() -> None:
    with pytest.raises(ValidationError, match="run_personal_codex"):
        Settings(_env_file=None, llm_provider="codex_local")
    settings = Settings(_env_file=None, llm_provider="codex_local", codex_local_token=TOKEN)
    assert isinstance(create_llm_client(settings), CodexLocalClient)
    assert TOKEN not in repr(settings)


@pytest.mark.parametrize("token", [None, "too-short"])
def test_private_codex_configuration_requires_server_nonce(token: str | None) -> None:
    with pytest.raises(ValidationError, match="CODEX_LOCAL_TOKEN"):
        Settings(_env_file=None, llm_provider="codex_private", codex_local_token=token)


def test_private_codex_uses_only_fast_model() -> None:
    settings = Settings(_env_file=None, llm_provider="codex_private", codex_local_token=TOKEN)
    assert settings.llm_model_main == settings.llm_model_fast == "gpt-6-luna"
    assert isinstance(create_llm_client(settings), CodexLocalClient)
    assert TOKEN not in repr(settings)
    with pytest.raises(ValidationError, match="gpt-6-luna"):
        Settings(_env_file=None, llm_provider="codex_private", codex_local_token=TOKEN, llm_model_fast="other-model")


@pytest.mark.parametrize(
    ("peer", "token", "status"),
    [
        ("127.0.0.1", None, 403),
        ("127.0.0.1", "wrong", 403),
        ("198.51.100.1", TOKEN, 403),
        ("127.0.0.1", TOKEN, 200),
        ("::1", TOKEN, 200),
    ],
)
async def test_backend_requires_loopback_and_token(peer: str, token: str | None, status: int) -> None:
    app = create_app(Settings(_env_file=None, llm_provider="codex_local", codex_local_token=TOKEN))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=(peer, 50000)), base_url="http://127.0.0.1"
    ) as client:
        response = await client.get("/health", headers={"x-local-codex-token": token} if token else {})
    assert response.status_code == status
    assert TOKEN not in response.text
    if status == 200:
        assert response.json() == {
            "status": "ok",
            "llm_provider": "codex_local",
            "codex_completed_calls": 0,
            "codex_model": "CLI-default",
            "codex_reasoning_effort": "low",
        }


@pytest.mark.parametrize("token,status", [(None, 403), ("wrong", 403), (TOKEN, 200)])
async def test_private_backend_requires_nonce_from_internal_proxy(token: str | None, status: int) -> None:
    app = create_app(Settings(_env_file=None, llm_provider="codex_private", codex_local_token=TOKEN))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("172.23.0.3", 50000)), base_url="http://agent:8000"
    ) as client:
        response = await client.get("/health", headers={"x-local-codex-token": token} if token else {})
        assert response.status_code == status
        assert TOKEN not in response.text
        if status == 200:
            assert response.json() == {
                "status": "ok",
                "llm_provider": "codex_private",
                "codex_completed_calls": 0,
                "codex_model": "gpt-6-luna",
                "codex_reasoning_effort": "low",
            }
        else:
            chat = await client.post("/api/chat", json={"message": "Do not call the model"})
            assert chat.status_code == 403


@pytest.mark.parametrize("provider", ["codex_local", "codex_private"])
async def test_cors_preflight_does_not_bypass_personal_nonce(provider: str) -> None:
    app = create_app(Settings(_env_file=None, llm_provider=provider, codex_local_token=TOKEN))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 50000)), base_url="http://agent:8000"
    ) as client:
        response = await client.options(
            "/api/chat", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"}
        )
    assert response.status_code == 403


async def test_forwarded_headers_cannot_bypass_local_peer_boundary() -> None:
    app = create_app(Settings(_env_file=None, llm_provider="codex_local", codex_local_token=TOKEN))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("198.51.100.1", 50000)), base_url="http://127.0.0.1"
    ) as client:
        response = await client.get("/health", headers={"x-local-codex-token": TOKEN, "X-Forwarded-For": "127.0.0.1"})
    assert response.status_code == 403


async def test_default_fake_provider_still_allows_health_without_personal_nonce() -> None:
    app = create_app(Settings(_env_file=None))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent:8000") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "llm_provider": "fake"}
