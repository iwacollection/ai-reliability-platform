import json

from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
from re import fullmatch
from uuid import NAMESPACE_URL, UUID, uuid5

from services.agent_runtime.app.action.preflight_artifact_models import (
    PreflightArtifactRecord,
    PreflightArtifactStatus,
)
from services.agent_runtime.app.action.execution_models import (
    ActionExecutionRecord,
    ActionExecutionStatus,
)
from services.agent_runtime.app.action.preflight_artifact_service import (
    PreflightArtifactService,
)
from services.agent_runtime.app.action.production_pilot import (
    KubernetesProductionPilotControl,
)
from services.agent_runtime.app.action.production_pilot_budget_service import (
    ProductionPilotBudgetService,
)
from services.agent_runtime.app.action.production_pilot_ceremony_models import (
    ProductionPilotActivationChecklist,
    ProductionPilotCeremonyRecord,
    ProductionPilotCeremonyStatus,
)
from services.agent_runtime.app.action.production_pilot_ceremony_store import (
    ProductionPilotCeremonyActivationResult,
    ProductionPilotCeremonyClaimResult,
    ProductionPilotCeremonyConflictError,
    ProductionPilotCeremonyStore,
)
from services.agent_runtime.app.action.production_pilot_rehearsal import (
    ProductionPilotRehearsalService,
)
from services.agent_runtime.app.approval.models import (
    ApprovalStatus,
)
from services.agent_runtime.app.approval.service import (
    ApprovalService,
)


class ProductionPilotCeremonyError(RuntimeError):
    """Production Pilot ceremony evidence could not be recorded safely."""


_IDENTIFIER_PATTERN = (
    r"[A-Za-z0-9](?:[A-Za-z0-9_.:/-]{0,126}[A-Za-z0-9])?"
)


