from __future__ import annotations

import asyncio
import sqlite3

from pathlib import Path
from uuid import UUID

from services.agent_runtime.app.approval.models import (
    ApprovalRequest,
    ApprovalStatus,
)


class ApprovalConflictError(RuntimeError):
    """
    Raised when an ApprovalRequest changed after it was read.
    """


class ApprovalStore:
    """
    SQLite-backed approval request store.

    Guarantees:
    - Approval requests survive process restarts.
    - Separate ApprovalStore instances share the same data.
    - Conditional status updates prevent concurrent decision overwrite.
    - Every operation uses an independent SQLite connection.
    - Incident queries use the persisted incident_id index.

    Future:
    - PostgreSQL transaction and row lock
    - Redis transaction or Lua script
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
    ) -> None:
        self.db_path = Path(
            db_path
            or (
                Path("data")
                / "approvals.db"
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
                """
                CREATE TABLE IF NOT EXISTS approval_requests
                (
                    id TEXT PRIMARY KEY,

                    incident_id TEXT,

                    status TEXT NOT NULL,

                    request_data TEXT NOT NULL,

                    created_at TEXT NOT NULL,

                    updated_at TEXT NOT NULL
                )
                """
            )

            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS
                    idx_approval_requests_incident_id

                ON approval_requests
                (
                    incident_id
                )
                """
            )

            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS
                    idx_approval_requests_status

                ON approval_requests
                (
                    status
                )
                """
            )

    async def save(
        self,
        request: ApprovalRequest,
    ) -> ApprovalRequest:
        return await asyncio.to_thread(
            self._save_sync,
            request,
        )

    def _save_sync(
        self,
        request: ApprovalRequest,
    ) -> ApprovalRequest:
        request_data = self._serialize(
            request
        )

        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO approval_requests
                    (
                        id,
                        incident_id,
                        status,
                        request_data,
                        created_at,
                        updated_at
                    )

                    VALUES
                    (
                        ?,
                        ?,
                        ?,
                        ?,
                        ?,
                        ?
                    )
                    """,
                    (
                        request.id,
                        self._incident_id_value(
                            request
                        ),
                        self._status_value(
                            request.status
                        ),
                        request_data,
                        request.created_at.isoformat(),
                        request.updated_at.isoformat(),
                    ),
                )

        except sqlite3.IntegrityError as exc:
            raise ApprovalConflictError(
                "Approval request already exists: "
                f"{request.id}"
            ) from exc

        return self._deserialize(
            request_data
        )

    async def get(
        self,
        request_id: str,
    ) -> ApprovalRequest | None:
        return await asyncio.to_thread(
            self._get_sync,
            request_id,
        )

    def _get_sync(
        self,
        request_id: str,
    ) -> ApprovalRequest | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT request_data

                FROM approval_requests

                WHERE id = ?
                """,
                (
                    request_id,
                ),
            ).fetchone()

        if row is None:
            return None

        return self._deserialize(
            row[0]
        )

    async def update(
        self,
        request: ApprovalRequest,
        expected_status: ApprovalStatus | None = None,
    ) -> ApprovalRequest:
        return await asyncio.to_thread(
            self._update_sync,
            request,
            expected_status,
        )

    def _update_sync(
        self,
        request: ApprovalRequest,
        expected_status: ApprovalStatus | None,
    ) -> ApprovalRequest:
        request_data = self._serialize(
            request
        )

        parameters: list[str | None] = [
            self._incident_id_value(
                request
            ),
            self._status_value(
                request.status
            ),
            request_data,
            request.updated_at.isoformat(),
            request.id,
        ]

        statement = """
            UPDATE approval_requests

            SET
                incident_id = ?,
                status = ?,
                request_data = ?,
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

        with self._connect() as connection:
            cursor = connection.execute(
                statement,
                parameters,
            )

            if cursor.rowcount == 1:
                return self._deserialize(
                    request_data
                )

            current_row = connection.execute(
                """
                SELECT status

                FROM approval_requests

                WHERE id = ?
                """,
                (
                    request.id,
                ),
            ).fetchone()

        if current_row is None:
            raise ValueError(
                "Approval request not found: "
                f"{request.id}"
            )

        if expected_status is not None:
            raise ApprovalConflictError(
                "Approval status conflict for "
                f"{request.id}: expected "
                f"{self._status_value(expected_status)}, "
                f"found {current_row[0]}"
            )

        raise ApprovalConflictError(
            "Approval request update conflict: "
            f"{request.id}"
        )

    async def list_by_incident(
        self,
        incident_id: UUID | str,
    ) -> list[ApprovalRequest]:
        """
        Return every approval attempt linked to one Incident.

        Multiple remediation attempts are valid. The stable chronological
        order lets higher layers select the latest attempt without hiding the
        earlier workflow history.
        """

        return await asyncio.to_thread(
            self._list_by_incident_sync,
            incident_id,
        )

    def _list_by_incident_sync(
        self,
        incident_id: UUID | str,
    ) -> list[ApprovalRequest]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT request_data

                FROM approval_requests

                WHERE incident_id = ?

                ORDER BY created_at ASC, id ASC
                """,
                (
                    str(
                        incident_id
                    ),
                ),
            ).fetchall()

        return [
            self._deserialize(
                row[0]
            )
            for row in rows
        ]

    async def list_all(
        self,
    ) -> list[ApprovalRequest]:
        return await asyncio.to_thread(
            self._list_all_sync
        )

    def _list_all_sync(
        self,
    ) -> list[ApprovalRequest]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT request_data

                FROM approval_requests

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
        request: ApprovalRequest,
    ) -> str:
        return request.model_dump_json()

    @staticmethod
    def _deserialize(
        request_data: str,
    ) -> ApprovalRequest:
        return ApprovalRequest.model_validate_json(
            request_data
        )

    @staticmethod
    def _status_value(
        status: ApprovalStatus | str,
    ) -> str:
        if isinstance(
            status,
            ApprovalStatus,
        ):
            return status.value

        return str(
            status
        )

    @staticmethod
    def _incident_id_value(
        request: ApprovalRequest,
    ) -> str | None:
        if request.incident_id is None:
            return None

        return str(
            request.incident_id
        )
