from __future__ import annotations

import asyncio
import sqlite3

from pathlib import Path
from uuid import UUID

from services.agent_runtime.app.investigation.persistence_models import (
    IncidentAnalysisRecord,
)


class IncidentAnalysisStore:
    """
    SQLite-backed per-Incident analysis persistence.

    The store is deliberately separate from historical Agent Memory:
    - one row is keyed by incident_id;
    - primary RCA and Investigation can be enriched independently;
    - merge-on-write prevents a primary-only update from deleting a previously
      persisted Investigation, or vice versa;
    - no Incident/Approval/Action/Verification lifecycle state is stored here.
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
    ) -> None:
        self.db_path = Path(
            db_path
            or (
                Path("data")
                / "incident_analysis.db"
            )
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
                CREATE TABLE IF NOT EXISTS incident_analysis
                (
                    incident_id TEXT PRIMARY KEY,
                    analysis_data TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    async def get(
        self,
        incident_id: UUID | str,
    ) -> IncidentAnalysisRecord | None:
        return await asyncio.to_thread(
            self._get_sync,
            str(
                incident_id
            ),
        )

    def _get_sync(
        self,
        incident_id: str,
    ) -> IncidentAnalysisRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT analysis_data

                FROM incident_analysis

                WHERE incident_id = ?
                """,
                (
                    incident_id,
                ),
            ).fetchone()

        if row is None:
            return None

        return self._deserialize(
            row[
                0
            ]
        )

    async def upsert(
        self,
        record: IncidentAnalysisRecord,
    ) -> IncidentAnalysisRecord:
        if not isinstance(
            record,
            IncidentAnalysisRecord,
        ):
            raise TypeError(
                "Incident analysis record is invalid"
            )

        return await asyncio.to_thread(
            self._upsert_sync,
            record,
        )

    def _upsert_sync(
        self,
        record: IncidentAnalysisRecord,
    ) -> IncidentAnalysisRecord:
        connection = self._connect()

        try:
            connection.execute(
                "BEGIN IMMEDIATE"
            )

            row = connection.execute(
                """
                SELECT analysis_data

                FROM incident_analysis

                WHERE incident_id = ?
                """,
                (
                    str(
                        record.incident_id
                    ),
                ),
            ).fetchone()

            if row is None:
                merged = record

                connection.execute(
                    """
                    INSERT INTO incident_analysis
                    (
                        incident_id,
                        analysis_data,
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
                        str(
                            merged.incident_id
                        ),
                        self._serialize(
                            merged
                        ),
                        merged.created_at.isoformat(),
                        merged.updated_at.isoformat(),
                    ),
                )

            else:
                current = self._deserialize(
                    row[
                        0
                    ]
                )

                merged = self._merge(
                    current=current,
                    incoming=record,
                )

                connection.execute(
                    """
                    UPDATE incident_analysis

                    SET
                        analysis_data = ?,
                        updated_at = ?

                    WHERE incident_id = ?
                    """,
                    (
                        self._serialize(
                            merged
                        ),
                        merged.updated_at.isoformat(),
                        str(
                            merged.incident_id
                        ),
                    ),
                )

            connection.commit()

            return merged

        except Exception:
            connection.rollback()
            raise

        finally:
            connection.close()

    async def list_all(
        self,
    ) -> list[IncidentAnalysisRecord]:
        return await asyncio.to_thread(
            self._list_all_sync
        )

    def _list_all_sync(
        self,
    ) -> list[IncidentAnalysisRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT analysis_data

                FROM incident_analysis

                ORDER BY created_at ASC, incident_id ASC
                """
            ).fetchall()

        return [
            self._deserialize(
                row[
                    0
                ]
            )
            for row in rows
        ]

    @staticmethod
    def _merge(
        *,
        current: IncidentAnalysisRecord,
        incoming: IncidentAnalysisRecord,
    ) -> IncidentAnalysisRecord:
        if (
            current.incident_id
            != incoming.incident_id
        ):
            raise ValueError(
                "Incident analysis identity mismatch"
            )

        primary = (
            incoming.primary_rca
            if (
                incoming.primary_rca
                is not None
                and (
                    current.primary_rca
                    is None
                    or (
                        incoming.primary_rca
                        .recorded_at
                        >= current.primary_rca
                        .recorded_at
                    )
                )
            )
            else current.primary_rca
        )

        investigation = (
            incoming.investigation
            if (
                incoming.investigation
                is not None
                and (
                    current.investigation
                    is None
                    or (
                        incoming.investigation
                        .updated_at
                        >= current.investigation
                        .updated_at
                    )
                )
            )
            else current.investigation
        )

        return IncidentAnalysisRecord(
            incident_id=(
                current.incident_id
            ),
            request_id=(
                incoming.request_id
                or current.request_id
            ),
            scope=incoming.scope,
            primary_rca=primary,
            investigation=investigation,
            created_at=current.created_at,
            updated_at=max(
                current.updated_at,
                incoming.updated_at,
            ),
        )

    @staticmethod
    def _serialize(
        record: IncidentAnalysisRecord,
    ) -> str:
        return record.model_dump_json()

    @staticmethod
    def _deserialize(
        value: str,
    ) -> IncidentAnalysisRecord:
        return (
            IncidentAnalysisRecord
            .model_validate_json(
                value
            )
        )


__all__ = [
    "IncidentAnalysisStore",
]
