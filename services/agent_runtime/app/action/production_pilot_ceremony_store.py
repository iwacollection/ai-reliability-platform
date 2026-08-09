import asyncio
import sqlite3

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID

from services.agent_runtime.app.action.production_pilot_ceremony_models import (
    ProductionPilotCeremonyRecord,
    ProductionPilotCeremonyStatus,
)


class ProductionPilotCeremonyConflictError(RuntimeError):
    """The Pilot ceremony is already bound to different evidence."""


@dataclass(frozen=True, slots=True)
class ProductionPilotCeremonyClaimResult:
    record: ProductionPilotCeremonyRecord
    created: bool

    @property
    def is_replay(self) -> bool:
        return not self.created


@dataclass(frozen=True, slots=True)
class ProductionPilotCeremonyActivationResult:
    record: ProductionPilotCeremonyRecord
    applied: bool

    @property
    def is_replay(self) -> bool:
        return not self.applied


class ProductionPilotCeremonyStore:
    """SQLite CAS store for one immutable activation ceremony per Pilot."""

    def __init__(
        self,
        db_path: str | Path | None = None,
    ) -> None:
        self.db_path = Path(
            db_path
            or (
                Path("data")
                / "production_pilot_ceremonies.db"
            )
        )
        self.db_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.db_path,
            timeout=10.0,
        )
        connection.execute(
            "PRAGMA busy_timeout = 10000"
        )
        connection.execute(
            "PRAGMA synchronous = FULL"
        )
        return connection

    def _init_db(self) -> None:
        connection = self._connect()
        try:
            connection.execute(
                "PRAGMA journal_mode = WAL"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS production_pilot_ceremonies
                (
                    ceremony_id TEXT PRIMARY KEY,
                    pilot_id TEXT NOT NULL UNIQUE,
                    approval_id TEXT NOT NULL UNIQUE,
                    incident_id TEXT NOT NULL,
                    reviewer_operator_id TEXT NOT NULL,
                    executor_operator_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    execution_id TEXT,
                    status TEXT NOT NULL,
                    record_data TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                )
                """
            )
            columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(production_pilot_ceremonies)"
                ).fetchall()
            }
            if "execution_id" not in columns:
                connection.execute(
                    """
                    ALTER TABLE production_pilot_ceremonies
                    ADD COLUMN execution_id TEXT
                    """
                )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS
                    idx_production_pilot_ceremony_idempotency
                ON production_pilot_ceremonies(
                    reviewer_operator_id,
                    idempotency_key
                )
                """
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS
                    idx_production_pilot_ceremony_execution
                ON production_pilot_ceremonies(execution_id)
                WHERE execution_id IS NOT NULL
                """
            )
            connection.commit()
        finally:
            connection.close()

    async def activate(
        self,
        *,
        ceremony_id: UUID | str,
        execution_id: UUID | str,
        execution_idempotency_key: str,
        activated_at: datetime,
    ) -> ProductionPilotCeremonyActivationResult:
        return await asyncio.to_thread(
            self._activate_sync,
            str(ceremony_id),
            str(execution_id),
            execution_idempotency_key,
            activated_at,
        )

    def _activate_sync(
        self,
        ceremony_id: str,
        execution_id: str,
        execution_idempotency_key: str,
        activated_at: datetime,
    ) -> ProductionPilotCeremonyActivationResult:
        connection = self._connect()
        try:
            connection.execute(
                "BEGIN IMMEDIATE"
            )
            row = connection.execute(
                """
                SELECT status, execution_id, record_data
                FROM production_pilot_ceremonies
                WHERE ceremony_id = ?
                """,
                (ceremony_id,),
            ).fetchone()
            if row is None:
                raise ProductionPilotCeremonyConflictError(
                    "Production Pilot ceremony was not found"
                )

            execution_row = connection.execute(
                """
                SELECT ceremony_id
                FROM production_pilot_ceremonies
                WHERE execution_id = ?
                """,
                (execution_id,),
            ).fetchone()
            if (
                execution_row is not None
                and execution_row[0] != ceremony_id
            ):
                raise ProductionPilotCeremonyConflictError(
                    "Action Execution is bound to another Pilot ceremony"
                )

            current = self._deserialize(
                row[2]
            )
            if row[0] != current.status.value:
                raise ProductionPilotCeremonyConflictError(
                    "Production Pilot ceremony status index is inconsistent"
                )
            indexed_execution_id = row[1]
            record_execution_id = (
                str(current.execution_id)
                if current.execution_id is not None
                else None
            )
            if indexed_execution_id != record_execution_id:
                raise ProductionPilotCeremonyConflictError(
                    "Production Pilot ceremony execution index is inconsistent"
                )

            try:
                updated = current.activate(
                    execution_id=execution_id,
                    execution_idempotency_key=(
                        execution_idempotency_key
                    ),
                    activated_at=activated_at,
                )
            except (TypeError, ValueError) as exc:
                raise ProductionPilotCeremonyConflictError(
                    "Production Pilot ceremony activation is invalid"
                ) from exc

            if updated is current:
                connection.commit()
                return ProductionPilotCeremonyActivationResult(
                    record=current,
                    applied=False,
                )

            cursor = connection.execute(
                """
                UPDATE production_pilot_ceremonies
                SET execution_id = ?,
                    status = ?,
                    record_data = ?
                WHERE ceremony_id = ?
                  AND status = ?
                  AND execution_id IS NULL
                """,
                (
                    execution_id,
                    updated.status.value,
                    updated.model_dump_json(),
                    ceremony_id,
                    ProductionPilotCeremonyStatus.READY.value,
                ),
            )
            if cursor.rowcount != 1:
                raise ProductionPilotCeremonyConflictError(
                    "Production Pilot ceremony activation conflicted"
                )
            connection.commit()
            return ProductionPilotCeremonyActivationResult(
                record=updated,
                applied=True,
            )
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise ProductionPilotCeremonyConflictError(
                "Production Pilot ceremony execution binding conflicted"
            ) from exc
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    async def claim_ready(
        self,
        record: ProductionPilotCeremonyRecord,
    ) -> ProductionPilotCeremonyClaimResult:
        return await asyncio.to_thread(
            self._claim_ready_sync,
            record,
        )

    def _claim_ready_sync(
        self,
        record: ProductionPilotCeremonyRecord,
    ) -> ProductionPilotCeremonyClaimResult:
        if record.status != ProductionPilotCeremonyStatus.READY:
            raise ValueError(
                "New Production Pilot ceremony must be READY"
            )
        connection = self._connect()
        try:
            connection.execute(
                "BEGIN IMMEDIATE"
            )
            rows = connection.execute(
                """
                SELECT record_data
                FROM production_pilot_ceremonies
                WHERE pilot_id = ?
                   OR approval_id = ?
                   OR ceremony_id = ?
                   OR (
                        reviewer_operator_id = ?
                        AND idempotency_key = ?
                   )
                """,
                (
                    record.pilot_id,
                    record.approval_id,
                    str(record.ceremony_id),
                    record.reviewer_operator_id,
                    record.idempotency_key,
                ),
            ).fetchall()
            if not rows:
                connection.execute(
                    """
                    INSERT INTO production_pilot_ceremonies
                    (
                        ceremony_id,
                        pilot_id,
                        approval_id,
                        incident_id,
                        reviewer_operator_id,
                        executor_operator_id,
                        idempotency_key,
                        status,
                        record_data,
                        created_at,
                        expires_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(record.ceremony_id),
                        record.pilot_id,
                        record.approval_id,
                        str(record.incident_id),
                        record.reviewer_operator_id,
                        record.executor_operator_id,
                        record.idempotency_key,
                        record.status.value,
                        record.model_dump_json(),
                        record.created_at.isoformat(),
                        record.expires_at.isoformat(),
                    ),
                )
                connection.commit()
                return ProductionPilotCeremonyClaimResult(
                    record=record,
                    created=True,
                )

            existing_records = [
                self._deserialize(row[0])
                for row in rows
            ]
            first = existing_records[0]
            if any(
                item.ceremony_id != first.ceremony_id
                for item in existing_records[1:]
            ):
                raise ProductionPilotCeremonyConflictError(
                    "Production Pilot ceremony indexes conflict"
                )
            if not self._same_logical_binding(
                first,
                record,
            ):
                raise ProductionPilotCeremonyConflictError(
                    "Production Pilot ceremony is already bound"
                )
            connection.commit()
            return ProductionPilotCeremonyClaimResult(
                record=first,
                created=False,
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    async def get_by_pilot(
        self,
        pilot_id: str,
    ) -> ProductionPilotCeremonyRecord | None:
        return await asyncio.to_thread(
            self._get_one_sync,
            "pilot_id",
            pilot_id,
        )

    async def get_by_approval(
        self,
        approval_id: str,
    ) -> ProductionPilotCeremonyRecord | None:
        return await asyncio.to_thread(
            self._get_one_sync,
            "approval_id",
            approval_id,
        )

    async def get(
        self,
        ceremony_id: UUID | str,
    ) -> ProductionPilotCeremonyRecord | None:
        return await asyncio.to_thread(
            self._get_one_sync,
            "ceremony_id",
            str(ceremony_id),
        )

    async def get_by_execution(
        self,
        execution_id: UUID | str,
    ) -> ProductionPilotCeremonyRecord | None:
        return await asyncio.to_thread(
            self._get_one_sync,
            "execution_id",
            str(execution_id),
        )

    def _get_one_sync(
        self,
        column: str,
        value: str,
    ) -> ProductionPilotCeremonyRecord | None:
        if column not in {
            "ceremony_id",
            "pilot_id",
            "approval_id",
            "execution_id",
        }:
            raise ValueError(
                "Production Pilot ceremony query is invalid"
            )
        connection = self._connect()
        try:
            row = connection.execute(
                f"""
                SELECT record_data
                FROM production_pilot_ceremonies
                WHERE {column} = ?
                """,
                (value,),
            ).fetchone()
        finally:
            connection.close()
        return (
            None
            if row is None
            else self._deserialize(row[0])
        )

    @staticmethod
    def _same_logical_binding(
        existing: ProductionPilotCeremonyRecord,
        requested: ProductionPilotCeremonyRecord,
    ) -> bool:
        return (
            existing.ceremony_id == requested.ceremony_id
            and existing.pilot_id == requested.pilot_id
            and existing.change_ticket == requested.change_ticket
            and existing.runbook_version == requested.runbook_version
            and existing.approval_id == requested.approval_id
            and existing.incident_id == requested.incident_id
            and existing.artifact_id == requested.artifact_id
            and existing.contract_id == requested.contract_id
            and existing.patch_sha256 == requested.patch_sha256
            and existing.reviewer_operator_id
            == requested.reviewer_operator_id
            and existing.executor_operator_id
            == requested.executor_operator_id
            and existing.idempotency_key == requested.idempotency_key
            and existing.checklist == requested.checklist
        )

    @staticmethod
    def _deserialize(
        value: str,
    ) -> ProductionPilotCeremonyRecord:
        return ProductionPilotCeremonyRecord.model_validate_json(
            value
        )


__all__ = [
    "ProductionPilotCeremonyActivationResult",
    "ProductionPilotCeremonyClaimResult",
    "ProductionPilotCeremonyConflictError",
    "ProductionPilotCeremonyStore",
]
