from __future__ import annotations

import asyncio
import sqlite3

from datetime import UTC, datetime
from pathlib import Path

from services.agent_runtime.app.conversation.models import (
    ConversationIntent,
    ConversationSession,
)


class InMemoryConversationSessionStore:
    """
    Bounded process-local conversation routing state.

    Incident/Evidence/RCA data remains in existing authoritative persistence.
    """

    def __init__(self, *, max_sessions: int = 10000) -> None:
        if (
            not isinstance(max_sessions, int)
            or isinstance(max_sessions, bool)
            or max_sessions <= 0
            or max_sessions > 100000
        ):
            raise ValueError(
                "Conversation max_sessions is invalid"
            )

        self.max_sessions = max_sessions
        self._items: dict[str, ConversationSession] = {}
        self._lock = asyncio.Lock()

    async def get(
        self,
        conversation_id: str,
    ) -> ConversationSession | None:
        async with self._lock:
            return self._items.get(conversation_id)

    async def update(
        self,
        *,
        conversation_id: str,
        incident_id: str | None,
        intent: ConversationIntent,
    ) -> ConversationSession:
        async with self._lock:
            current = self._items.get(conversation_id)
            now = datetime.now(UTC)

            if current is None:
                if len(self._items) >= self.max_sessions:
                    oldest_key = min(
                        self._items,
                        key=lambda key: self._items[key].updated_at,
                    )
                    self._items.pop(oldest_key, None)

                value = ConversationSession(
                    conversation_id=conversation_id,
                    incident_id=incident_id,
                    last_intent=intent,
                    turn_count=1,
                    created_at=now,
                    updated_at=now,
                )
            else:
                value = current.model_copy(
                    update={
                        "incident_id": (
                            incident_id
                            if incident_id is not None
                            else current.incident_id
                        ),
                        "last_intent": intent,
                        "turn_count": current.turn_count + 1,
                        "updated_at": now,
                    }
                )

            self._items[conversation_id] = value
            return value



