import asyncio
import json
import sqlite3
from collections.abc import AsyncIterator
from datetime import date
from typing import Any

import httpx
import pytest

from app.agent.core import AgentEvent, TravelAgent
from app.agent.session import SessionStore
from app.chat_recovery import ChatRecovery, ChatRecoveryError, legacy_stream
from app.config import Settings
from app.llm import FakeLLMClient
from app.main import create_app


class ControlledAgent:
    """One observable stateful action, blocked after the first SSE event."""

    def __init__(self) -> None:
        self.calls = 0
        self.commits = 0
        self.cancelled = False
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.sessions = SessionStore()

    async def run(self, message: str, session_id: str | None = None) -> AsyncIterator[AgentEvent]:
        self.calls += 1
        session = self.sessions.get_or_create(session_id)
        try:
            self.started.set()
            yield AgentEvent(type="status", data={"text": "Ищу"})
            await self.release.wait()
            self.commits += 1
            session.version += 1
            yield AgentEvent(type="delta", data={"text": message + "\nготово"})
            yield AgentEvent(type="done", data={"session_id": session.id, "version": session.version})
        except asyncio.CancelledError:
            self.cancelled = True
            raise


async def collect(stream: AsyncIterator[bytes]) -> bytes:
    return b"".join([chunk async for chunk in stream])


def parse_sse(body: str) -> list[tuple[str, Any]]:
    events = []
    for block in body.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if "data" in fields:
            events.append((fields.get("event", "message"), json.loads(fields["data"])))
    return events


async def test_concurrent_duplicate_subscribers_run_stateful_action_once() -> None:
    agent = ControlledAgent()
    cache = ChatRecovery()
    first = cache.get_or_start("same-id", "Запрос", None, lambda: agent.run("Запрос"))
    await agent.started.wait()
    duplicate = cache.get_or_start("same-id", "Запрос", None, lambda: agent.run("Запрос"), retry=True)
    assert duplicate is first
    left = asyncio.create_task(collect(first.stream()))
    right = asyncio.create_task(collect(duplicate.stream()))
    agent.release.set()
    assert await left == await right
    assert agent.calls == agent.commits == 1
    await cache.aclose()


async def test_subscriber_disconnect_keeps_producer_alive_and_replays_from_start() -> None:
    agent = ControlledAgent()
    cache = ChatRecovery()
    turn = cache.get_or_start("interrupted", "Привет", None, lambda: agent.run("Привет"))
    subscriber = turn.stream()
    first = await anext(subscriber)
    assert b"event: status" in first
    await subscriber.aclose()
    assert turn.task is not None and not turn.task.done()
    assert not agent.cancelled
    agent.release.set()
    await turn.task
    retry = cache.get_or_start("interrupted", "Привет", None, lambda: agent.run("Привет"), retry=True)
    replay = await collect(retry.stream())
    assert replay.startswith(first)
    events = parse_sse(replay.decode())
    assert events[-1][0] == "done"
    assert events[-1][1]["session_id"] in agent.sessions._sessions
    assert agent.calls == agent.commits == 1
    await cache.aclose()


async def test_sse_heartbeat_keeps_recoverable_producer_alive(monkeypatch: Any) -> None:
    monkeypatch.setattr("app.chat_recovery.PING_INTERVAL_S", 0.001)
    agent = ControlledAgent()
    cache = ChatRecovery()
    turn = cache.get_or_start("heartbeat", "Привет", None, lambda: agent.run("Привет"))
    subscriber = turn.stream()
    assert b"event: status" in await anext(subscriber)
    assert await anext(subscriber) == b": ping\n\n"
    assert not agent.cancelled
    agent.release.set()
    assert parse_sse((await collect(subscriber)).decode())[-1][0] == "done"
    await cache.aclose()


async def test_legacy_stream_heartbeat_and_disconnect_still_cancel_agent(monkeypatch: Any) -> None:
    monkeypatch.setattr("app.chat_recovery.PING_INTERVAL_S", 0.001)
    agent = ControlledAgent()
    stream = legacy_stream(agent.run("Привет"))
    assert b"event: status" in await anext(stream)
    assert await anext(stream) == b": ping\n\n"
    await stream.aclose()
    assert agent.cancelled and agent.commits == 0


