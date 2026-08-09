from __future__ import annotations

import asyncio
import sqlite3

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from services.agent_runtime.app.incident.enums import (
    IncidentStatus,
)

from services.agent_runtime.app.incident.state import (
    IncidentState,
)


class IncidentConflictError(RuntimeError):
    """
    Raised when an IncidentState changed after it was read.
    """


class IncidentStore:
    """
    SQLite-backed incident state store.

    Guarantees:
    - Incident state survives process restarts.
    - Separate IncidentStore instances share the same data.
    - Conditional status updates prevent concurrent state overwrite.
    - SQLite work does not directly block the event loop.
    - Every operation opens and closes its own database connection.
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
    ) -> None:
        self.db_path = Path(
            db_path
            or (
                Path("data")
                / "incidents.db"
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

    def _init_db(
        self,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                "PRAGMA journal_mode = WAL"
            )

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS incidents
                (
                    id TEXT PRIMARY KEY,

                    status TEXT NOT NULL,

                    incident_data TEXT NOT NULL,

                    created_at TEXT NOT NULL,

                    updated_at TEXT NOT NULL
                )
                """
            )

            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS
                    idx_incidents_status

                ON incidents
                (
                    status
                )
                """
            )

            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS
                    idx_incidents_updated_at

                ON incidents
                (
                    updated_at
                )
                """
            )

    async def save(
        self,
        incident: IncidentState,
    ) -> IncidentState:
        return await asyncio.to_thread(
            self._save_sync,
            incident,
        )

    def _save_sync(
        self,
        incident: IncidentState,
    ) -> IncidentState:
        incident_data = self._serialize(
            incident
        )

        try:
            with self._connection() as connection:
                connection.execute(
                    """
                    INSERT INTO incidents
                    (
                        id,
                        status,
                        incident_data,
                        created_at,
                        updated_at
                    )

                    VALUES
                    (
                        ?,
                        ?,
                        ?,
                        ?,
                        ?
                    )
                    """,
                    (
                        str(
                            incident.id
                        ),
                        self._status_value(
                            incident.status
                        ),
                        incident_data,
                        incident.created_at.isoformat(),
                        incident.updated_at.isoformat(),
                    ),
                )

        except sqlite3.IntegrityError as exc:
            raise IncidentConflictError(
                "Incident already exists: "
                f"{incident.id}"
            ) from exc

        return self._deserialize(
            incident_data
        )

    async def get(
        self,
        incident_id: str,
    ) -> IncidentState | None:
        return await asyncio.to_thread(
            self._get_sync,
            incident_id,
        )

    def _get_sync(
        self,
        incident_id: str,
    ) -> IncidentState | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT incident_data

                FROM incidents

                WHERE id = ?
                """,
                (
                    str(
                        incident_id
                    ),
                ),
            ).fetchone()

        if row is None:
            return None

        return self._deserialize(
            row[0]
        )

    async def update(
        self,
        incident: IncidentState,
        expected_status: IncidentStatus | None = None,
    ) -> IncidentState:
        return await asyncio.to_thread(
            self._update_sync,
            incident,
            expected_status,
        )

    def _update_sync(
        self,
        incident: IncidentState,
        expected_status: IncidentStatus | None,
    ) -> IncidentState:
        incident_data = self._serialize(
            incident
        )

        parameters: list[str] = [
            self._status_value(
                incident.status
            ),
            incident_data,
            incident.updated_at.isoformat(),
            str(
                incident.id
            ),
        ]

        statement = """
            UPDATE incidents

            SET
                status = ?,
                incident_data = ?,
                updated_at = ?

            WHERE id = ?
        """

        if expected_status is not None:
            statement += """
                AND status = ?
            """

            parameters.append(
                self._status_value(
                    expected_status
                )
            )

        with self._connection() as connection:
            cursor = connection.execute(
                statement,
                parameters,
            )

            if cursor.rowcount == 1:
                return self._deserialize(
                    incident_data
                )

            current_row = connection.execute(
                """
                SELECT status

                FROM incidents

                WHERE id = ?
                """,
                (
                    str(
                        incident.id
                    ),
                ),
            ).fetchone()

        if current_row is None:
            raise ValueError(
                "Incident not found: "
                f"{incident.id}"
            )

        if expected_status is not None:
            raise IncidentConflictError(
                "Incident status conflict for "
                f"{incident.id}: expected "
                f"{self._status_value(expected_status)}, "
                f"found {current_row[0]}"
            )

        raise IncidentConflictError(
            "Incident update conflict: "
            f"{incident.id}"
        )

    async def list_all(
        self,
    ) -> list[IncidentState]:
        return await asyncio.to_thread(
            self._list_all_sync
        )

    def _list_all_sync(
        self,
    ) -> list[IncidentState]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT incident_data

                FROM incidents

                ORDER BY created_at ASC
                """
            ).fetchall()

        return [
            self._deserialize(
                row[0]
            )
            for row in rows
        ]

    @staticmethod
    def _serialize(
        incident: IncidentState,
    ) -> str:
        return incident.model_dump_json()

    @staticmethod
    def _deserialize(
        incident_data: str,
    ) -> IncidentState:
        return IncidentState.model_validate_json(
            incident_data
        )

    @staticmethod
    def _status_value(
        status: IncidentStatus | str,
    ) -> str:
        if isinstance(
            status,
            IncidentStatus,
        ):
            return status.value

        return str(
            status
        )