class ProductionPilotCeremonyService:
    """Validate and durably record the reviewed one-shot canary checklist."""

    def __init__(
        self,
        *,
        store: ProductionPilotCeremonyStore,
        control: KubernetesProductionPilotControl,
        rehearsal: ProductionPilotRehearsalService,
        budget_service: ProductionPilotBudgetService,
        approval_service: ApprovalService,
        artifact_service: PreflightArtifactService,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        expected_types = (
            (store, ProductionPilotCeremonyStore, "store"),
            (control, KubernetesProductionPilotControl, "control"),
            (rehearsal, ProductionPilotRehearsalService, "rehearsal"),
            (budget_service, ProductionPilotBudgetService, "budget service"),
            (approval_service, ApprovalService, "Approval service"),
            (artifact_service, PreflightArtifactService, "Artifact service"),
        )
        for value, expected, label in expected_types:
            if not isinstance(value, expected):
                raise TypeError(
                    f"Production Pilot ceremony {label} is invalid"
                )
        self.store = store
        self.control = control
        self.rehearsal = rehearsal
        self.budget_service = budget_service
        self.approval_service = approval_service
        self.artifact_service = artifact_service
        self._clock = clock or (
            lambda: datetime.now(UTC)
        )

    async def record_checklist(
        self,
        *,
        approval_id: str,
        reviewer_operator_id: str,
        idempotency_key: str,
        checklist: ProductionPilotActivationChecklist,
    ) -> ProductionPilotCeremonyClaimResult:
        self._required_identifier(
            approval_id,
            "Approval ID",
        )
        self._required_identifier(
            reviewer_operator_id,
            "reviewer operator ID",
        )
        self._required_identifier(
            idempotency_key,
            "idempotency key",
        )
        if not isinstance(
            checklist,
            ProductionPilotActivationChecklist,
        ):
            raise TypeError(
                "Production Pilot activation checklist is invalid"
            )

        pilot_id = self.control.config.pilot_id
        if pilot_id is not None:
            existing = await self.store.get_by_pilot(
                pilot_id
            )
            if existing is not None:
                self._require_exact_replay(
                    existing=existing,
                    approval_id=approval_id,
                    reviewer_operator_id=reviewer_operator_id,
                    idempotency_key=idempotency_key,
                    checklist=checklist,
                )
                return ProductionPilotCeremonyClaimResult(
                    record=existing,
                    created=False,
                )

        if reviewer_operator_id == checklist.executor_operator_id:
            raise ProductionPilotCeremonyError(
                "Production Pilot reviewer and executor must be different"
            )
        if (
            checklist.executor_operator_id
            not in self.control.config.authorized_operator_ids
        ):
            raise ProductionPilotCeremonyError(
                "Production Pilot executor is not in the exact allowlist"
            )

        try:
            readiness = self.control.require_enablement()
        except Exception as exc:
            raise ProductionPilotCeremonyError(
                "Production Pilot manifest is not ready for ceremony"
            ) from exc
        if readiness.kill_switch.state != "engaged":
            raise ProductionPilotCeremonyError(
                "Production Pilot ceremony requires an ENGAGED Kill Switch"
            )

        rehearsal = await self.rehearsal.run(
            operator_id=checklist.executor_operator_id
        )
        if not rehearsal.passed:
            raise ProductionPilotCeremonyError(
                "Production Pilot zero-write rehearsal did not pass"
            )

        approval = await self.approval_service.get(
            approval_id
        )
        if (
            approval is None
            or approval.status != ApprovalStatus.APPROVED
            or approval.incident_id is None
            or approval.action.approved is not True
        ):
            raise ProductionPilotCeremonyError(
                "Production Pilot ceremony requires an approved Action"
            )
        artifact = await self.artifact_service.get_by_approval_id(
            approval.id
        )
        if (
            artifact is None
            or artifact.status
            != PreflightArtifactStatus.APPROVAL_BOUND
            or artifact.approval_id != approval.id
            or artifact.incident_id != approval.incident_id
        ):
            raise ProductionPilotCeremonyError(
                "Production Pilot Artifact binding is invalid"
            )

        checked_at = self._checked_at()
        contract = artifact.artifact.contract
        try:
            contract.require_executable_plan(
                approval.action,
                at=checked_at,
            )
        except Exception as exc:
            raise ProductionPilotCeremonyError(
                "Production Pilot Safety Contract is not executable"
            ) from exc

        config = self.control.config
        if any(
            value is None
            for value in (
                config.pilot_id,
                config.change_ticket,
                config.runbook_version,
                config.expires_at,
            )
        ):
            raise ProductionPilotCeremonyError(
                "Production Pilot manifest identity is incomplete"
            )
        assert config.pilot_id is not None
        assert config.change_ticket is not None
        assert config.runbook_version is not None
        assert config.expires_at is not None
        expires_at = min(
            config.expires_at,
            contract.expires_at,
        )
        if checked_at >= expires_at:
            raise ProductionPilotCeremonyError(
                "Production Pilot ceremony has expired"
            )

        ceremony_id = uuid5(
            NAMESPACE_URL,
            "ai-reliability-platform:production-pilot-ceremony:"
            f"{config.pilot_id}:{approval.id}",
        )
        evidence_values = {
            "ceremony_id": str(ceremony_id),
            "pilot_id": config.pilot_id,
            "change_ticket": config.change_ticket,
            "runbook_version": config.runbook_version,
            "approval_id": approval.id,
            "incident_id": str(approval.incident_id),
            "artifact_id": str(artifact.artifact_id),
            "contract_id": str(contract.contract_id),
            "patch_sha256": contract.dry_run.patch_sha256,
            "reviewer_operator_id": reviewer_operator_id,
            "executor_operator_id": checklist.executor_operator_id,
            "idempotency_key": idempotency_key,
            "checklist": checklist.model_dump(mode="json"),
            "readiness_checked_at": readiness.checked_at.isoformat(),
            "created_at": checked_at.isoformat(),
            "expires_at": expires_at.isoformat(),
        }
        evidence_sha256 = sha256(
            json.dumps(
                evidence_values,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest()
        record = ProductionPilotCeremonyRecord(
            **evidence_values,
            evidence_sha256=evidence_sha256,
        )
        return await self.store.claim_ready(
            record
        )

    async def get_by_approval(
        self,
        approval_id: str,
    ) -> ProductionPilotCeremonyRecord | None:
        return await self.store.get_by_approval(
            approval_id
        )

    async def get_by_execution(
        self,
        execution_id: UUID | str,
    ) -> ProductionPilotCeremonyRecord | None:
        return await self.store.get_by_execution(
            execution_id
        )

    def audit_checked_at(self) -> datetime:
        """Return the same trusted UTC clock used by Ceremony validation."""

        return self._checked_at()

    async def activate_for_execution(
        self,
        *,
        execution: ActionExecutionRecord,
        preflight_record: PreflightArtifactRecord,
    ) -> ProductionPilotCeremonyActivationResult:
        """
        Atomically bind READY evidence to one durable RUNNING Claim.

        This method never creates the Action Execution, reserves the Pilot
        budget, contacts Kubernetes, or starts Verification. Exact activation
        replay returns applied=False and must never grant a new executor call.
        """

        if (
            not isinstance(execution, ActionExecutionRecord)
            or execution.status != ActionExecutionStatus.RUNNING
        ):
            raise ProductionPilotCeremonyError(
                "Production Pilot activation requires a RUNNING execution"
            )
        if not isinstance(
            preflight_record,
            PreflightArtifactRecord,
        ):
            raise ProductionPilotCeremonyError(
                "Production Pilot activation Artifact is invalid"
            )

        ceremony = await self.store.get_by_approval(
            execution.approval_id
        )
        if ceremony is None:
            raise ProductionPilotCeremonyError(
                "Production Pilot activation ceremony is missing"
            )

        self._require_execution_binding(
            ceremony=ceremony,
            execution=execution,
            preflight_record=preflight_record,
        )

        if ceremony.status == ProductionPilotCeremonyStatus.ACTIVATED:
            assert ceremony.activated_at is not None
            try:
                return await self.store.activate(
                    ceremony_id=ceremony.ceremony_id,
                    execution_id=execution.id,
                    execution_idempotency_key=(
                        execution.idempotency_key
                    ),
                    activated_at=ceremony.activated_at,
                )
            except (
                ProductionPilotCeremonyConflictError,
                TypeError,
                ValueError,
            ) as exc:
                raise ProductionPilotCeremonyError(
                    "Production Pilot activation replay conflicts"
                ) from exc

        checked_at = self._checked_at()
        if checked_at >= ceremony.expires_at:
            raise ProductionPilotCeremonyError(
                "Production Pilot activation ceremony has expired"
            )

        try:
            self.control.require_execution(
                operator_id=execution.operator_id,
                production_executor_configured=True,
            )
        except Exception as exc:
            raise ProductionPilotCeremonyError(
                "Production Pilot is not ready for activation"
            ) from exc

        approval = await self.approval_service.get(
            execution.approval_id
        )
        if (
            approval is None
            or approval.status != ApprovalStatus.APPROVED
            or approval.action.approved is not True
            or approval.incident_id != execution.incident_id
            or approval.action != execution.action
        ):
            raise ProductionPilotCeremonyError(
                "Production Pilot activation Approval binding is invalid"
            )

        durable_artifact = (
            await self.artifact_service.get_by_approval_id(
                execution.approval_id
            )
        )
        if (
            durable_artifact is None
            or durable_artifact != preflight_record
            or durable_artifact.status
            != PreflightArtifactStatus.APPROVAL_BOUND
        ):
            raise ProductionPilotCeremonyError(
                "Production Pilot activation Artifact binding is invalid"
            )

        budget = await self.budget_service.get(
            ceremony.pilot_id
        )
        if budget is not None:
            raise ProductionPilotCeremonyError(
                "Production Pilot activation requires an available write budget"
            )

        contract = durable_artifact.artifact.contract
        try:
            contract.require_executable_plan(
                execution.action,
                at=checked_at,
            )
        except Exception as exc:
            raise ProductionPilotCeremonyError(
                "Production Pilot activation Safety Contract is not executable"
            ) from exc

        try:
            return await self.store.activate(
                ceremony_id=ceremony.ceremony_id,
                execution_id=execution.id,
                execution_idempotency_key=(
                    execution.idempotency_key
                ),
                activated_at=checked_at,
            )
        except (
            ProductionPilotCeremonyConflictError,
            TypeError,
            ValueError,
        ) as exc:
            raise ProductionPilotCeremonyError(
                "Production Pilot activation conflicted"
            ) from exc

    def _require_execution_binding(
        self,
        *,
        ceremony: ProductionPilotCeremonyRecord,
        execution: ActionExecutionRecord,
        preflight_record: PreflightArtifactRecord,
    ) -> None:
        contract = preflight_record.artifact.contract
        config = self.control.config
        if (
            ceremony.pilot_id != config.pilot_id
            or ceremony.change_ticket != config.change_ticket
            or ceremony.runbook_version != config.runbook_version
            or ceremony.approval_id != execution.approval_id
            or ceremony.incident_id != execution.incident_id
            or ceremony.executor_operator_id != execution.operator_id
            or preflight_record.approval_id != execution.approval_id
            or preflight_record.incident_id != execution.incident_id
            or ceremony.artifact_id != preflight_record.artifact_id
            or ceremony.contract_id != contract.contract_id
            or ceremony.patch_sha256
            != contract.dry_run.patch_sha256
            or ceremony.evidence_sha256
            != ceremony.expected_evidence_sha256()
        ):
            raise ProductionPilotCeremonyError(
                "Production Pilot activation binding is invalid"
            )

    def _checked_at(self) -> datetime:
        value = self._clock()
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise ProductionPilotCeremonyError(
                "Production Pilot ceremony clock is invalid"
            )
        return value.astimezone(UTC)

    @staticmethod
    def _required_identifier(
        value: str,
        label: str,
    ) -> None:
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or len(value) > 128
            or fullmatch(
                _IDENTIFIER_PATTERN,
                value,
            )
            is None
        ):
            raise ValueError(
                f"Production Pilot ceremony {label} is invalid"
            )

    @staticmethod
    def _require_exact_replay(
        *,
        existing: ProductionPilotCeremonyRecord,
        approval_id: str,
        reviewer_operator_id: str,
        idempotency_key: str,
        checklist: ProductionPilotActivationChecklist,
    ) -> None:
        if (
            existing.approval_id != approval_id
            or existing.reviewer_operator_id
            != reviewer_operator_id
            or existing.idempotency_key != idempotency_key
            or existing.checklist != checklist
        ):
            raise ProductionPilotCeremonyError(
                "Production Pilot ceremony replay conflicts with durable evidence"
            )


__all__ = [
    "ProductionPilotCeremonyError",
    "ProductionPilotCeremonyService",
]