async def test_lost_done_reuses_first_new_session_and_rejects_changed_payload() -> None:
    agent = ControlledAgent()
    agent.release.set()
    cache = ChatRecovery()
    turn = cache.get_or_start("first-message", "Привет", None, lambda: agent.run("Привет"))
    assert turn.task is not None
    await turn.task  # No subscriber received even the final done.
    original = await collect(turn.stream())
    repeated = cache.get_or_start("first-message", "Привет", None, lambda: agent.run("Привет"), retry=True)
    assert await collect(repeated.stream()) == original
    sid = parse_sse(original.decode())[-1][1]["session_id"]
    for message, session_id in (("Другое", None), ("Привет", sid)):
        with pytest.raises(ChatRecoveryError) as conflict:
            cache.get_or_start("first-message", message, session_id, lambda: agent.run("Другое"), retry=True)
        assert conflict.value.status_code == 409
    assert len(agent.sessions._sessions) == 1
    assert agent.calls == 1
    await cache.aclose()


async def test_shutdown_cancels_producer_and_finalizes_waiting_subscriber() -> None:
    agent = ControlledAgent()
    cache = ChatRecovery()
    turn = cache.get_or_start("shutdown", "Привет", None, lambda: agent.run("Привет"))
    await agent.started.wait()
    await cache.aclose()
    assert turn.task is not None and turn.task.done()
    assert agent.cancelled and agent.commits == 0
    assert parse_sse((await collect(turn.stream())).decode())[-1][0] == "error"
    with pytest.raises(ChatRecoveryError) as closed:
        cache.get_or_start("new", "Привет", None, lambda: agent.run("Привет"))
    assert closed.value.status_code == 503


async def test_shutdown_before_first_task_step_does_not_leave_subscriber_waiting() -> None:
    agent = ControlledAgent()
    cache = ChatRecovery()
    turn = cache.get_or_start("not-started", "Привет", None, lambda: agent.run("Привет"))
    await cache.aclose()
    assert turn.completed_at is not None
    assert agent.calls == 0
    assert b"event: error" in await collect(turn.stream())


async def test_shutdown_waits_for_actual_agent_child_cleanup() -> None:
    agent = TravelAgent(FakeLLMClient(), {}, llm_writes_text=False)
    started = asyncio.Event()
    cleanup_started = asyncio.Event()
    cleanup_allowed = asyncio.Event()
    cleaned = asyncio.Event()

    async def blocked_turn(session: Any, message: str, emit: Any) -> None:
        started.set()
        emit("status", {"text": "Ищу"})
        try:
            await asyncio.Event().wait()
        finally:
            cleanup_started.set()
            await cleanup_allowed.wait()
            cleaned.set()

    agent._turn = blocked_turn
    cache = ChatRecovery()
    cache.get_or_start("real-agent-shutdown", "Привет", None, lambda: agent.run("Привет"))
    await started.wait()
    closing = asyncio.create_task(cache.aclose())
    try:
        await cleanup_started.wait()
        await asyncio.sleep(0)
        assert not closing.done(), "Shutdown must wait for provider/subprocess cleanup, not just send cancellation"
    finally:
        cleanup_allowed.set()
        await closing
    assert cleaned.is_set()


async def test_expired_or_evicted_retry_never_reruns_and_running_turn_is_not_evicted() -> None:
    now = [0.0]
    cache = ChatRecovery(ttl_s=5, max_completed=1, max_running=2, clock=lambda: now[0])
    agent = ControlledAgent()
    running = cache.get_or_start("running", "run", None, lambda: agent.run("run"))
    await agent.started.wait()

    async def completed() -> AsyncIterator[AgentEvent]:
        yield AgentEvent(type="done", data={"session_id": "completed"})

    for request_id in ("complete1", "complete2"):
        turn = cache.get_or_start(request_id, "done", None, completed)
        assert turn.task is not None
        await turn.task
    assert cache.get_or_start("running", "run", None, completed, retry=True) is running
    with pytest.raises(ChatRecoveryError) as evicted:
        cache.get_or_start("complete1", "done", None, completed, retry=True)
    assert evicted.value.status_code == 410
    now[0] = 6.0
    with pytest.raises(ChatRecoveryError) as expired:
        cache.get_or_start("complete2", "done", None, completed, retry=True)
    assert expired.value.status_code == 410
    assert cache.get_or_start("running", "run", None, completed, retry=True) is running
    with pytest.raises(ChatRecoveryError) as restarted:
        ChatRecovery().get_or_start("complete2", "done", None, completed, retry=True)
    assert restarted.value.status_code == 410
    await cache.aclose()


