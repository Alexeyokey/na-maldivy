"""Personal account access must stay behind the local proxy's private token."""

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
