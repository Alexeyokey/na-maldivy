"""Состояние диалога и хранилище сессий.

Временная замена TripState из R1 (#4): бриф, этап, показанные варианты и версия.
Хранилище в памяти процесса — для демо достаточно; Redis или БД подключаются заменой SessionStore.
"""

import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.agent.brief import Source, TripBrief
from app.llm import Message

HISTORY_WINDOW = 8  # сколько последних реплик видит модель


class Stage(StrEnum):
    COLLECTING = "collecting"
    DESTINATIONS_SHOWN = "destinations_shown"
    PACKAGES_SHOWN = "packages_shown"
    TRAINS_SHOWN = "trains_shown"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    BOOKED = "booked"


@dataclass
class Session:
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    brief: TripBrief = field(default_factory=TripBrief)
    sources: dict[str, Source] = field(default_factory=dict)
    stage: Stage = Stage.COLLECTING
    destinations: list[dict[str, Any]] = field(default_factory=list)
    packages: list[dict[str, Any]] = field(default_factory=list)
    trains: list[dict[str, Any]] = field(default_factory=list)
    # Условия поменялись после поиска: показанные варианты устарели и будут пересобраны.
    stale: bool = False
    selected: int | None = None
    booking: dict[str, Any] | None = None
    version: int = 0  # растёт при каждом изменении брифа, для диффа «было → стало» (R4)
    history: list[Message] = field(default_factory=list)

    def history_tail(self) -> list[Message]:
        return self.history[-HISTORY_WINDOW:]

    def package(self, option: int | None) -> dict[str, Any] | None:
        if option is None or not 1 <= option <= len(self.packages):
            return None
        return self.packages[option - 1]

    def snapshot(self) -> dict[str, Any]:
        """Состояние для интерфейса и отладки."""
        return {
            "session_id": self.id,
            "version": self.version,
            "stage": self.stage.value,
            "brief": self.brief.model_dump(mode="json"),
            "sources": self.sources,
            "stale": self.stale,
            "selected": self.selected,
            "booking": self.booking,
        }


class SessionStore:
    def __init__(self, max_sessions: int = 1000) -> None:
        self._sessions: dict[str, Session] = {}
        self._max = max_sessions

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def get_or_create(self, session_id: str | None) -> Session:
        if session_id and session_id in self._sessions:
            return self._sessions[session_id]
        if len(self._sessions) >= self._max:
            self._sessions.pop(next(iter(self._sessions)))  # самая старая сессия
        session = Session(id=session_id or uuid.uuid4().hex)
        self._sessions[session.id] = session
        return session

    def reset(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
