from __future__ import annotations

import asyncio
import sqlite3

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID

from services.agent_runtime.app.action.production_pilot_budget_models import (
    ProductionPilotBudgetRecord,
    ProductionPilotBudgetStatus,
)


class ProductionPilotBudgetConflictError(RuntimeError):
    """The one-write Pilot budget is bound to another execution."""


@dataclass(frozen=True, slots=True)
class ProductionPilotBudgetReservationResult:
    record: ProductionPilotBudgetRecord
    created: bool

    @property
    def is_replay(self) -> bool:
        return not self.created


@dataclass(frozen=True, slots=True)
class ProductionPilotBudgetConsumptionResult:
    record: ProductionPilotBudgetRecord
    applied: bool

    @property
    def is_replay(self) -> bool:
        return not self.applied


class ProductionPilotBudgetStore:
    """SQLite CAS store for one irreversible write budget per Pilot."""

    def __init__(
        self,
        db_path: str | Path | None = None,
    ) -> None:
        self.db_path = Path(
            db_path
            or (
                Path("data")
                / "production_pilot_budget.db"
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
                "PRAGMA synchronous = FULL"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS production_pilot_budget
                (
                    pilot_id TEXT PRIMARY KEY,
                    execution_id TEXT NOT NULL UNIQUE,
                    approval_id TEXT NOT NULL,
                    contract_id TEXT NOT NULL,
                    patch_sha256 TEXT NOT NULL,
                    status TEXT NOT NULL,
                    record_data TEXT NOT NULL,
                    reserved_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    consumed_at TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS
                    idx_production_pilot_budget_status
                ON production_pilot_budget(status)
                """
            )
            connection.commit()
        finally:
            connection.close()

    async def reserve(
        self,
        record: ProductionPilotBudgetRecord,
    ) -> ProductionPilotBudgetReservationResult:
        return await asyncio.to_thread(
            self._reserve_sync,
            record,
        )

    def _reserve_sync(
        self,
        record: ProductionPilotBudgetRecord,
    ) -> ProductionPilotBudgetReservationResult:
        if (
            record.status
            != ProductionPilotBudgetStatus.RESERVED
            or record.consumed_at is not None
        ):
            raise ValueError(
                "New production pilot budget must be reserved"
            )

        connection = self._connect()
        try:
            connection.execute(
                "BEGIN IMMEDIATE"
            )
            pilot_row = connection.execute(
                """
                SELECT record_data
                FROM production_pilot_budget
                WHERE pilot_id = ?
                """,
                (record.pilot_id,),
            ).fetchone()
            execution_row = connection.execute(
                """
                SELECT record_data
                FROM production_pilot_budget
                WHERE execution_id = ?
                """,
                (str(record.execution_id),),
            ).fetchone()

            if pilot_row is None and execution_row is None:
                serialized = record.model_dump_json()
                connection.execute(
                    """
                    INSERT INTO production_pilot_budget
                    (
                        pilot_id,
                        execution_id,
                        approval_id,
                        contract_id,
                        patch_sha256,
                        status,
                        record_data,
                        reserved_at,
                        updated_at,
                        consumed_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.pilot_id,
                        str(record.execution_id),
                        record.approval_id,
                        str(record.contract_id),
                        record.patch_sha256,
                        record.status.value,
                        serialized,
                        record.reserved_at.isoformat(),
                        record.updated_at.isoformat(),
                        None,
                    ),
                )
                connection.commit()
                return ProductionPilotBudgetReservationResult(
                    record=record,
                    created=True,
                )

            existing = self._resolve_existing(
                pilot_row,
                execution_row,
            )
            if not self._same_binding(
                existing,
                record,
            ):
                raise ProductionPilotBudgetConflictError(
                    "Production pilot write budget is already bound"
                )
            connection.commit()
            return ProductionPilotBudgetReservationResult(
                record=existing,
                created=False,
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    async def consume(
        self,
        *,
        pilot_id: str,
        execution_id: UUID,
        contract_id: UUID,
        patch_sha256: str,
        consumed_at: datetime,
    ) -> ProductionPilotBudgetConsumptionResult:
        return await asyncio.to_thread(
            self._consume_sync,
            pilot_id,
            execution_id,
            contract_id,
            patch_sha256,
            consumed_at,
        )

    def _consume_sync(
        self,
        pilot_id: str,
        execution_id: UUID,
        contract_id: UUID,
        patch_sha256: str,
        consumed_at: datetime,
    ) -> ProductionPilotBudgetConsumptionResult:
        connection = self._connect()
        try:
            connection.execute(
                "BEGIN IMMEDIATE"
            )
            row = connection.execute(
                """
                SELECT record_data
                FROM production_pilot_budget
                WHERE pilot_id = ?
                """,
                (pilot_id,),
            ).fetchone()
            if row is None:
                raise ProductionPilotBudgetConflictError(
                    "Production pilot write budget was not reserved"
                )
            current = self._deserialize(row[0])
            if (
                current.execution_id != execution_id
                or current.contract_id != contract_id
                or current.patch_sha256 != patch_sha256
            ):
                raise ProductionPilotBudgetConflictError(
                    "Production pilot write budget binding does not match"
                )
            if (
                current.status
                == ProductionPilotBudgetStatus.CONSUMED
            ):
                connection.commit()
                return ProductionPilotBudgetConsumptionResult(
                    record=current,
                    applied=False,
                )

            updated = current.consume(
                consumed_at=consumed_at
            )
            cursor = connection.execute(
                """
                UPDATE production_pilot_budget
                SET status = ?,
                    record_data = ?,
                    updated_at = ?,
                    consumed_at = ?
                WHERE pilot_id = ? AND status = ?
                """,
                (
                    updated.status.value,
                    updated.model_dump_json(),
                    updated.updated_at.isoformat(),
                    updated.consumed_at.isoformat(),
                    pilot_id,
                    ProductionPilotBudgetStatus.RESERVED.value,
                ),
            )
            if cursor.rowcount != 1:
                raise ProductionPilotBudgetConflictError(
                    "Production pilot write budget consumption conflicted"
                )
            connection.commit()
            return ProductionPilotBudgetConsumptionResult(
                record=updated,
                applied=True,
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    async def get(
        self,
        pilot_id: str,
    ) -> ProductionPilotBudgetRecord | None:
        return await asyncio.to_thread(
            self._get_sync,
            pilot_id,
        )

    def _get_sync(
        self,
        pilot_id: str,
    ) -> ProductionPilotBudgetRecord | None:
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT record_data
                FROM production_pilot_budget
                WHERE pilot_id = ?
                """,
                (pilot_id,),
            ).fetchone()
        finally:
            connection.close()
        return (
            None
            if row is None
            else self._deserialize(row[0])
        )

    @classmethod
    def _resolve_existing(
        cls,
        pilot_row,
        execution_row,
    ) -> ProductionPilotBudgetRecord:
        rows = [
            row
            for row in (
                pilot_row,
                execution_row,
            )
            if row is not None
        ]
        records = [
            cls._deserialize(row[0])
            for row in rows
        ]
        if any(
            item.pilot_id != records[0].pilot_id
            or item.execution_id != records[0].execution_id
            for item in records[1:]
        ):
            raise ProductionPilotBudgetConflictError(
                "Production pilot budget indexes conflict"
            )
        return records[0]

    @staticmethod
    def _same_binding(
        existing: ProductionPilotBudgetRecord,
        requested: ProductionPilotBudgetRecord,
    ) -> bool:
        return (
            existing.pilot_id == requested.pilot_id
            and existing.execution_id == requested.execution_id
            and existing.approval_id == requested.approval_id
            and existing.contract_id == requested.contract_id
            and existing.operator_id == requested.operator_id
            and existing.patch_sha256 == requested.patch_sha256
        )

    @staticmethod
    def _deserialize(
        value: str,
    ) -> ProductionPilotBudgetRecord:
        return ProductionPilotBudgetRecord.model_validate_json(
            value
        )


__all__ = [
    "ProductionPilotBudgetConflictError",
    "ProductionPilotBudgetConsumptionResult",
    "ProductionPilotBudgetReservationResult",
    "ProductionPilotBudgetStore",
]