async def test_cache_aggregate_bytes_and_active_turn_limits() -> None:
    cache = ChatRecovery(max_completed_bytes=200, max_running=1)

    async def complete() -> AsyncIterator[AgentEvent]:
        yield AgentEvent(type="delta", data={"text": "x" * 80})
        yield AgentEvent(type="done")

    for request_id in ("one", "two"):
        turn = cache.get_or_start(request_id, "done", None, complete)
        assert turn.task is not None
        await turn.task
    assert sum(turn.size for turn in cache._turns.values()) <= 200
    with pytest.raises(ChatRecoveryError) as evicted:
        cache.get_or_start("one", "done", None, complete, retry=True)
    assert evicted.value.status_code == 410
    agent = ControlledAgent()
    cache.get_or_start("blocked", "run", None, lambda: agent.run("run"))
    with pytest.raises(ChatRecoveryError) as busy:
        cache.get_or_start("other", "run", None, lambda: agent.run("run"))
    assert busy.value.status_code == 503
    await cache.aclose()


async def test_durable_ledger_allows_never_received_retry_once_and_stores_no_message(tmp_path: Any) -> None:
    path = tmp_path / "chat-requests.sqlite3"
    cache = ChatRecovery(ledger_path=path)
    agent = ControlledAgent()
    agent.release.set()
    # The initial browser fetch never reached this process, so no acceptance exists.
    turn = cache.get_or_start(
        "offline-before-send",
        "Личный запрос без сохранения текста",
        None,
        lambda: agent.run("Личный запрос без сохранения текста"),
        retry=True,
    )
    assert turn.task is not None
    await turn.task
    replay = cache.get_or_start(
        "offline-before-send",
        "Личный запрос без сохранения текста",
        None,
        lambda: agent.run("Личный запрос без сохранения текста"),
        retry=True,
    )
    assert await collect(replay.stream()) == await collect(turn.stream())
    assert agent.calls == agent.commits == 1
    assert path.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(path) as db:
        assert [row[1] for row in db.execute("PRAGMA table_info(accepted_chat_requests)")] == [
            "request_id",
            "payload_sha256",
            "accepted_at",
        ]
        row = db.execute("SELECT * FROM accepted_chat_requests").fetchone()
    assert row[0] == "offline-before-send" and len(row[1]) == 64 and row[2] > 0
    assert "Личный запрос".encode() not in path.read_bytes()
    await cache.aclose()


async def test_durable_ledger_restart_and_eviction_reject_seen_without_rerun(tmp_path: Any) -> None:
    path = tmp_path / "chat-requests.sqlite3"
    agent = ControlledAgent()
    agent.release.set()
    first_process = ChatRecovery(ledger_path=path, max_completed=0)
    turn = first_process.get_or_start("accepted-before-restart", "Привет", None, lambda: agent.run("Привет"))
    assert turn.task is not None
    await turn.task
    for registry in (first_process, ChatRecovery(ledger_path=path)):
        for retry in (False, True):
            with pytest.raises(ChatRecoveryError) as missing:
                registry.get_or_start(
                    "accepted-before-restart", "Привет", None, lambda: agent.run("Привет"), retry=retry
                )
            assert missing.value.status_code == 410
        with pytest.raises(ChatRecoveryError) as conflict:
            registry.get_or_start(
                "accepted-before-restart", "Изменено", None, lambda: agent.run("Изменено"), retry=True
            )
        assert conflict.value.status_code == 409
        await registry.aclose()
    assert agent.calls == agent.commits == 1


