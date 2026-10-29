"""Process-local, bounded replay of accepted chat turns.

A disconnected HTTP subscriber does not own the agent turn. Its request ID keeps
the original input and the complete SSE result for up to 30 minutes after
completion, subject to the count/byte caps below.
The durable acceptance ledger distinguishes an unsent request from a lost reply.
After expiry or a process restart an accepted request fails closed instead of
running the same booking/tool a second time. The ledger contains only IDs, hashes
and timestamps; do not delete it while clients can still retry accepted requests.
"""

import asyncio
import hashlib
import json
import os
import sqlite3
from collections.abc import AsyncIterator, Callable
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic
from time import time as unix_time

from fastapi.sse import format_sse_event

from app.agent.core import AgentEvent

PING_INTERVAL_S = 15.0
REPLAY_TTL_S = 30 * 60.0
MAX_COMPLETED = 256
MAX_COMPLETED_BYTES = 32 * 1024 * 1024
MAX_RUNNING = 16
MAX_TURN_BYTES = 4 * 1024 * 1024
MAX_TURN_EVENTS = 10_000
TURN_TIMEOUT_S = 10 * 60.0


class ChatRecoveryError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class ChatRequestLedger:
    """Durable acceptance markers only; never stores messages or provider/account data."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def _connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(self.path, flags, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
        db = sqlite3.connect(self.path, timeout=2)
        try:
            db.execute("PRAGMA journal_mode=DELETE")
            db.execute(
                "CREATE TABLE IF NOT EXISTS accepted_chat_requests ("
                "request_id TEXT PRIMARY KEY, payload_sha256 TEXT NOT NULL, accepted_at REAL NOT NULL)"
            )
            db.commit()
            yield db
        finally:
            db.close()

    def lookup(self, request_id: str) -> str | None:
        with self._connection() as db:
            row = db.execute(
                "SELECT payload_sha256 FROM accepted_chat_requests WHERE request_id=?", (request_id,)
            ).fetchone()
        return row[0] if row else None

    def claim(self, request_id: str, fingerprint: str) -> str | None:
        """Return an existing fingerprint, or commit the first acceptance atomically."""
        with self._connection() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT payload_sha256 FROM accepted_chat_requests WHERE request_id=?", (request_id,)
                ).fetchone()
                if row is not None:
                    db.rollback()
                    return row[0]
                db.execute(
                    "INSERT INTO accepted_chat_requests (request_id, payload_sha256, accepted_at) VALUES (?, ?, ?)",
                    (request_id, fingerprint, unix_time()),
                )
                db.commit()
            except BaseException:
                db.rollback()
                raise
        return None


def payload_fingerprint(message: str, session_id: str | None, *, search_only: bool = False) -> str:
    payload = {"message": message, "session_id": session_id}
    if search_only:
        payload["search_only"] = True
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def encode_event(event: AgentEvent) -> bytes:
    # JSON escaping keeps user-provided newlines inside the single SSE data field.
    return format_sse_event(event=event.type, data_str=json.dumps(event.data, ensure_ascii=False))


def error_event(message: str) -> bytes:
    return encode_event(AgentEvent(type="error", data={"message": message}))


@dataclass
class ChatTurn:
    message: str
    session_id: str | None
    events: list[bytes] = field(default_factory=list)
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None
    completed_at: float | None = None
    size: int = 0
    search_only: bool = False

    def append(self, event: bytes) -> None:
        self.events.append(event)
        self.size += len(event)
        self.notify()

    def notify(self) -> None:
        # Rotate the signal instead of clearing a shared Event. A slow subscriber
        # must not clear a notification that another subscriber has not consumed.
        changed, self.changed = self.changed, asyncio.Event()
        changed.set()

    async def stream(self) -> AsyncIterator[bytes]:
        """Each subscriber starts at event zero; disconnecting only closes this iterator."""
        cursor = 0
        while True:
            changed = self.changed
            while cursor < len(self.events):
                event = self.events[cursor]
                cursor += 1
                yield event
            if self.completed_at is not None:
                return
            try:
                await asyncio.wait_for(changed.wait(), timeout=PING_INTERVAL_S)
            except TimeoutError:
                yield b": ping\n\n"


class ChatRecovery:
    def __init__(
        self,
        *,
        ttl_s: float = REPLAY_TTL_S,
        max_completed: int = MAX_COMPLETED,
        max_completed_bytes: int = MAX_COMPLETED_BYTES,
        max_running: int = MAX_RUNNING,
        max_turn_bytes: int = MAX_TURN_BYTES,
        max_turn_events: int = MAX_TURN_EVENTS,
        timeout_s: float = TURN_TIMEOUT_S,
        clock: Callable[[], float] = monotonic,
        ledger_path: Path | None = None,
    ) -> None:
        self._ttl_s = ttl_s
        self._max_completed = max_completed
        self._max_completed_bytes = max_completed_bytes
        self._max_running = max_running
        self._max_turn_bytes = max_turn_bytes
        self._max_turn_events = max_turn_events
        self._timeout_s = timeout_s
        self._clock = clock
        self._turns: dict[str, ChatTurn] = {}
        self._closed = False
        self._ledger = ChatRequestLedger(ledger_path) if ledger_path is not None else None

    def get_or_start(
        self,
        request_id: str,
        message: str,
        session_id: str | None,
        run: Callable[[], AsyncIterator[AgentEvent]],
        *,
        retry: bool = False,
        session_exists: bool = True,
        search_only: bool = False,
    ) -> ChatTurn:
        """Accept synchronously, before response headers and without an await race."""
        self._prune()
        if self._closed:
            raise ChatRecoveryError(503, "Сервер перезапускается. Повторите подключение позже.")
        existing = self._turns.get(request_id)
        if existing is not None:
            if (existing.message, existing.session_id, existing.search_only) != (message, session_id, search_only):
                raise ChatRecoveryError(409, "Это сообщение уже принято с другим содержимым.")
            return existing
        fingerprint = payload_fingerprint(message, session_id, search_only=search_only)
        if self._ledger is not None:
            try:
                seen = self._ledger.lookup(request_id)
            except (OSError, sqlite3.Error):
                raise self._ledger_unavailable() from None
            if seen is not None:
                self._reject_seen(seen, fingerprint)
        elif retry:
            self._reject_seen(fingerprint, fingerprint)
        if session_id is not None and not session_exists:
            raise ChatRecoveryError(
                410,
                "Сессия на сервере уже недоступна. Переписка в браузере сохранена; начните новый поиск.",
            )
        if sum(turn.completed_at is None for turn in self._turns.values()) >= self._max_running:
            raise ChatRecoveryError(503, "Агент занят. Повторите отправку чуть позже.")
        if self._ledger is not None:
            try:
                seen = self._ledger.claim(request_id, fingerprint)
            except (OSError, sqlite3.Error):
                raise self._ledger_unavailable() from None
            if seen is not None:  # Another process may have accepted it after lookup.
                self._reject_seen(seen, fingerprint)
        turn = ChatTurn(message=message, session_id=session_id, search_only=search_only)
        self._turns[request_id] = turn
        turn.task = asyncio.create_task(self._produce(turn, run), name="chat-recovery-turn")
        return turn

    @staticmethod
    def _ledger_unavailable() -> ChatRecoveryError:
        return ChatRecoveryError(503, "Не удалось безопасно принять сообщение. Повторите отправку чуть позже.")

    @staticmethod
    def _reject_seen(seen: str, fingerprint: str) -> None:
        if seen != fingerprint:
            raise ChatRecoveryError(409, "Это сообщение уже принято с другим содержимым.")
        raise ChatRecoveryError(
            410,
            "Сообщение было принято, но сохранённый ответ уже недоступен: сервер перезапустился "
            "или истёк срок восстановления. Переписка в браузере сохранена. "
            "Начните новый поиск, чтобы не повторить действие случайно.",
        )

    def _prune(self) -> None:
        now = self._clock()
        completed = sorted(
            (turn.completed_at, request_id) for request_id, turn in self._turns.items() if turn.completed_at is not None
        )
        completed_bytes = sum(self._turns[request_id].size for _, request_id in completed)
        for index, (completed_at, request_id) in enumerate(completed):
            if (
                now - completed_at >= self._ttl_s
                or index < len(completed) - self._max_completed
                or completed_bytes > self._max_completed_bytes
            ):
                completed_bytes -= self._turns[request_id].size
                del self._turns[request_id]

    async def _produce(self, turn: ChatTurn, run: Callable[[], AsyncIterator[AgentEvent]]) -> None:
        source: AsyncIterator[AgentEvent] | None = None
        terminal = False
        try:
            async with asyncio.timeout(self._timeout_s):
                source = run()
                async for event in source:
                    data = encode_event(event)
                    if turn.size + len(data) > self._max_turn_bytes or len(turn.events) >= self._max_turn_events:
                        turn.append(
                            error_event("Ответ слишком большой. Начните новый поиск с более коротким запросом.")
                        )
                        terminal = True
                        break
                    turn.append(data)
                    if event.type in {"done", "error"}:
                        terminal = True
                        break
            if not terminal:
                turn.append(error_event("Агент завершил ответ без подтверждения. Начните новый поиск."))
        except TimeoutError:
            turn.append(error_event("Агент не успел завершить запрос. Начните новый поиск или уточните условия."))
        except asyncio.CancelledError:
            turn.append(error_event("Сервер перезапускается. Переписка в браузере сохранена."))
            raise
        except Exception:
            # Do not expose provider errors, prompts, tokens or internal tracebacks.
            turn.append(error_event("Не удалось завершить запрос. Переписка в браузере сохранена."))
        finally:
            try:
                if source is not None:
                    close = getattr(source, "aclose", None)
                    if close is not None:
                        with suppress(Exception):
                            await close()
            finally:
                turn.completed_at = self._clock()
                turn.notify()
                self._prune()

    async def aclose(self) -> None:
        self._closed = True
        tasks = [turn.task for turn in self._turns.values() if turn.task is not None and not turn.task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        # A task cancelled before its first scheduling point has no finally block.
        for turn in self._turns.values():
            if turn.completed_at is None:
                turn.append(error_event("Сервер перезапускается. Переписка в браузере сохранена."))
                turn.completed_at = self._clock()
                turn.notify()


async def legacy_stream(events: AsyncIterator[AgentEvent]) -> AsyncIterator[bytes]:
    """Keep old no-request-ID cancellation semantics and SSE heartbeat comments."""
    pending: asyncio.Task[AgentEvent] | None = None
    try:
        while True:
            pending = asyncio.create_task(anext(events))
            while True:
                done, _ = await asyncio.wait({pending}, timeout=PING_INTERVAL_S)
                if done:
                    break
                yield b": ping\n\n"
            try:
                event = pending.result()
            except StopAsyncIteration:
                return
            yield encode_event(event)
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        close = getattr(events, "aclose", None)
        if close is not None:
            await close()
