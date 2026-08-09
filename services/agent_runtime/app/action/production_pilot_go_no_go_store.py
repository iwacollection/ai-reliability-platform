import asyncio
import sqlite3

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from services.agent_runtime.app.action.production_pilot_go_no_go_models import (
    ProductionPilotGoNoGoRecord,
    ProductionPilotLiveProbeRecord,
    ProductionPilotLiveProbeStatus,
)


class ProductionPilotGoNoGoConflictError(RuntimeError):
    """One Pilot binding is already owned by different evidence."""


@dataclass(frozen=True, slots=True)
class ProductionPilotLiveProbeClaimResult:
    record: ProductionPilotLiveProbeRecord
    created: bool

    @property
    def is_replay(self) -> bool:
        return not self.created


@dataclass(frozen=True, slots=True)
class ProductionPilotLiveProbeCompletionResult:
    record: ProductionPilotLiveProbeRecord
    applied: bool

    @property
    def is_replay(self) -> bool:
        return not self.applied


@dataclass(frozen=True, slots=True)
class ProductionPilotGoNoGoClaimResult:
    record: ProductionPilotGoNoGoRecord
    created: bool

    @property
    def is_replay(self) -> bool:
        return not self.created


class ProductionPilotGoNoGoStore:
    """SQLite CAS store for one live probe and one final decision per Pilot."""

    def __init__(
        self,
        db_path: str | Path | None = None,
    ) -> None:
        self.db_path = Path(
            db_path
            or (
                Path("data")
                / "production_pilot_go_no_go.db"
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
                CREATE TABLE IF NOT EXISTS production_pilot_live_probes
                (
                    probe_id TEXT PRIMARY KEY,
                    approval_id TEXT NOT NULL UNIQUE,
                    pilot_id TEXT NOT NULL UNIQUE,
                    executor_operator_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_sha256 TEXT NOT NULL,
                    status TEXT NOT NULL,
                    record_data TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS
                    idx_production_pilot_live_probe_idempotency
                ON production_pilot_live_probes(
                    executor_operator_id,
                    idempotency_key
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS production_pilot_go_no_go_decisions
                (
                    decision_id TEXT PRIMARY KEY,
                    probe_id TEXT NOT NULL UNIQUE,
                    approval_id TEXT NOT NULL UNIQUE,
                    pilot_id TEXT NOT NULL UNIQUE,
                    reviewer_operator_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_sha256 TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    record_data TEXT NOT NULL,
                    decided_at TEXT NOT NULL,
                    FOREIGN KEY(probe_id)
                        REFERENCES production_pilot_live_probes(probe_id)
                )
                """
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS
                    idx_production_pilot_go_no_go_idempotency
                ON production_pilot_go_no_go_decisions(
                    reviewer_operator_id,
                    idempotency_key
                )
                """
            )
            connection.commit()
        finally:
            connection.close()

    async def claim_probe(
        self,
        record: ProductionPilotLiveProbeRecord,
    ) -> ProductionPilotLiveProbeClaimResult:
        return await asyncio.to_thread(
            self._claim_probe_sync,
            record,
        )

    def _claim_probe_sync(
        self,
        record: ProductionPilotLiveProbeRecord,
    ) -> ProductionPilotLiveProbeClaimResult:
        if record.status != ProductionPilotLiveProbeStatus.RUNNING.value:
            raise ValueError(
                "New Production Pilot live probe must be RUNNING"
            )
        connection = self._connect()
        try:
            connection.execute(
                "BEGIN IMMEDIATE"
            )
            rows = connection.execute(
                """
                SELECT record_data
                FROM production_pilot_live_probes
                WHERE probe_id = ?
                   OR approval_id = ?
                   OR pilot_id = ?
                   OR (
                        executor_operator_id = ?
                        AND idempotency_key = ?
                   )
                """,
                (
                    str(record.probe_id),
                    record.approval_id,
                    record.pilot_id,
                    record.executor_operator_id,
                    record.idempotency_key,
                ),
            ).fetchall()
            if not rows:
                connection.execute(
                    """
                    INSERT INTO production_pilot_live_probes
                    (
                        probe_id,
                        approval_id,
                        pilot_id,
                        executor_operator_id,
                        idempotency_key,
                        request_sha256,
                        status,
                        record_data,
                        started_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(record.probe_id),
                        record.approval_id,
                        record.pilot_id,
                        record.executor_operator_id,
                        record.idempotency_key,
                        record.request_sha256,
                        record.status,
                        record.model_dump_json(),
                        record.started_at.isoformat(),
                        record.updated_at.isoformat(),
                    ),
                )
                connection.commit()
                return ProductionPilotLiveProbeClaimResult(
                    record=record,
                    created=True,
                )

            existing_records = [
                self._deserialize_probe(row[0])
                for row in rows
            ]
            first = existing_records[0]
            if any(
                item.probe_id != first.probe_id
                for item in existing_records[1:]
            ):
                raise ProductionPilotGoNoGoConflictError(
                    "Production Pilot live probe indexes conflict"
                )
            if not self._same_probe_binding(first, record):
                raise ProductionPilotGoNoGoConflictError(
                    "Production Pilot live probe is already bound"
                )
            connection.commit()
            return ProductionPilotLiveProbeClaimResult(
                record=first,
                created=False,
            )
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise ProductionPilotGoNoGoConflictError(
                "Production Pilot live probe claim conflicted"
            ) from exc
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    async def complete_probe(
        self,
        record: ProductionPilotLiveProbeRecord,
    ) -> ProductionPilotLiveProbeCompletionResult:
        return await asyncio.to_thread(
            self._complete_probe_sync,
            record,
        )

    def _complete_probe_sync(
        self,
        record: ProductionPilotLiveProbeRecord,
    ) -> ProductionPilotLiveProbeCompletionResult:
        if not record.is_terminal:
            raise ValueError(
                "Production Pilot live probe completion must be terminal"
            )
        connection = self._connect()
        try:
            connection.execute(
                "BEGIN IMMEDIATE"
            )
            row = connection.execute(
                """
                SELECT record_data
                FROM production_pilot_live_probes
                WHERE probe_id = ?
                """,
                (str(record.probe_id),),
            ).fetchone()
            if row is None:
                raise ProductionPilotGoNoGoConflictError(
                    "Production Pilot live probe claim was not found"
                )
            current = self._deserialize_probe(row[0])
            if not self._same_probe_binding(current, record):
                raise ProductionPilotGoNoGoConflictError(
                    "Production Pilot live probe completion binding changed"
                )
            if current.is_terminal:
                if current.record_sha256 != record.record_sha256:
                    raise ProductionPilotGoNoGoConflictError(
                        "Production Pilot live probe already has another result"
                    )
                connection.commit()
                return ProductionPilotLiveProbeCompletionResult(
                    record=current,
                    applied=False,
                )

            cursor = connection.execute(
                """
                UPDATE production_pilot_live_probes
                SET status = ?,
                    record_data = ?,
                    updated_at = ?
                WHERE probe_id = ?
                  AND status = ?
                """,
                (
                    record.status,
                    record.model_dump_json(),
                    record.updated_at.isoformat(),
                    str(record.probe_id),
                    ProductionPilotLiveProbeStatus.RUNNING.value,
                ),
            )
            if cursor.rowcount != 1:
                raise ProductionPilotGoNoGoConflictError(
                    "Production Pilot live probe completion conflicted"
                )
            connection.commit()
            return ProductionPilotLiveProbeCompletionResult(
                record=record,
                applied=True,
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    async def get_probe(
        self,
        probe_id: UUID | str,
    ) -> ProductionPilotLiveProbeRecord | None:
        return await asyncio.to_thread(
            self._get_probe_sync,
            str(probe_id),
            "probe_id",
        )

    async def get_probe_by_approval(
        self,
        approval_id: str,
    ) -> ProductionPilotLiveProbeRecord | None:
        return await asyncio.to_thread(
            self._get_probe_sync,
            approval_id,
            "approval_id",
        )

    def _get_probe_sync(
        self,
        value: str,
        column: str,
    ) -> ProductionPilotLiveProbeRecord | None:
        if column not in {"probe_id", "approval_id"}:
            raise ValueError(
                "Production Pilot live probe lookup is invalid"
            )
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT record_data
                FROM production_pilot_live_probes
                WHERE """
                + column
                + " = ?",
                (value,),
            ).fetchone()
            return (
                self._deserialize_probe(row[0])
                if row is not None
                else None
            )
        finally:
            connection.close()

    async def claim_decision(
        self,
        record: ProductionPilotGoNoGoRecord,
    ) -> ProductionPilotGoNoGoClaimResult:
        return await asyncio.to_thread(
            self._claim_decision_sync,
            record,
        )

    def _claim_decision_sync(
        self,
        record: ProductionPilotGoNoGoRecord,
    ) -> ProductionPilotGoNoGoClaimResult:
        connection = self._connect()
        try:
            connection.execute(
                "BEGIN IMMEDIATE"
            )
            probe_row = connection.execute(
                """
                SELECT record_data
                FROM production_pilot_live_probes
                WHERE probe_id = ?
                """,
                (str(record.probe_id),),
            ).fetchone()
            if probe_row is None:
                raise ProductionPilotGoNoGoConflictError(
                    "Production Pilot live probe was not found"
                )
            probe = self._deserialize_probe(probe_row[0])
            if (
                probe.approval_id != record.approval_id
                or probe.pilot_id != record.pilot_id
                or probe.record_sha256 != record.probe_record_sha256
            ):
                raise ProductionPilotGoNoGoConflictError(
                    "Production Pilot decision probe binding changed"
                )

            rows = connection.execute(
                """
                SELECT record_data
                FROM production_pilot_go_no_go_decisions
                WHERE decision_id = ?
                   OR probe_id = ?
                   OR approval_id = ?
                   OR pilot_id = ?
                   OR (
                        reviewer_operator_id = ?
                        AND idempotency_key = ?
                   )
                """,
                (
                    str(record.decision_id),
                    str(record.probe_id),
                    record.approval_id,
                    record.pilot_id,
                    record.reviewer_operator_id,
                    record.idempotency_key,
                ),
            ).fetchall()
            if not rows:
                connection.execute(
                    """
                    INSERT INTO production_pilot_go_no_go_decisions
                    (
                        decision_id,
                        probe_id,
                        approval_id,
                        pilot_id,
                        reviewer_operator_id,
                        idempotency_key,
                        request_sha256,
                        decision,
                        record_data,
                        decided_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(record.decision_id),
                        str(record.probe_id),
                        record.approval_id,
                        record.pilot_id,
                        record.reviewer_operator_id,
                        record.idempotency_key,
                        record.request_sha256,
                        record.decision,
                        record.model_dump_json(),
                        record.decided_at.isoformat(),
                    ),
                )
                connection.commit()
                return ProductionPilotGoNoGoClaimResult(
                    record=record,
                    created=True,
                )

            existing_records = [
                self._deserialize_decision(row[0])
                for row in rows
            ]
            first = existing_records[0]
            if any(
                item.decision_id != first.decision_id
                for item in existing_records[1:]
            ):
                raise ProductionPilotGoNoGoConflictError(
                    "Production Pilot Go/No-Go indexes conflict"
                )
            if not self._same_decision_binding(first, record):
                raise ProductionPilotGoNoGoConflictError(
                    "Production Pilot Go/No-Go decision is already bound"
                )
            connection.commit()
            return ProductionPilotGoNoGoClaimResult(
                record=first,
                created=False,
            )
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise ProductionPilotGoNoGoConflictError(
                "Production Pilot Go/No-Go decision conflicted"
            ) from exc
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    async def get_decision_by_approval(
        self,
        approval_id: str,
    ) -> ProductionPilotGoNoGoRecord | None:
        return await asyncio.to_thread(
            self._get_decision_by_approval_sync,
            approval_id,
        )

    def _get_decision_by_approval_sync(
        self,
        approval_id: str,
    ) -> ProductionPilotGoNoGoRecord | None:
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT record_data
                FROM production_pilot_go_no_go_decisions
                WHERE approval_id = ?
                """,
                (approval_id,),
            ).fetchone()
            return (
                self._deserialize_decision(row[0])
                if row is not None
                else None
            )
        finally:
            connection.close()

    @staticmethod
    def _same_probe_binding(
        left: ProductionPilotLiveProbeRecord,
        right: ProductionPilotLiveProbeRecord,
    ) -> bool:
        return (
            left.probe_id == right.probe_id
            and left.approval_id == right.approval_id
            and left.incident_id == right.incident_id
            and left.artifact_id == right.artifact_id
            and left.ceremony_id == right.ceremony_id
            and left.pilot_id == right.pilot_id
            and left.executor_operator_id
            == right.executor_operator_id
            and left.idempotency_key == right.idempotency_key
            and left.request_sha256 == right.request_sha256
            and left.evidence_sha256 == right.evidence_sha256
            and left.handoff_report_sha256
            == right.handoff_report_sha256
            and left.configuration_sha256
            == right.configuration_sha256
            and left.deployment_release_sha256
            == right.deployment_release_sha256
        )

    @staticmethod
    def _same_decision_binding(
        left: ProductionPilotGoNoGoRecord,
        right: ProductionPilotGoNoGoRecord,
    ) -> bool:
        return (
            left.decision_id == right.decision_id
            and left.probe_id == right.probe_id
            and left.approval_id == right.approval_id
            and left.pilot_id == right.pilot_id
            and left.reviewer_operator_id
            == right.reviewer_operator_id
            and left.idempotency_key == right.idempotency_key
            and left.request_sha256 == right.request_sha256
            and left.probe_record_sha256
            == right.probe_record_sha256
        )

    @staticmethod
    def _deserialize_probe(
        value: str,
    ) -> ProductionPilotLiveProbeRecord:
        return ProductionPilotLiveProbeRecord.model_validate_json(
            value
        )

    @staticmethod
    def _deserialize_decision(
        value: str,
    ) -> ProductionPilotGoNoGoRecord:
        return ProductionPilotGoNoGoRecord.model_validate_json(
            value
        )


__all__ = [
    "ProductionPilotGoNoGoClaimResult",
    "ProductionPilotGoNoGoConflictError",
    "ProductionPilotGoNoGoStore",
    "ProductionPilotLiveProbeClaimResult",
    "ProductionPilotLiveProbeCompletionResult",
]
