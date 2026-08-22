from __future__ import annotations

import asyncio
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator
from uuid import UUID

from services.agent_runtime.app.investigation.session_models import (
    InvestigationSessionRecord,
    InvestigationSessionStatus,
    InvestigationStepRecord,
    InvestigationStepStatus,
)


class InvestigationSessionConflictError(RuntimeError):
    """
    A durable Session changed or conflicts with an idempotency identity.
    """


@dataclass(frozen=True)
class InvestigationSessionCreateResult:
    session: InvestigationSessionRecord
    created: bool

    @property
    def replayed(self) -> bool:
        return not self.created


class InvestigationSessionStore:
    """
    SQLite persistence for bounded, replay-safe Investigation Sessions.

    Guarantees:
    - incident_id + run_key is unique across Store instances;
    - exact create replay returns the persisted Session;
    - conflicting immutable input fails closed;
    - versioned compare-and-swap permits one concurrent transition;
    - the step ledger is append-only and a claimed step can be completed once;
    - terminal and indeterminate Sessions cannot automatically advance;
    - every operation opens and closes its own SQLite connection.

    This Store owns no LLM, Probe, Incident, Approval, Action, Verification,
    budget or Kubernetes capability.
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
    ) -> None:
        self.db_path = Path(
            db_path
            or (
                Path("data")
                / "investigation_sessions.db"
            )
        )
        self.db_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        self._init_db()

    @contextmanager
    def _connection(
        self,
    ) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            self.db_path,
            timeout=10.0,
        )
        connection.execute(
            "PRAGMA busy_timeout = 10000"
        )
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _init_db(self) -> None:
        with self._connection() as connection:
            connection.execute(
                "PRAGMA journal_mode = WAL"
            )
            connection.execute(
                "PRAGMA synchronous = FULL"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS investigation_sessions
                (
                    session_id TEXT PRIMARY KEY,
                    incident_id TEXT NOT NULL,
                    run_key TEXT NOT NULL,
                    input_digest TEXT NOT NULL,
                    status TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    session_data TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE
                    (
                        incident_id,
                        run_key
                    )
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS
                    idx_investigation_sessions_incident
                ON investigation_sessions
                (
                    incident_id,
                    created_at,
                    session_id
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS
                    idx_investigation_sessions_status
                ON investigation_sessions
                (
                    status,
                    updated_at
                )
                """
            )

    async def create_or_get(
        self,
        session: InvestigationSessionRecord,
    ) -> InvestigationSessionCreateResult:
        if not isinstance(
            session,
            InvestigationSessionRecord,
        ):
            raise TypeError(
                "Investigation Session is invalid"
            )
        session = InvestigationSessionRecord.model_validate(
            session.model_dump(
                mode="python"
            )
        )
        if (
            session.version != 0
            or session.status
            != InvestigationSessionStatus.READY
        ):
            raise ValueError(
                "Investigation Session creation requires version zero Ready state"
            )

        return await asyncio.to_thread(
            self._create_or_get_sync,
            session,
        )

    def _create_or_get_sync(
        self,
        session: InvestigationSessionRecord,
    ) -> InvestigationSessionCreateResult:
        with self._connection() as connection:
            connection.execute(
                "BEGIN IMMEDIATE"
            )
            row = connection.execute(
                """
                SELECT session_data
                FROM investigation_sessions
                WHERE incident_id = ? AND run_key = ?
                """,
                (
                    str(
                        session.incident_id
                    ),
                    session.run_key,
                ),
            ).fetchone()

            if row is not None:
                current = self._deserialize(
                    row[0]
                )
                self._assert_create_replay(
                    current=current,
                    incoming=session,
                )
                return InvestigationSessionCreateResult(
                    session=current,
                    created=False,
                )

            collision = connection.execute(
                """
                SELECT session_data
                FROM investigation_sessions
                WHERE session_id = ?
                """,
                (
                    str(
                        session.session_id
                    ),
                ),
            ).fetchone()
            if collision is not None:
                raise InvestigationSessionConflictError(
                    "Investigation Session identity collision"
                )

            connection.execute(
                """
                INSERT INTO investigation_sessions
                (
                    session_id,
                    incident_id,
                    run_key,
                    input_digest,
                    status,
                    version,
                    session_data,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(
                        session.session_id
                    ),
                    str(
                        session.incident_id
                    ),
                    session.run_key,
                    session.input_digest,
                    session.status.value,
                    session.version,
                    self._serialize(
                        session
                    ),
                    session.created_at.isoformat(),
                    session.updated_at.isoformat(),
                ),
            )

        return InvestigationSessionCreateResult(
            session=self._deserialize(
                self._serialize(
                    session
                )
            ),
            created=True,
        )

    async def get(
        self,
        session_id: UUID | str,
    ) -> InvestigationSessionRecord | None:
        normalized = str(
            UUID(
                str(
                    session_id
                )
            )
        )
        return await asyncio.to_thread(
            self._get_sync,
            normalized,
        )

    def _get_sync(
        self,
        session_id: str,
    ) -> InvestigationSessionRecord | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT session_data
                FROM investigation_sessions
                WHERE session_id = ?
                """,
                (
                    session_id,
                ),
            ).fetchone()
        if row is None:
            return None
        session = self._deserialize(
            row[0]
        )
        if str(session.session_id) != session_id:
            raise ValueError(
                "Investigation Session identity mismatch"
            )
        return session

    async def get_by_run(
        self,
        *,
        incident_id: UUID | str,
        run_key: str,
    ) -> InvestigationSessionRecord | None:
        normalized_incident_id = str(
            UUID(
                str(
                    incident_id
                )
            )
        )
        if (
            not isinstance(run_key, str)
            or not run_key
            or run_key != run_key.strip()
            or len(run_key) > 256
            or "\x00" in run_key
        ):
            raise ValueError(
                "Investigation Session run_key is invalid"
            )
        return await asyncio.to_thread(
            self._get_by_run_sync,
            normalized_incident_id,
            run_key,
        )

    def _get_by_run_sync(
        self,
        incident_id: str,
        run_key: str,
    ) -> InvestigationSessionRecord | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT session_data
                FROM investigation_sessions
                WHERE incident_id = ? AND run_key = ?
                """,
                (
                    incident_id,
                    run_key,
                ),
            ).fetchone()
        if row is None:
            return None
        return self._deserialize(
            row[0]
        )

    async def list_by_incident(
        self,
        incident_id: UUID | str,
    ) -> list[InvestigationSessionRecord]:
        normalized = str(
            UUID(
                str(
                    incident_id
                )
            )
        )
        return await asyncio.to_thread(
            self._list_by_incident_sync,
            normalized,
        )

    def _list_by_incident_sync(
        self,
        incident_id: str,
    ) -> list[InvestigationSessionRecord]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT session_data
                FROM investigation_sessions
                WHERE incident_id = ?
                ORDER BY created_at ASC, session_id ASC
                """,
                (
                    incident_id,
                ),
            ).fetchall()
        return [
            self._deserialize(
                row[0]
            )
            for row in rows
        ]

    async def list_recent_by_incident(
        self,
        incident_id: UUID | str,
        *,
        limit: int = 20,
    ) -> list[InvestigationSessionRecord]:
        """Read only the newest bounded window, returned oldest to newest."""

        normalized = str(
            UUID(
                str(
                    incident_id
                )
            )
        )
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or limit < 1
            or limit > 101
        ):
            raise ValueError(
                "Investigation Session query limit is invalid"
            )
        return await asyncio.to_thread(
            self._list_recent_by_incident_sync,
            normalized,
            limit,
        )

    def _list_recent_by_incident_sync(
        self,
        incident_id: str,
        limit: int,
    ) -> list[InvestigationSessionRecord]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT session_data
                FROM investigation_sessions
                WHERE incident_id = ?
                ORDER BY created_at DESC, session_id DESC
                LIMIT ?
                """,
                (
                    incident_id,
                    limit,
                ),
            ).fetchall()
        newest_first = [
            self._deserialize(
                row[0]
            )
            for row in rows
        ]
        return list(
            reversed(
                newest_first
            )
        )

    async def compare_and_swap(
        self,
        session: InvestigationSessionRecord,
        *,
        expected_version: int,
    ) -> InvestigationSessionRecord:
        if not isinstance(
            session,
            InvestigationSessionRecord,
        ):
            raise TypeError(
                "Investigation Session is invalid"
            )
        session = InvestigationSessionRecord.model_validate(
            session.model_dump(
                mode="python"
            )
        )
        if (
            not isinstance(expected_version, int)
            or isinstance(expected_version, bool)
            or expected_version < 0
        ):
            raise ValueError(
                "Investigation Session expected_version is invalid"
            )
        if session.version != expected_version + 1:
            raise ValueError(
                "Investigation Session CAS requires exactly one version increment"
            )

        return await asyncio.to_thread(
            self._compare_and_swap_sync,
            session,
            expected_version,
        )

    def _compare_and_swap_sync(
        self,
        session: InvestigationSessionRecord,
        expected_version: int,
    ) -> InvestigationSessionRecord:
        with self._connection() as connection:
            connection.execute(
                "BEGIN IMMEDIATE"
            )
            row = connection.execute(
                """
                SELECT session_data
                FROM investigation_sessions
                WHERE session_id = ?
                """,
                (
                    str(
                        session.session_id
                    ),
                ),
            ).fetchone()

            if row is None:
                raise ValueError(
                    "Investigation Session not found"
                )

            current = self._deserialize(
                row[0]
            )

            if current.version != expected_version:
                raise InvestigationSessionConflictError(
                    "Investigation Session version conflict"
                )

            self._assert_transition(
                current=current,
                incoming=session,
            )

            cursor = connection.execute(
                """
                UPDATE investigation_sessions
                SET
                    status = ?,
                    version = ?,
                    session_data = ?,
                    updated_at = ?
                WHERE
                    session_id = ?
                    AND version = ?
                    AND incident_id = ?
                    AND run_key = ?
                    AND input_digest = ?
                """,
                (
                    session.status.value,
                    session.version,
                    self._serialize(
                        session
                    ),
                    session.updated_at.isoformat(),
                    str(
                        session.session_id
                    ),
                    expected_version,
                    str(
                        session.incident_id
                    ),
                    session.run_key,
                    session.input_digest,
                ),
            )

            if cursor.rowcount != 1:
                raise InvestigationSessionConflictError(
                    "Investigation Session compare-and-swap conflict"
                )

        return self._deserialize(
            self._serialize(
                session
            )
        )

    @staticmethod
    def _assert_create_replay(
        *,
        current: InvestigationSessionRecord,
        incoming: InvestigationSessionRecord,
    ) -> None:
        if (
            current.session_id != incoming.session_id
            or current.incident_id != incoming.incident_id
            or current.run_key != incoming.run_key
            or current.input_digest != incoming.input_digest
        ):
            raise InvestigationSessionConflictError(
                "Investigation Session idempotency conflict"
            )

    @classmethod
    def _assert_transition(
        cls,
        *,
        current: InvestigationSessionRecord,
        incoming: InvestigationSessionRecord,
    ) -> None:
        if (
            current.session_id != incoming.session_id
            or current.incident_id != incoming.incident_id
            or current.run_key != incoming.run_key
            or current.input_digest != incoming.input_digest
            or current.created_at != incoming.created_at
        ):
            raise InvestigationSessionConflictError(
                "Investigation Session immutable identity changed"
            )
        if incoming.updated_at < current.updated_at:
            raise InvestigationSessionConflictError(
                "Investigation Session clock moved backwards"
            )
        if current.status in {
            InvestigationSessionStatus.COMPLETED,
            InvestigationSessionStatus.FAILED,
            InvestigationSessionStatus.INDETERMINATE,
        }:
            raise InvestigationSessionConflictError(
                "Investigation Session automatic transition is blocked"
            )

        allowed = {
            InvestigationSessionStatus.READY: {
                InvestigationSessionStatus.RUNNING,
            },
            InvestigationSessionStatus.PAUSED: {
                InvestigationSessionStatus.RUNNING,
            },
            InvestigationSessionStatus.RUNNING: {
                InvestigationSessionStatus.PAUSED,
                InvestigationSessionStatus.COMPLETED,
                InvestigationSessionStatus.FAILED,
                InvestigationSessionStatus.INDETERMINATE,
            },
        }
        if incoming.status not in allowed.get(
            current.status,
            set(),
        ):
            raise InvestigationSessionConflictError(
                "Investigation Session status transition is invalid"
            )

        current_steps = current.steps
        incoming_steps = incoming.steps

        if len(incoming_steps) == len(current_steps) + 1:
            if (
                incoming.status != InvestigationSessionStatus.RUNNING
                or incoming_steps[:-1] != current_steps
                or incoming_steps[-1].status
                != InvestigationStepStatus.CLAIMED
            ):
                raise InvestigationSessionConflictError(
                    "Investigation Session step Claim is not append-only"
                )
            return

        if (
            len(incoming_steps) == len(current_steps)
            and current_steps
            and incoming_steps[:-1] == current_steps[:-1]
        ):
            cls._assert_step_completion(
                current=current_steps[-1],
                incoming=incoming_steps[-1],
            )
            return

        raise InvestigationSessionConflictError(
            "Investigation Session step ledger changed non-atomically"
        )

    @staticmethod
    def _assert_step_completion(
        *,
        current: InvestigationStepRecord,
        incoming: InvestigationStepRecord,
    ) -> None:
        if current.status != InvestigationStepStatus.CLAIMED:
            raise InvestigationSessionConflictError(
                "Investigation step was already completed"
            )
        immutable_equal = all(
            (
                current.step_id == incoming.step_id,
                current.sequence == incoming.sequence,
                current.kind == incoming.kind,
                current.claimant == incoming.claimant,
                current.request_digest == incoming.request_digest,
                current.probe == incoming.probe,
                current.claimed_at == incoming.claimed_at,
            )
        )
        if (
            not immutable_equal
            or incoming.status
            == InvestigationStepStatus.CLAIMED
        ):
            raise InvestigationSessionConflictError(
                "Investigation step completion changed Claim identity"
            )

    @staticmethod
    def _serialize(
        session: InvestigationSessionRecord,
    ) -> str:
        return session.model_dump_json()

    @staticmethod
    def _deserialize(
        value: str,
    ) -> InvestigationSessionRecord:
        return InvestigationSessionRecord.model_validate_json(
            value
        )


__all__ = [
    "InvestigationSessionConflictError",
    "InvestigationSessionCreateResult",
    "InvestigationSessionStore",
]