class SQLiteConversationSessionStore:
    """
    Durable ChatOps conversation -> Incident binding.

    Only routing/session state is stored here:
    - opaque conversation binding key,
    - currently-bound incident_id,
    - last deterministic intent,
    - turn count and timestamps.

    Incident/RCA/Approval/Action/Verification facts remain in their existing
    authoritative stores.
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        max_sessions: int = 10000,
    ) -> None:
        if (
            not isinstance(
                max_sessions,
                int,
            )
            or isinstance(
                max_sessions,
                bool,
            )
            or max_sessions <= 0
            or max_sessions > 100000
        ):
            raise ValueError(
                "Conversation max_sessions is invalid"
            )

        self.db_path = Path(
            db_path
            or (
                Path("data")
                / "conversation_sessions.db"
            )
        )

        self.max_sessions = (
            max_sessions
        )

        self.db_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        self._init_db()

    def _connect(
        self,
    ) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.db_path,
            timeout=10.0,
        )

        connection.execute(
            "PRAGMA busy_timeout = 10000"
        )

        return connection

    def _init_db(
        self,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "PRAGMA journal_mode = WAL"
            )

            connection.execute(
                "PRAGMA synchronous = FULL"
            )

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_sessions
                (
                    conversation_id TEXT PRIMARY KEY,
                    session_data TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_conversation_sessions_updated_at
                ON conversation_sessions(updated_at)
                """
            )

    async def get(
        self,
        conversation_id: str,
    ) -> ConversationSession | None:
        normalized_id = (
            self._conversation_id(
                conversation_id
            )
        )

        return await asyncio.to_thread(
            self._get_sync,
            normalized_id,
        )

    def _get_sync(
        self,
        conversation_id: str,
    ) -> ConversationSession | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT session_data

                FROM conversation_sessions

                WHERE conversation_id = ?
                """,
                (
                    conversation_id,
                ),
            ).fetchone()

        if row is None:
            return None

        session = (
            ConversationSession
            .model_validate_json(
                row[
                    0
                ]
            )
        )

        if (
            session.conversation_id
            != conversation_id
        ):
            raise ValueError(
                "Conversation session identity mismatch"
            )

        return session

    async def update(
        self,
        *,
        conversation_id: str,
        incident_id: str | None,
        intent: ConversationIntent,
    ) -> ConversationSession:
        normalized_id = (
            self._conversation_id(
                conversation_id
            )
        )

        normalized_incident_id = (
            self._incident_id(
                incident_id
            )
        )

        if not isinstance(
            intent,
            ConversationIntent,
        ):
            raise TypeError(
                "Conversation intent is invalid"
            )

        return await asyncio.to_thread(
            self._update_sync,
            normalized_id,
            normalized_incident_id,
            intent,
        )

    def _update_sync(
        self,
        conversation_id: str,
        incident_id: str | None,
        intent: ConversationIntent,
    ) -> ConversationSession:
        connection = self._connect()

        try:
            connection.execute(
                "BEGIN IMMEDIATE"
            )

            row = connection.execute(
                """
                SELECT session_data

                FROM conversation_sessions

                WHERE conversation_id = ?
                """,
                (
                    conversation_id,
                ),
            ).fetchone()

            now = datetime.now(
                UTC
            )

            if row is None:
                value = ConversationSession(
                    conversation_id=(
                        conversation_id
                    ),
                    incident_id=(
                        incident_id
                    ),
                    last_intent=intent,
                    turn_count=1,
                    created_at=now,
                    updated_at=now,
                )

                connection.execute(
                    """
                    INSERT INTO conversation_sessions
                    (
                        conversation_id,
                        session_data,
                        created_at,
                        updated_at
                    )

                    VALUES
                    (
                        ?,
                        ?,
                        ?,
                        ?
                    )
                    """,
                    (
                        conversation_id,
                        value.model_dump_json(),
                        value.created_at.isoformat(),
                        value.updated_at.isoformat(),
                    ),
                )

            else:
                current = (
                    ConversationSession
                    .model_validate_json(
                        row[
                            0
                        ]
                    )
                )

                if (
                    current.conversation_id
                    != conversation_id
                ):
                    raise ValueError(
                        "Conversation session identity mismatch"
                    )

                value = current.model_copy(
                    update={
                        "incident_id": (
                            incident_id
                            if incident_id
                            is not None
                            else current.incident_id
                        ),
                        "last_intent": intent,
                        "turn_count": (
                            current.turn_count
                            + 1
                        ),
                        "updated_at": now,
                    }
                )

                connection.execute(
                    """
                    UPDATE conversation_sessions

                    SET
                        session_data = ?,
                        updated_at = ?

                    WHERE conversation_id = ?
                    """,
                    (
                        value.model_dump_json(),
                        value.updated_at.isoformat(),
                        conversation_id,
                    ),
                )

            self._prune_sync(
                connection
            )

            connection.commit()

            return value

        except Exception:
            connection.rollback()
            raise

        finally:
            connection.close()

    def _prune_sync(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        row = connection.execute(
            """
            SELECT COUNT(*)

            FROM conversation_sessions
            """
        ).fetchone()

        count = int(
            row[
                0
            ]
        )

        overflow = (
            count
            - self.max_sessions
        )

        if overflow <= 0:
            return

        connection.execute(
            """
            DELETE FROM conversation_sessions

            WHERE conversation_id IN
            (
                SELECT conversation_id

                FROM conversation_sessions

                ORDER BY
                    updated_at ASC,
                    conversation_id ASC

                LIMIT ?
            )
            """,
            (
                overflow,
            ),
        )

    @staticmethod
    def _conversation_id(
        value: str,
    ) -> str:
        if (
            not isinstance(
                value,
                str,
            )
            or not value
            or value != value.strip()
            or len(
                value
            )
            > 256
            or "\x00" in value
        ):
            raise ValueError(
                "conversation_id is invalid"
            )

        return value

    @staticmethod
    def _incident_id(
        value: str | None,
    ) -> str | None:
        if value is None:
            return None

        if (
            not isinstance(
                value,
                str,
            )
            or not value
            or value != value.strip()
            or len(
                value
            )
            > 256
            or "\x00" in value
        ):
            raise ValueError(
                "incident_id is invalid"
            )

        return value


__all__ = [
    "InMemoryConversationSessionStore",
    "SQLiteConversationSessionStore",
]
