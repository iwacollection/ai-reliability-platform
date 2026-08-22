from collections.abc import Callable
from datetime import UTC, datetime

from services.agent_runtime.app.action.execution_models import (
    ActionExecutionRecord,
    ActionExecutionStatus,
)
from services.agent_runtime.app.action.preflight_artifact_models import (
    PreflightArtifactRecord,
)
from services.agent_runtime.app.action.production_pilot_budget_models import (
    ProductionPilotBudgetRecord,
)
from services.agent_runtime.app.action.production_pilot_budget_store import (
    ProductionPilotBudgetConsumptionResult,
    ProductionPilotBudgetReservationResult,
    ProductionPilotBudgetStore,
)


class ProductionPilotBudgetService:
    """Bind and consume the irreversible one-write budget for one Pilot."""

    def __init__(
        self,
        *,
        store: ProductionPilotBudgetStore,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(
            store,
            ProductionPilotBudgetStore,
        ):
            raise TypeError(
                "Production pilot budget store is invalid"
            )
        self.store = store
        self._clock = clock or (
            lambda: datetime.now(UTC)
        )

    async def reserve(
        self,
        *,
        pilot_id: str,
        execution: ActionExecutionRecord,
        preflight_record: PreflightArtifactRecord,
    ) -> ProductionPilotBudgetReservationResult:
        self._require_running_execution(
            execution
        )
        self._require_binding(
            execution,
            preflight_record,
        )
        contract = (
            preflight_record.artifact.contract
        )
        checked_at = self._checked_at()
        record = ProductionPilotBudgetRecord(
            pilot_id=pilot_id,
            execution_id=execution.id,
            approval_id=execution.approval_id,
            contract_id=contract.contract_id,
            operator_id=execution.operator_id,
            patch_sha256=(
                contract.dry_run.patch_sha256
            ),
            reserved_at=checked_at,
            updated_at=checked_at,
        )
        return await self.store.reserve(
            record
        )

    async def consume(
        self,
        *,
        pilot_id: str,
        execution: ActionExecutionRecord,
        preflight_record: PreflightArtifactRecord,
    ) -> ProductionPilotBudgetConsumptionResult:
        self._require_running_execution(
            execution
        )
        self._require_binding(
            execution,
            preflight_record,
        )
        contract = (
            preflight_record.artifact.contract
        )
        return await self.store.consume(
            pilot_id=pilot_id,
            execution_id=execution.id,
            contract_id=contract.contract_id,
            patch_sha256=(
                contract.dry_run.patch_sha256
            ),
            consumed_at=self._checked_at(),
        )

    async def get(
        self,
        pilot_id: str,
    ) -> ProductionPilotBudgetRecord | None:
        return await self.store.get(
            pilot_id
        )

    def _checked_at(self) -> datetime:
        value = self._clock()
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise ValueError(
                "Production pilot budget clock is invalid"
            )
        return value.astimezone(UTC)

    @staticmethod
    def _require_running_execution(
        execution: ActionExecutionRecord,
    ) -> None:
        if (
            not isinstance(
                execution,
                ActionExecutionRecord,
            )
            or execution.status
            != ActionExecutionStatus.RUNNING
        ):
            raise ValueError(
                "Production pilot budget requires a RUNNING execution"
            )

    @staticmethod
    def _require_binding(
        execution: ActionExecutionRecord,
        record: PreflightArtifactRecord,
    ) -> None:
        if not isinstance(
            record,
            PreflightArtifactRecord,
        ):
            raise TypeError(
                "Production pilot budget preflight record is invalid"
            )
        if record.approval_id != execution.approval_id:
            raise ValueError(
                "Production pilot budget Approval binding is invalid"
            )
        if record.incident_id != execution.incident_id:
            raise ValueError(
                "Production pilot budget Incident binding is invalid"
            )


__all__ = [
    "ProductionPilotBudgetService",
]