async def test_durable_ledger_database_failure_never_starts_agent(tmp_path: Any, monkeypatch: Any) -> None:
    agent = ControlledAgent()
    path = tmp_path / "database-is-a-directory"
    path.mkdir()
    broken = ChatRecovery(ledger_path=path)
    with pytest.raises(ChatRecoveryError) as unavailable:
        broken.get_or_start("unaccepted", "Привет", None, lambda: agent.run("Привет"), retry=True)
    assert unavailable.value.status_code == 503
    assert not broken._turns and agent.calls == 0

    cache = ChatRecovery(ledger_path=tmp_path / "claim-failure.sqlite3")

    def fail_claim(*args: Any) -> Any:
        raise sqlite3.OperationalError("private database error must never reach HTTP")

    monkeypatch.setattr(cache._ledger, "claim", fail_claim)
    with pytest.raises(ChatRecoveryError) as unavailable:
        cache.get_or_start("unaccepted", "Привет", None, lambda: agent.run("Привет"))
    assert unavailable.value.status_code == 503
    assert "private database error" not in unavailable.value.detail
    assert not cache._turns and agent.calls == 0


async def test_durable_ledger_atomic_claim_race_does_not_start_second_producer(tmp_path: Any, monkeypatch: Any) -> None:
    path = tmp_path / "chat-requests.sqlite3"
    first = ChatRecovery(ledger_path=path)
    second = ChatRecovery(ledger_path=path)
    agent = ControlledAgent()
    lookup = first._ledger.lookup

    def race(request_id: str) -> Any:
        result = lookup(request_id)
        second.get_or_start(request_id, "Привет", None, lambda: agent.run("Привет"))
        return result

    monkeypatch.setattr(first._ledger, "lookup", race)
    with pytest.raises(ChatRecoveryError) as accepted_elsewhere:
        first.get_or_start("claim-race", "Привет", None, lambda: agent.run("Привет"), retry=True)
    assert accepted_elsewhere.value.status_code == 410
    assert not first._turns
    await agent.started.wait()
    assert agent.calls == 1
    await first.aclose()
    await second.aclose()


async def test_timeout_and_oversized_output_are_terminal_and_replay_without_rerun() -> None:
    agent = ControlledAgent()
    cache = ChatRecovery(timeout_s=0.01)
    turn = cache.get_or_start("timeout", "run", None, lambda: agent.run("run"))
    assert turn.task is not None
    await turn.task
    assert agent.cancelled
    assert parse_sse((await collect(turn.stream())).decode())[-1][0] == "error"
    assert cache.get_or_start("timeout", "run", None, lambda: agent.run("run"), retry=True) is turn
    assert agent.calls == 1
    await cache.aclose()

    async def huge() -> AsyncIterator[AgentEvent]:
        yield AgentEvent(type="delta", data={"text": "x" * 1000})
        raise AssertionError("oversized generator must be closed")

    cache = ChatRecovery(max_turn_bytes=100)
    turn = cache.get_or_start("huge", "run", None, huge)
    assert turn.task is not None
    await turn.task
    assert [name for name, _ in parse_sse((await collect(turn.stream())).decode())] == ["error"]
    await cache.aclose()


async def test_http_conflicts_expired_sessions_validation_and_no_id_legacy(tmp_path: Any) -> None:
    app = create_app(Settings(_env_file=None, checkout_db_path=tmp_path / "checkout.sqlite3"))
    async with app.router.lifespan_context(app):
        agent = ControlledAgent()
        agent.release.set()
        app.state.agent = agent
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            payload = {"message": "Привет", "request_id": "http-same", "session_id": None}
            first = await client.post("/api/chat", json=payload)
            second = await client.post("/api/chat", json={**payload, "retry": True})
            assert first.status_code == second.status_code == 200
            assert first.content == second.content
            assert first.headers["cache-control"] == "no-store"
            assert first.headers["x-accel-buffering"] == "no"
            assert "Привет\\nготово" in first.text
            conflict = await client.post("/api/chat", json={**payload, "message": "Изменено"})
            assert conflict.status_code == 409
            assert conflict.headers["content-type"].startswith("application/json")
            never_received = await client.post("/api/chat", json={**payload, "request_id": "gone", "retry": True})
            assert never_received.status_code == 200
            missing_session = await client.post(
                "/api/chat", json={**payload, "request_id": "new-id", "session_id": "unknown"}
            )
            assert missing_session.status_code == 410
            assert agent.calls == 2
            for request_id in ("", "x" * 65, "bad space", "line\nbreak"):
                response = await client.post("/api/chat", json={**payload, "request_id": request_id})
                assert response.status_code == 422
            assert (await client.post("/api/chat", json={"message": "Привет", "retry": True})).status_code == 422
            for _ in range(2):
                legacy = await client.post("/api/chat", json={"message": "Привет"})
                assert parse_sse(legacy.text)[-1][0] == "done"
            assert agent.calls == agent.commits == 4


