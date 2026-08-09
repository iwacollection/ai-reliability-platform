import asyncio
import sqlite3

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from services.agent_runtime.app.verification.models import (
    VerificationResult,
    VerificationStatus,
)


class VerificationConflictError(
    RuntimeError
):
    """
    Verification state or ownership conflicts with persisted state.
    """


@dataclass(frozen=True)
class VerificationClaimResult:
    """Result of atomically claiming one Action Execution verification."""

    verification: VerificationResult

    created: bool


class VerificationStore:
    """
    SQLite persistence for verification results.

    Supports:
    - cross-instance persistence
    - lookup by Incident
    - exactly one verification claim per Action Execution
    - compare-and-set status updates
    - automatic migration of legacy SQLite data
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
    ) -> None:
        self.db_path = (
            Path(db_path)
            if db_path is not None
            else Path("data") / "verifications.db"
        )

        self.db_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        self._initialize_database()

    def _connect(
        self,
    ) -> sqlite3.Connection:
        connection = sqlite3.connect(
            str(self.db_path),
            timeout=10.0,
        )

        connection.execute(
            "PRAGMA busy_timeout = 10000"
        )

        return connection

    def _initialize_database(
        self,
    ) -> None:
        connection = self._connect()

        try:
            connection.execute(
                "PRAGMA journal_mode = WAL"
            )

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS verification_results (
                    id TEXT PRIMARY KEY,
                    incident_id TEXT NOT NULL,
                    action_execution_id TEXT,
                    status TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    verification_data TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

            columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(verification_results)"
                ).fetchall()
            }

            if "action_execution_id" not in columns:
                connection.execute(
                    """
                    ALTER TABLE verification_results
                    ADD COLUMN action_execution_id TEXT
                    """
                )

            self._backfill_action_execution_links(
                connection
            )
            self._require_unique_action_links(
                connection
            )

            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_verification_results_incident_id
                ON verification_results (
                    incident_id
                )
                """
            )

            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_verification_results_status
                ON verification_results (
                    status
                )
                """
            )

            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_verification_results_incident_attempt
                ON verification_results (
                    incident_id,
                    attempt
                )
                """
            )

            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS
                uq_verification_results_action_execution_id
                ON verification_results (
                    action_execution_id
                )
                WHERE action_execution_id IS NOT NULL
                """
            )

            connection.commit()

        except Exception:
            connection.rollback()
            raise

        finally:
            connection.close()

    def _backfill_action_execution_links(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        """
        Upgrade links stored in JSON or legacy metadata into the SQL column.

        The JSON payload is rewritten at the same time so future reads expose
        the structured action_execution_id field.
        """

        rows = connection.execute(
            """
            SELECT id,
                   action_execution_id,
                   verification_data
            FROM verification_results
            """
        ).fetchall()

        for (
            verification_id,
            stored_link,
            payload,
        ) in rows:
            result = self._deserialize(
                payload
            )
            metadata_link = result.metadata.get(
                "action_execution_id"
            )

            candidates = [
                value
                for value in (
                    result.action_execution_id,
                    stored_link,
                    metadata_link,
                )
                if value is not None
            ]

            if not candidates:
                continue

            normalized = {
                self._normalize_action_execution_id(
                    value,
                    verification_id=(
                        verification_id
                    ),
                )
                for value in candidates
            }

            if len(normalized) != 1:
                raise VerificationConflictError(
                    "Verification contains conflicting "
                    "Action Execution links: "
                    f"{verification_id}"
                )

            action_execution_id = normalized.pop()

            if (
                result.action_execution_id
                != UUID(action_execution_id)
            ):
                result.action_execution_id = UUID(
                    action_execution_id
                )

            connection.execute(
                """
                UPDATE verification_results
                SET action_execution_id = ?,
                    verification_data = ?
                WHERE id = ?
                """,
                (
                    action_execution_id,
                    result.model_dump_json(),
                    verification_id,
                ),
            )

    @staticmethod
    def _require_unique_action_links(
        connection: sqlite3.Connection,
    ) -> None:
        duplicate = connection.execute(
            """
            SELECT action_execution_id,
                   COUNT(*)
            FROM verification_results
            WHERE action_execution_id IS NOT NULL
            GROUP BY action_execution_id
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        ).fetchone()

        if duplicate is not None:
            raise VerificationConflictError(
                "Multiple Verification results reference "
                "the same Action Execution: "
                f"{duplicate[0]}"
            )

    @staticmethod
    def _normalize_action_execution_id(
        value: object,
        *,
        verification_id: object,
    ) -> str:
        try:
            return str(
                UUID(
                    str(value)
                )
            )
        except (TypeError, ValueError) as exc:
            raise VerificationConflictError(
                "Verification contains an invalid "
                "Action Execution id: "
                f"{verification_id}"
            ) from exc

    @staticmethod
    def _status_value(
        status: VerificationStatus | str,
    ) -> str:
        if isinstance(
            status,
            VerificationStatus,
        ):
            return status.value

        return str(status)

    @staticmethod
    def _deserialize(
        payload: str,
    ) -> VerificationResult:
        return (
            VerificationResult.model_validate_json(
                payload
            )
        )

    def _normalize_result_link(
        self,
        result: VerificationResult,
    ) -> VerificationResult:
        """
        Preserve compatibility with callers that still put the link only in
        metadata while the service and coordinator migration is in progress.
        """

        normalized = result.model_copy(
            deep=True
        )
        metadata_link = normalized.metadata.get(
            "action_execution_id"
        )

        candidates = [
            value
            for value in (
                normalized.action_execution_id,
                metadata_link,
            )
            if value is not None
        ]

        if not candidates:
            return normalized

        normalized_ids = {
            self._normalize_action_execution_id(
                value,
                verification_id=normalized.id,
            )
            for value in candidates
        }

        if len(normalized_ids) != 1:
            raise VerificationConflictError(
                "Verification contains conflicting "
                "Action Execution links: "
                f"{normalized.id}"
            )

        normalized.action_execution_id = UUID(
            normalized_ids.pop()
        )

        return normalized

    @staticmethod
    def _action_execution_value(
        result: VerificationResult,
    ) -> str | None:
        if result.action_execution_id is None:
            return None

        return str(
            result.action_execution_id
        )

    async def save(
        self,
        result: VerificationResult,
    ) -> VerificationResult:
        return await asyncio.to_thread(
            self._save,
            result,
        )

    def _save(
        self,
        result: VerificationResult,
    ) -> VerificationResult:
        normalized = self._normalize_result_link(
            result
        )
        payload = normalized.model_dump_json()

        connection = self._connect()

        try:
            connection.execute(
                """
                INSERT INTO verification_results (
                    id,
                    incident_id,
                    action_execution_id,
                    status,
                    attempt,
                    verification_data,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(normalized.id),
                    str(normalized.incident_id),
                    self._action_execution_value(
                        normalized
                    ),
                    self._status_value(
                        normalized.status
                    ),
                    normalized.attempt,
                    payload,
                    normalized.created_at.isoformat(),
                    normalized.updated_at.isoformat(),
                ),
            )

            connection.commit()

        except sqlite3.IntegrityError as error:
            connection.rollback()

            raise VerificationConflictError(
                "Verification result or Action "
                "Execution link already exists"
            ) from error

        finally:
            connection.close()

        return self._deserialize(
            payload
        )

    async def claim(
        self,
        result: VerificationResult,
    ) -> VerificationClaimResult:
        """
        Atomically create or replay one Action Execution verification claim.

        Only the caller receiving created=True may start probes. An existing
        claim is returned only when all immutable request fields match.
        """

        return await asyncio.to_thread(
            self._claim,
            result,
        )

    def _claim(
        self,
        result: VerificationResult,
    ) -> VerificationClaimResult:
        normalized = self._normalize_result_link(
            result
        )

        if normalized.action_execution_id is None:
            raise ValueError(
                "Verification claim requires "
                "action_execution_id"
            )

        if (
            normalized.status
            != VerificationStatus.PENDING
        ):
            raise ValueError(
                "Verification claim must be PENDING"
            )

        action_execution_id = str(
            normalized.action_execution_id
        )
        payload = normalized.model_dump_json()
        connection = self._connect()

        try:
            connection.execute(
                "BEGIN IMMEDIATE"
            )

            row = connection.execute(
                """
                SELECT verification_data
                FROM verification_results
                WHERE action_execution_id = ?
                """,
                (
                    action_execution_id,
                ),
            ).fetchone()

            if row is not None:
                current = self._deserialize(
                    row[0]
                )

                if not self._same_claim(
                    current,
                    normalized,
                ):
                    raise VerificationConflictError(
                        "Action Execution is already linked "
                        "to a different Verification claim"
                    )

                connection.commit()

                return VerificationClaimResult(
                    verification=current,
                    created=False,
                )

            connection.execute(
                """
                INSERT INTO verification_results (
                    id,
                    incident_id,
                    action_execution_id,
                    status,
                    attempt,
                    verification_data,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(normalized.id),
                    str(normalized.incident_id),
                    action_execution_id,
                    self._status_value(
                        normalized.status
                    ),
                    normalized.attempt,
                    payload,
                    normalized.created_at.isoformat(),
                    normalized.updated_at.isoformat(),
                ),
            )

            connection.commit()

            return VerificationClaimResult(
                verification=self._deserialize(
                    payload
                ),
                created=True,
            )

        except sqlite3.IntegrityError as error:
            connection.rollback()

            raise VerificationConflictError(
                "Verification claim conflicts with "
                "persisted state"
            ) from error

        except Exception:
            connection.rollback()
            raise

        finally:
            connection.close()

    @staticmethod
    def _same_claim(
        current: VerificationResult,
        requested: VerificationResult,
    ) -> bool:
        return (
            current.incident_id
            == requested.incident_id
            and current.action_execution_id
            == requested.action_execution_id
            and current.action == requested.action
            and current.target == requested.target
            and current.attempt == requested.attempt
            and current.metadata
            == requested.metadata
        )

    async def get(
        self,
        verification_id: UUID | str,
    ) -> VerificationResult | None:
        return await asyncio.to_thread(
            self._get,
            verification_id,
        )

    def _get(
        self,
        verification_id: UUID | str,
    ) -> VerificationResult | None:
        connection = self._connect()

        try:
            row = connection.execute(
                """
                SELECT verification_data
                FROM verification_results
                WHERE id = ?
                """,
                (
                    str(verification_id),
                ),
            ).fetchone()

        finally:
            connection.close()

        if row is None:
            return None

        return self._deserialize(
            row[0]
        )

    async def get_by_action_execution(
        self,
        action_execution_id: UUID | str,
    ) -> VerificationResult | None:
        return await asyncio.to_thread(
            self._get_by_action_execution,
            action_execution_id,
        )

    def _get_by_action_execution(
        self,
        action_execution_id: UUID | str,
    ) -> VerificationResult | None:
        normalized_id = self._normalize_action_execution_id(
            action_execution_id,
            verification_id="lookup",
        )
        connection = self._connect()

        try:
            row = connection.execute(
                """
                SELECT verification_data
                FROM verification_results
                WHERE action_execution_id = ?
                """,
                (
                    normalized_id,
                ),
            ).fetchone()

        finally:
            connection.close()

        if row is None:
            return None

        return self._deserialize(
            row[0]
        )

    async def update(
        self,
        result: VerificationResult,
        expected_status: (
            VerificationStatus | str | None
        ) = None,
    ) -> VerificationResult:
        return await asyncio.to_thread(
            self._update,
            result,
            expected_status,
        )

    def _update(
        self,
        result: VerificationResult,
        expected_status: (
            VerificationStatus | str | None
        ),
    ) -> VerificationResult:
        normalized = self._normalize_result_link(
            result
        )
        payload = normalized.model_dump_json()
        action_execution_id = (
            self._action_execution_value(
                normalized
            )
        )
        connection = self._connect()

        try:
            connection.execute(
                "BEGIN IMMEDIATE"
            )

            current = connection.execute(
                """
                SELECT status,
                       action_execution_id
                FROM verification_results
                WHERE id = ?
                """,
                (
                    str(normalized.id),
                ),
            ).fetchone()

            if current is None:
                raise ValueError(
                    "Verification result not found"
                )

            if current[1] != action_execution_id:
                raise VerificationConflictError(
                    "Verification Action Execution "
                    "link is immutable"
                )

            if (
                expected_status is not None
                and current[0]
                != self._status_value(
                    expected_status
                )
            ):
                raise VerificationConflictError(
                    "Verification status conflict: "
                    f"current status is {current[0]}"
                )

            connection.execute(
                """
                UPDATE verification_results
                SET incident_id = ?,
                    action_execution_id = ?,
                    status = ?,
                    attempt = ?,
                    verification_data = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    str(normalized.incident_id),
                    action_execution_id,
                    self._status_value(
                        normalized.status
                    ),
                    normalized.attempt,
                    payload,
                    normalized.updated_at.isoformat(),
                    str(normalized.id),
                ),
            )

            connection.commit()

        except sqlite3.IntegrityError as error:
            connection.rollback()

            raise VerificationConflictError(
                "Verification update conflicts with "
                "an Action Execution link"
            ) from error

        except Exception:
            connection.rollback()
            raise

        finally:
            connection.close()

        return self._deserialize(
            payload
        )

    async def list_by_incident(
        self,
        incident_id: UUID | str,
    ) -> list[VerificationResult]:
        return await asyncio.to_thread(
            self._list_by_incident,
            incident_id,
        )

    def _list_by_incident(
        self,
        incident_id: UUID | str,
    ) -> list[VerificationResult]:
        connection = self._connect()

        try:
            rows = connection.execute(
                """
                SELECT verification_data
                FROM verification_results
                WHERE incident_id = ?
                ORDER BY attempt, created_at, id
                """,
                (
                    str(incident_id),
                ),
            ).fetchall()

        finally:
            connection.close()

        return [
            self._deserialize(
                row[0]
            )
            for row in rows
        ]

    async def list_all(
        self,
    ) -> list[VerificationResult]:
        return await asyncio.to_thread(
            self._list_all
        )

    def _list_all(
        self,
    ) -> list[VerificationResult]:
        connection = self._connect()

        try:
            rows = connection.execute(
                """
                SELECT verification_data
                FROM verification_results
                ORDER BY created_at, id
                """
            ).fetchall()

        finally:
            connection.close()

        return [
            self._deserialize(
                row[0]
            )
            for row in rows
        ]
