from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from uuid import UUID

from services.agent_runtime.app.investigation.engine_shadow_evaluation_models import (
    InvestigationEngineShadowEvaluationCreateResult,
    InvestigationEngineShadowEvaluationSnapshot,
)


class InvestigationEngineShadowEvaluationConflictError(RuntimeError):
    """Durable Shadow evaluation identity conflicts with stored evidence."""


class InvestigationEngineShadowEvaluationStore:
    """SQLite ledger for immutable, bounded Shadow evaluation snapshots."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        if self.db_path.suffix.lower() != ".db" or "\x00" in str(self.db_path):
            raise ValueError("Shadow evaluation database path is invalid")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            self.db_path,
            timeout=30.0,
        )
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS investigation_shadow_evaluations
                (
                    evaluation_id TEXT PRIMARY KEY,
                    incident_id TEXT NOT NULL,
                    source_window_digest TEXT NOT NULL,
                    assessment_digest TEXT NOT NULL UNIQUE,
                    snapshot_data TEXT NOT NULL,
                    generated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS
                    idx_investigation_shadow_evaluations_incident_time
                ON investigation_shadow_evaluations
                    (incident_id, generated_at DESC, evaluation_id DESC)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS
                    idx_investigation_shadow_evaluations_source_window
                ON investigation_shadow_evaluations
                    (source_window_digest, generated_at DESC)
                """
            )

    async def create_or_get(
        self,
        snapshot: InvestigationEngineShadowEvaluationSnapshot,
    ) -> InvestigationEngineShadowEvaluationCreateResult:
        if not isinstance(snapshot, InvestigationEngineShadowEvaluationSnapshot):
            raise TypeError("Shadow evaluation snapshot is invalid")
        candidate = InvestigationEngineShadowEvaluationSnapshot.model_validate(
            snapshot.model_dump(mode="python")
        )
        return await asyncio.to_thread(self._create_or_get_sync, candidate)

    def _create_or_get_sync(
        self,
        snapshot: InvestigationEngineShadowEvaluationSnapshot,
    ) -> InvestigationEngineShadowEvaluationCreateResult:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT snapshot_data
                FROM investigation_shadow_evaluations
                WHERE assessment_digest = ?
                """,
                (snapshot.assessment_digest,),
            ).fetchone()
            if row is not None:
                current = self._deserialize(row[0])
                if (
                    current.evaluation_id != snapshot.evaluation_id
                    or current.incident_id != snapshot.incident_id
                    or current.source_window_digest
                    != snapshot.source_window_digest
                    or current.policy_digest != snapshot.policy_digest
                    or current.matrix_digest != snapshot.matrix_digest
                    or current.release_digest != snapshot.release_digest
                ):
                    raise InvestigationEngineShadowEvaluationConflictError(
                        "Shadow evaluation replay conflicts with durable evidence"
                    )
                return InvestigationEngineShadowEvaluationCreateResult(
                    snapshot=current,
                    created=False,
                )

            collision = connection.execute(
                """
                SELECT assessment_digest
                FROM investigation_shadow_evaluations
                WHERE evaluation_id = ?
                """,
                (str(snapshot.evaluation_id),),
            ).fetchone()
            if collision is not None:
                raise InvestigationEngineShadowEvaluationConflictError(
                    "Shadow evaluation identity collision"
                )

            connection.execute(
                """
                INSERT INTO investigation_shadow_evaluations
                (
                    evaluation_id,
                    incident_id,
                    source_window_digest,
                    assessment_digest,
                    snapshot_data,
                    generated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(snapshot.evaluation_id),
                    str(snapshot.incident_id),
                    snapshot.source_window_digest,
                    snapshot.assessment_digest,
                    self._serialize(snapshot),
                    snapshot.generated_at.isoformat(),
                ),
            )
            return InvestigationEngineShadowEvaluationCreateResult(
                snapshot=snapshot,
                created=True,
            )

    async def get(
        self,
        evaluation_id: UUID | str,
    ) -> InvestigationEngineShadowEvaluationSnapshot | None:
        normalized = str(UUID(str(evaluation_id)))
        return await asyncio.to_thread(self._get_sync, normalized)

    def _get_sync(
        self,
        evaluation_id: str,
    ) -> InvestigationEngineShadowEvaluationSnapshot | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT snapshot_data
                FROM investigation_shadow_evaluations
                WHERE evaluation_id = ?
                """,
                (evaluation_id,),
            ).fetchone()
        return self._deserialize(row[0]) if row is not None else None

    async def list_recent_by_incident(
        self,
        incident_id: UUID | str,
        *,
        limit: int = 20,
    ) -> list[InvestigationEngineShadowEvaluationSnapshot]:
        normalized = str(UUID(str(incident_id)))
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or limit < 1
            or limit > 100
        ):
            raise ValueError("Shadow evaluation query limit is invalid")
        return await asyncio.to_thread(
            self._list_recent_by_incident_sync,
            normalized,
            limit,
        )

    def _list_recent_by_incident_sync(
        self,
        incident_id: str,
        limit: int,
    ) -> list[InvestigationEngineShadowEvaluationSnapshot]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT snapshot_data
                FROM investigation_shadow_evaluations
                WHERE incident_id = ?
                ORDER BY generated_at DESC, evaluation_id DESC
                LIMIT ?
                """,
                (incident_id, limit),
            ).fetchall()
        return [self._deserialize(row[0]) for row in reversed(rows)]

    @staticmethod
    def _serialize(snapshot: InvestigationEngineShadowEvaluationSnapshot) -> str:
        return json.dumps(
            snapshot.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _deserialize(value: str) -> InvestigationEngineShadowEvaluationSnapshot:
        return InvestigationEngineShadowEvaluationSnapshot.model_validate_json(
            value
        )


__all__ = [
    "InvestigationEngineShadowEvaluationConflictError",
    "InvestigationEngineShadowEvaluationStore",
]