async def test_real_http_disconnect_reconnect_does_not_cancel_or_repeat_agent(tmp_path: Any) -> None:
    app = create_app(Settings(_env_file=None, checkout_db_path=tmp_path / "checkout.sqlite3"))
    async with app.router.lifespan_context(app):
        agent = ControlledAgent()
        app.state.agent = agent
        disconnected = asyncio.Event()
        received_body = False
        payload = {"message": "Первый запрос", "request_id": "http-interrupted", "session_id": None}
        request_body = json.dumps(payload).encode()
        response_chunks = []

        async def receive() -> dict[str, Any]:
            nonlocal received_body
            if not received_body:
                received_body = True
                return {"type": "http.request", "body": request_body, "more_body": False}
            await disconnected.wait()
            return {"type": "http.disconnect"}

        async def send(event: dict[str, Any]) -> None:
            if event["type"] == "http.response.body" and event.get("body"):
                response_chunks.append(event["body"])
                disconnected.set()  # The first SSE event reached the browser, then its socket died.

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/chat",
            "raw_path": b"/api/chat",
            "query_string": b"",
            "root_path": "",
            "headers": [(b"content-type", b"application/json")],
            "client": ("127.0.0.1", 12345),
            "server": ("test", 80),
        }
        await asyncio.wait_for(app(scope, receive, send), timeout=2)
        assert b"event: status" in b"".join(response_chunks)
        assert not agent.cancelled
        assert agent.commits == 0
        agent.release.set()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            resumed = await client.post("/api/chat", json={**payload, "retry": True})
        events = parse_sse(resumed.text)
        assert [name for name, _ in events] == ["status", "delta", "done"]
        assert agent.calls == agent.commits == 1


async def test_actual_cart_confirmation_replay_does_not_run_agent_or_create_cart_twice(tmp_path: Any) -> None:
    app = create_app(Settings(_env_file=None, checkout_db_path=tmp_path / "checkout.sqlite3"))
    async with app.router.lifespan_context(app):
        app.state.agent._today = lambda: date(2026, 10, 28)
        checkout = app.state.checkout
        original_create_cart = checkout.create_cart
        cart_calls = []

        def tracked_cart(session: Any, **kwargs: Any) -> Any:
            cart_calls.append(session.id)
            return original_create_cart(session, **kwargs)

        checkout.create_cart = tracked_cart
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            planned = parse_sse(
                (
                    await client.post(
                        "/api/chat",
                        json={
                            "message": "из Москвы в Дубай 18 ноября 2026 на неделю вдвоём",
                            "request_id": "cart-search",
                        },
                    )
                ).text
            )
            session_id = planned[-1][1]["session_id"]
            options = next(data["items"] for kind, data in planned if kind == "options")
            option = next(row for row in options if row["can_confirm"])
            selected = parse_sse(
                (
                    await client.post(
                        "/api/chat",
                        json={
                            "message": f"беру {option['index']} вариант",
                            "session_id": session_id,
                            "request_id": "cart-select",
                        },
                    )
                ).text
            )
            assert selected[-1][1]["stage"] == "awaiting_confirmation"
            payload = {"message": "да, оформляй", "session_id": session_id, "request_id": "cart-confirm"}
            first = await client.post("/api/chat", json=payload)
            repeated = await client.post("/api/chat", json={**payload, "retry": True})
        assert first.content == repeated.content
        assert next(data for kind, data in parse_sse(first.text) if kind == "booking")["status"] == "awaiting_travelers"
        assert cart_calls == [session_id]
        session = app.state.agent.sessions.get(session_id)
        assert sum(message.role == "user" and message.content == "да, оформляй" for message in session.history) == 1
