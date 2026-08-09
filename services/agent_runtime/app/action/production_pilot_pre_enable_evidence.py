import json

from datetime import datetime
from hashlib import sha256
from re import fullmatch
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from services.agent_runtime.app.action.execution_service import (
    ActionExecutionService,
)
from services.agent_runtime.app.action.preflight_artifact_models import (
    PreflightArtifactStatus,
)
from services.agent_runtime.app.action.preflight_artifact_service import (
    PreflightArtifactService,
)
from services.agent_runtime.app.action.production_pilot import (
    ProductionPilotReadinessService,
)
from services.agent_runtime.app.action.production_pilot_budget_service import (
    ProductionPilotBudgetService,
)
from services.agent_runtime.app.action.production_pilot_ceremony_models import (
    ProductionPilotCeremonyStatus,
)
from services.agent_runtime.app.action.production_pilot_ceremony_service import (
    ProductionPilotCeremonyService,
)
from services.agent_runtime.app.action.production_pilot_crash_rehearsal import (
    ProductionPilotCrashRecoveryRehearsalService,
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
from services.agent_runtime.app.incident.enums import (
    IncidentStatus,
)
from services.agent_runtime.app.incident.store import (
    IncidentStore,
)
from services.agent_runtime.app.verification.service import (
    VerificationService,
)


PRODUCTION_PILOT_PRE_ENABLE_SIGN_OFF_ACKNOWLEDGEMENT = (
    "I_CONFIRM_OOM_PILOT_PRE_ENABLE_EVIDENCE_V1"
)
_SCHEMA_VERSION = "production_pilot_pre_enable_evidence/v1"
_SHA256_PATTERN = r"[0-9a-f]{64}"
_IDENTIFIER_PATTERN = (
    r"[A-Za-z0-9](?:[A-Za-z0-9_.:/-]{0,126}[A-Za-z0-9])?"
)


class ProductionPilotPreEnableEvidenceError(RuntimeError):
    """The bounded evidence cannot safely support a zero-write sign-off."""


class ProductionPilotPreEnableEvidenceConflictError(
    ProductionPilotPreEnableEvidenceError
):
    """The operator signed a stale or conflicting evidence digest."""


class ProductionPilotPreEnableEvidencePack(BaseModel):
    """
    Bounded live evidence assembled immediately before feature enablement.

    The pack intentionally omits raw patches, workload UID, resourceVersion,
    request headers, idempotency keys, credential references, credentials and
    persisted error text. Its digest is integrity evidence, not authorization
    to enable or execute the production Action.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    schema_version: Literal[
        "production_pilot_pre_enable_evidence/v1"
    ] = _SCHEMA_VERSION
    approval_id: str = Field(
        min_length=1,
        max_length=128,
    )
    incident_id: str
    artifact_id: str
    contract_id: str
    ceremony_id: str
    pilot_id: str
    change_ticket: str
    runbook_version: str
    approval_decision_operator_id: str | None
    ceremony_reviewer_operator_id: str
    executor_operator_id: str

    cluster: str | None
    namespace: str | None
    workload_kind: str | None
    workload_name: str | None
    container: str | None
    action_type: str | None
    current_memory_limit: str | None
    desired_memory_limit: str | None
    rollback_memory_limit: str | None
    memory_increase_percent: float | None
    safety_policy_version: str | None
    safety_patch_sha256: str

    artifact_state: Literal[
        "missing",
        "approval_bound",
        "invalid",
    ]
    approval_state: Literal[
        "missing",
        "pending",
        "approved",
        "rejected",
        "expired",
        "invalid",
    ]
    incident_state: Literal[
        "missing",
        "new",
        "analyzing",
        "confirmed",
        "healing",
        "resolved",
        "failed",
        "invalid",
    ]
    ceremony_state: Literal[
        "ready",
        "activated",
        "invalid",
    ]
    budget_state: Literal[
        "available",
        "reserved",
        "consumed",
        "invalid",
    ]
    action_execution_state: Literal[
        "not_created",
        "running",
        "succeeded",
        "failed",
        "indeterminate",
        "invalid",
    ]
    verification_state: Literal[
        "not_created",
        "pending",
        "running",
        "passed",
        "failed",
        "inconclusive",
        "timed_out",
        "invalid",
    ]
    contract_clock_state: Literal[
        "valid",
        "expired",
        "clock_invalid",
        "missing",
    ]
    ceremony_clock_state: Literal[
        "valid",
        "expired",
        "clock_invalid",
    ]
    pilot_window_state: Literal[
        "not_configured",
        "not_started",
        "active",
        "expired",
        "clock_invalid",
    ]
    kill_switch_state: Literal[
        "unconfigured",
        "unavailable",
        "invalid",
        "engaged",
        "disengaged",
    ]
    production_execution_enabled: bool
    production_executor_configured: bool
    exact_single_target: bool
    credential_references_separate: bool
    write_acknowledged: bool
    runbook_acknowledged: bool
    executor_allowlisted: bool
    reviewer_executor_separated: bool
    approval_executor_separated: bool
    bindings_consistent: bool
    enablement_rehearsal_passed: bool
    crash_recovery_rehearsal_passed: bool
    crash_recovery_checkpoint_count: int = Field(
        ge=0,
        le=13,
    )
    crash_recovery_report_sha256: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN,
    )

    ready_for_sign_off: bool
    evidence_blockers: tuple[str, ...]
    live_state_checked: Literal[True] = True
    storage_read_only: Literal[True] = True
    storage_write_count: Literal[0] = 0
    durable_claim_created: Literal[False] = False
    budget_reservation_count: Literal[0] = 0
    external_call_count: Literal[0] = 0
    kubernetes_call_count: Literal[0] = 0
    production_executor_call_count: Literal[0] = 0
    verification_call_count: Literal[0] = 0
    real_write_attempted: Literal[False] = False
    authorizes_enablement: Literal[False] = False
    authorizes_execution: Literal[False] = False
    automatic_resume_allowed: Literal[False] = False
    requires_live_recheck_before_resume: Literal[True] = True
    evidence_sha256: str = Field(
        pattern=_SHA256_PATTERN,
    )

    @model_validator(mode="after")
    def validate_integrity(
        self,
    ) -> "ProductionPilotPreEnableEvidencePack":
        if not self.evidence_blockers:
            if not self.ready_for_sign_off:
                raise ValueError(
                    "Pre-enable evidence without blockers must be ready"
                )
        elif self.ready_for_sign_off:
            raise ValueError(
                "Blocked pre-enable evidence cannot be signed"
            )
        expected = _digest_model(
            self,
            excluded={"evidence_sha256"},
        )
        if self.evidence_sha256 != expected:
            raise ValueError(
                "Production Pilot pre-enable evidence digest is invalid"
            )
        return self


class ProductionPilotPreEnableSignOffRequest(BaseModel):
    """Explicit acknowledgement of one exact live evidence digest."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    expected_evidence_sha256: str = Field(
        pattern=_SHA256_PATTERN,
    )
    acknowledgement: str = Field(
        min_length=1,
        max_length=128,
    )

    @model_validator(mode="after")
    def validate_acknowledgement(
        self,
    ) -> "ProductionPilotPreEnableSignOffRequest":
        if (
            self.acknowledgement
            != PRODUCTION_PILOT_PRE_ENABLE_SIGN_OFF_ACKNOWLEDGEMENT
        ):
            raise ValueError(
                "Production Pilot pre-enable acknowledgement is invalid"
            )
        return self


class ProductionPilotPreEnableSignOffResult(BaseModel):
    """Non-persistent operator proof bound to one authenticated identity."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    approval_id: str
    pilot_id: str
    operator_id: str
    evidence_sha256: str = Field(
        pattern=_SHA256_PATTERN,
    )
    acknowledgement: Literal[
        "I_CONFIRM_OOM_PILOT_PRE_ENABLE_EVIDENCE_V1"
    ] = PRODUCTION_PILOT_PRE_ENABLE_SIGN_OFF_ACKNOWLEDGEMENT
    sign_off_passed: Literal[True] = True
    sign_off_sha256: str = Field(
        pattern=_SHA256_PATTERN,
    )
    evidence: ProductionPilotPreEnableEvidencePack
    persisted: Literal[False] = False
    storage_write_count: Literal[0] = 0
    durable_claim_created: Literal[False] = False
    budget_reservation_count: Literal[0] = 0
    external_call_count: Literal[0] = 0
    kubernetes_call_count: Literal[0] = 0
    production_executor_call_count: Literal[0] = 0
    verification_call_count: Literal[0] = 0
    real_write_attempted: Literal[False] = False
    authorizes_enablement: Literal[False] = False
    authorizes_execution: Literal[False] = False
    exact_replay_safe: Literal[True] = True
    requires_live_recheck_before_resume: Literal[True] = True

    @model_validator(mode="after")
    def validate_binding(
        self,
    ) -> "ProductionPilotPreEnableSignOffResult":
        if (
            self.approval_id != self.evidence.approval_id
            or self.pilot_id != self.evidence.pilot_id
            or self.operator_id
            != self.evidence.executor_operator_id
            or self.evidence_sha256
            != self.evidence.evidence_sha256
            or not self.evidence.ready_for_sign_off
        ):
            raise ValueError(
                "Production Pilot pre-enable sign-off binding is invalid"
            )
        expected = _sign_off_digest(
            evidence_sha256=self.evidence_sha256,
            operator_id=self.operator_id,
            acknowledgement=self.acknowledgement,
        )
        if self.sign_off_sha256 != expected:
            raise ValueError(
                "Production Pilot pre-enable sign-off digest is invalid"
            )
        return self


class ProductionPilotPreEnableEvidenceService:
    """Assemble and sign bounded evidence without mutating durable state."""

    def __init__(
        self,
        *,
        readiness_service: ProductionPilotReadinessService,
        rehearsal_service: ProductionPilotRehearsalService,
        crash_rehearsal_service: (
            ProductionPilotCrashRecoveryRehearsalService
        ),
        ceremony_service: ProductionPilotCeremonyService,
        budget_service: ProductionPilotBudgetService,
        artifact_service: PreflightArtifactService,
        approval_service: ApprovalService,
        incident_store: IncidentStore,
        action_execution_service: ActionExecutionService,
        verification_service: VerificationService,
    ) -> None:
        dependencies = (
            (
                readiness_service,
                ProductionPilotReadinessService,
                "readiness service",
            ),
            (
                rehearsal_service,
                ProductionPilotRehearsalService,
                "rehearsal service",
            ),
            (
                crash_rehearsal_service,
                ProductionPilotCrashRecoveryRehearsalService,
                "crash rehearsal service",
            ),
            (
                ceremony_service,
                ProductionPilotCeremonyService,
                "Ceremony service",
            ),
            (
                budget_service,
                ProductionPilotBudgetService,
                "budget service",
            ),
            (
                artifact_service,
                PreflightArtifactService,
                "Artifact service",
            ),
            (
                approval_service,
                ApprovalService,
                "Approval service",
            ),
            (
                incident_store,
                IncidentStore,
                "Incident store",
            ),
            (
                action_execution_service,
                ActionExecutionService,
                "Action Execution service",
            ),
            (
                verification_service,
                VerificationService,
                "Verification service",
            ),
        )
        for value, expected, label in dependencies:
            if not isinstance(value, expected):
                raise TypeError(
                    f"Production Pilot pre-enable {label} is invalid"
                )

        self.readiness_service = readiness_service
        self.rehearsal_service = rehearsal_service
        self.crash_rehearsal_service = crash_rehearsal_service
        self.ceremony_service = ceremony_service
        self.budget_service = budget_service
        self.artifact_service = artifact_service
        self.approval_service = approval_service
        self.incident_store = incident_store
        self.action_execution_service = action_execution_service
        self.verification_service = verification_service

    async def get(
        self,
        approval_id: str,
    ) -> ProductionPilotPreEnableEvidencePack | None:
        _required_identifier(
            approval_id,
            "Approval ID",
        )
        ceremony = await self.ceremony_service.get_by_approval(
            approval_id
        )
        if ceremony is None:
            return None

        readiness = self.readiness_service.get()
        artifact = await self.artifact_service.get(
            ceremony.artifact_id
        )
        approval = await self.approval_service.get(
            approval_id
        )
        incident = await self.incident_store.get(
            str(ceremony.incident_id)
        )
        execution = (
            await self.action_execution_service.get_by_approval(
                approval_id
            )
        )
        verification = None
        if execution is not None:
            verification = (
                await self.verification_service.get_by_action_execution(
                    execution.id
                )
            )
        budget = await self.budget_service.get(
            ceremony.pilot_id
        )
        rehearsal = await self.rehearsal_service.run(
            operator_id=ceremony.executor_operator_id
        )
        crash_rehearsal = (
            await self.crash_rehearsal_service.run(
                operator_id=ceremony.executor_operator_id
            )
        )

        blockers: list[str] = []
        checked_at = readiness.checked_at
        control = self.ceremony_service.control
        config = control.config

        if not readiness.ready_for_enablement:
            blockers.append("pilot_not_ready_for_enablement")
        if readiness.kill_switch.state != "engaged":
            blockers.append("kill_switch_must_be_engaged")
        if readiness.production_execution_enabled:
            blockers.append("production_execution_must_remain_disabled")
        if readiness.production_executor_configured:
            blockers.append("production_executor_must_remain_unconfigured")
        if readiness.ready_for_execution:
            blockers.append("pilot_must_not_be_execution_ready")
        if not rehearsal.passed:
            blockers.append("zero_write_enablement_rehearsal_not_passed")
        if (
            not crash_rehearsal.passed
            or crash_rehearsal.checkpoint_count != 13
        ):
            blockers.append("crash_recovery_rehearsal_not_passed")

        artifact_state = "missing"
        contract_state = "missing"
        contract = None
        if artifact is None:
            blockers.append("preflight_artifact_missing")
        else:
            if (
                artifact.status
                == PreflightArtifactStatus.APPROVAL_BOUND
            ):
                artifact_state = "approval_bound"
            else:
                artifact_state = "invalid"
                blockers.append("preflight_artifact_not_approval_bound")
            contract = artifact.artifact.contract
            if checked_at < contract.prepared_at:
                contract_state = "clock_invalid"
                blockers.append("safety_contract_clock_invalid")
            elif contract.is_expired(checked_at):
                contract_state = "expired"
                blockers.append("safety_contract_expired")
            else:
                contract_state = "valid"

        approval_state = _enum_state(
            approval.status if approval is not None else None,
            missing="missing",
            allowed={
                "pending",
                "approved",
                "rejected",
                "expired",
            },
        )
        if approval_state != "approved":
            blockers.append("approval_not_approved")
        if (
            approval is None
            or approval.decision is None
        ):
            approval_operator_id = None
            blockers.append("approval_decision_evidence_missing")
        else:
            approval_operator_id = (
                approval.decision.operator_id
            )

        incident_state = _enum_state(
            incident.status if incident is not None else None,
            missing="missing",
            allowed={
                "new",
                "analyzing",
                "confirmed",
                "healing",
                "resolved",
                "failed",
            },
        )
        if incident_state != IncidentStatus.CONFIRMED.value:
            blockers.append("incident_not_confirmed_before_claim")

        ceremony_state = _enum_state(
            ceremony.status,
            missing="invalid",
            allowed={"ready", "activated"},
        )
        if ceremony_state != ProductionPilotCeremonyStatus.READY.value:
            blockers.append("ceremony_not_ready")
        if checked_at < ceremony.created_at:
            ceremony_clock_state = "clock_invalid"
            blockers.append("ceremony_clock_invalid")
        elif checked_at >= ceremony.expires_at:
            ceremony_clock_state = "expired"
            blockers.append("ceremony_expired")
        else:
            ceremony_clock_state = "valid"

        budget_state = _enum_state(
            budget.status if budget is not None else None,
            missing="available",
            allowed={"reserved", "consumed"},
        )
        if budget_state != "available":
            blockers.append("pilot_budget_not_available")

        execution_state = _enum_state(
            execution.status if execution is not None else None,
            missing="not_created",
            allowed={
                "running",
                "succeeded",
                "failed",
                "indeterminate",
            },
        )
        if execution_state != "not_created":
            blockers.append("action_execution_already_exists")

        verification_state = _enum_state(
            verification.status if verification is not None else None,
            missing="not_created",
            allowed={
                "pending",
                "running",
                "passed",
                "failed",
                "inconclusive",
                "timed_out",
            },
        )
        if verification_state != "not_created":
            blockers.append("verification_already_exists")

        executor_allowlisted = (
            ceremony.executor_operator_id
            in config.authorized_operator_ids
        )
        if not executor_allowlisted:
            blockers.append("executor_not_allowlisted")
        reviewer_separated = (
            ceremony.reviewer_operator_id
            != ceremony.executor_operator_id
        )
        if not reviewer_separated:
            blockers.append("ceremony_reviewer_executor_not_separated")
        approval_separated = (
            approval_operator_id is not None
            and approval_operator_id
            != ceremony.executor_operator_id
        )
        if not approval_separated:
            blockers.append("approval_executor_not_separated")

        bindings_consistent = self._bindings_consistent(
            ceremony=ceremony,
            artifact=artifact,
            approval=approval,
            incident=incident,
            contract=contract,
            checked_at=checked_at,
        )
        if not bindings_consistent:
            blockers.append("cross_store_binding_inconsistent")

        unique_blockers = tuple(
            dict.fromkeys(blockers)
        )
        values = self._evidence_values(
            ceremony=ceremony,
            artifact=artifact,
            contract=contract,
            approval_operator_id=approval_operator_id,
            artifact_state=artifact_state,
            approval_state=approval_state,
            incident_state=incident_state,
            ceremony_state=ceremony_state,
            budget_state=budget_state,
            execution_state=execution_state,
            verification_state=verification_state,
            contract_clock_state=contract_state,
            ceremony_clock_state=ceremony_clock_state,
            readiness=readiness,
            executor_allowlisted=executor_allowlisted,
            reviewer_separated=reviewer_separated,
            approval_separated=approval_separated,
            bindings_consistent=bindings_consistent,
            rehearsal=rehearsal,
            crash_rehearsal=crash_rehearsal,
            blockers=unique_blockers,
        )
        unsigned = ProductionPilotPreEnableEvidencePack.model_construct(
            **values,
            evidence_sha256="0" * 64,
        )
        digest = _digest_model(
            unsigned,
            excluded={"evidence_sha256"},
        )
        return ProductionPilotPreEnableEvidencePack(
            **values,
            evidence_sha256=digest,
        )

    async def sign_off(
        self,
        *,
        approval_id: str,
        operator_id: str,
        request: ProductionPilotPreEnableSignOffRequest,
    ) -> ProductionPilotPreEnableSignOffResult:
        _required_identifier(
            operator_id,
            "operator ID",
        )
        if not isinstance(
            request,
            ProductionPilotPreEnableSignOffRequest,
        ):
            raise TypeError(
                "Production Pilot pre-enable sign-off request is invalid"
            )
        evidence = await self.get(
            approval_id
        )
        if evidence is None:
            raise ProductionPilotPreEnableEvidenceError(
                "Production Pilot pre-enable evidence is unavailable"
            )
        if not evidence.ready_for_sign_off:
            raise ProductionPilotPreEnableEvidenceError(
                "Production Pilot pre-enable evidence is not ready"
            )
        if (
            request.expected_evidence_sha256
            != evidence.evidence_sha256
        ):
            raise ProductionPilotPreEnableEvidenceConflictError(
                "Production Pilot pre-enable evidence has changed"
            )
        if operator_id != evidence.executor_operator_id:
            raise ProductionPilotPreEnableEvidenceError(
                "Only the exact reviewed Executor may sign off"
            )

        return ProductionPilotPreEnableSignOffResult(
            approval_id=evidence.approval_id,
            pilot_id=evidence.pilot_id,
            operator_id=operator_id,
            evidence_sha256=evidence.evidence_sha256,
            sign_off_sha256=_sign_off_digest(
                evidence_sha256=evidence.evidence_sha256,
                operator_id=operator_id,
                acknowledgement=request.acknowledgement,
            ),
            evidence=evidence,
        )

    @staticmethod
    def _bindings_consistent(
        *,
        ceremony,
        artifact,
        approval,
        incident,
        contract,
        checked_at: datetime,
    ) -> bool:
        if any(
            value is None
            for value in (
                artifact,
                approval,
                incident,
                contract,
            )
        ):
            return False
        try:
            contract.require_executable_plan(
                approval.action,
                at=checked_at,
            )
        except Exception:
            return False
        return (
            artifact.approval_id == approval.id
            and approval.id == ceremony.approval_id
            and artifact.incident_id == approval.incident_id
            and approval.incident_id == ceremony.incident_id
            and incident.id == ceremony.incident_id
            and artifact.artifact_id == ceremony.artifact_id
            and contract.contract_id == ceremony.contract_id
            and contract.dry_run.patch_sha256
            == ceremony.patch_sha256
        )

    @staticmethod
    def _evidence_values(
        *,
        ceremony,
        artifact,
        contract,
        approval_operator_id,
        artifact_state,
        approval_state,
        incident_state,
        ceremony_state,
        budget_state,
        execution_state,
        verification_state,
        contract_clock_state,
        ceremony_clock_state,
        readiness,
        executor_allowlisted,
        reviewer_separated,
        approval_separated,
        bindings_consistent,
        rehearsal,
        crash_rehearsal,
        blockers,
    ) -> dict[str, Any]:
        scope = contract.scope if contract is not None else None
        memory = contract.memory if contract is not None else None
        return {
            "approval_id": ceremony.approval_id,
            "incident_id": str(ceremony.incident_id),
            "artifact_id": str(ceremony.artifact_id),
            "contract_id": str(ceremony.contract_id),
            "ceremony_id": str(ceremony.ceremony_id),
            "pilot_id": ceremony.pilot_id,
            "change_ticket": ceremony.change_ticket,
            "runbook_version": ceremony.runbook_version,
            "approval_decision_operator_id": approval_operator_id,
            "ceremony_reviewer_operator_id": (
                ceremony.reviewer_operator_id
            ),
            "executor_operator_id": ceremony.executor_operator_id,
            "cluster": scope.cluster if scope is not None else None,
            "namespace": scope.namespace if scope is not None else None,
            "workload_kind": (
                _enum_value(scope.kind)
                if scope is not None
                else None
            ),
            "workload_name": scope.name if scope is not None else None,
            "container": scope.container if scope is not None else None,
            "action_type": (
                _enum_value(contract.action_type)
                if contract is not None
                else None
            ),
            "current_memory_limit": (
                memory.current_limit if memory is not None else None
            ),
            "desired_memory_limit": (
                memory.desired_limit if memory is not None else None
            ),
            "rollback_memory_limit": (
                memory.rollback_limit if memory is not None else None
            ),
            "memory_increase_percent": (
                memory.increase_percent if memory is not None else None
            ),
            "safety_policy_version": (
                contract.policy_version if contract is not None else None
            ),
            "safety_patch_sha256": ceremony.patch_sha256,
            "artifact_state": artifact_state,
            "approval_state": approval_state,
            "incident_state": incident_state,
            "ceremony_state": ceremony_state,
            "budget_state": budget_state,
            "action_execution_state": execution_state,
            "verification_state": verification_state,
            "contract_clock_state": contract_clock_state,
            "ceremony_clock_state": ceremony_clock_state,
            "pilot_window_state": readiness.window_state,
            "kill_switch_state": readiness.kill_switch.state,
            "production_execution_enabled": (
                readiness.production_execution_enabled
            ),
            "production_executor_configured": (
                readiness.production_executor_configured
            ),
            "exact_single_target": readiness.exact_single_target,
            "credential_references_separate": (
                readiness.credential_references_separate
            ),
            "write_acknowledged": readiness.write_acknowledged,
            "runbook_acknowledged": readiness.runbook_acknowledged,
            "executor_allowlisted": executor_allowlisted,
            "reviewer_executor_separated": reviewer_separated,
            "approval_executor_separated": approval_separated,
            "bindings_consistent": bindings_consistent,
            "enablement_rehearsal_passed": rehearsal.passed,
            "crash_recovery_rehearsal_passed": (
                crash_rehearsal.passed
            ),
            "crash_recovery_checkpoint_count": (
                crash_rehearsal.checkpoint_count
            ),
            "crash_recovery_report_sha256": (
                crash_rehearsal.report_sha256
            ),
            "ready_for_sign_off": not blockers,
            "evidence_blockers": blockers,
        }


def _required_identifier(
    value: Any,
    label: str,
) -> str:
    if (
        not isinstance(value, str)
        or fullmatch(_IDENTIFIER_PATTERN, value) is None
    ):
        raise ValueError(
            f"Production Pilot pre-enable {label} is invalid"
        )
    return value


def _enum_value(value: Any) -> str:
    resolved = getattr(
        value,
        "value",
        value,
    )
    return str(resolved)


def _enum_state(
    value: Any,
    *,
    missing: str,
    allowed: set[str],
) -> str:
    if value is None:
        return missing
    resolved = _enum_value(value)
    return resolved if resolved in allowed else "invalid"


def _digest_mapping(
    values: dict[str, Any],
) -> str:
    canonical = json.dumps(
        values,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )
    return sha256(
        canonical.encode("utf-8")
    ).hexdigest()


def _digest_model(
    model: BaseModel,
    *,
    excluded: set[str],
) -> str:
    return _digest_mapping(
        model.model_dump(
            mode="json",
            exclude=excluded,
        )
    )


def _sign_off_digest(
    *,
    evidence_sha256: str,
    operator_id: str,
    acknowledgement: str,
) -> str:
    return _digest_mapping(
        {
            "schema_version": _SCHEMA_VERSION,
            "evidence_sha256": evidence_sha256,
            "operator_id": operator_id,
            "acknowledgement": acknowledgement,
        }
    )


__all__ = [
    "PRODUCTION_PILOT_PRE_ENABLE_SIGN_OFF_ACKNOWLEDGEMENT",
    "ProductionPilotPreEnableEvidenceConflictError",
    "ProductionPilotPreEnableEvidenceError",
    "ProductionPilotPreEnableEvidencePack",
    "ProductionPilotPreEnableEvidenceService",
    "ProductionPilotPreEnableSignOffRequest",
    "ProductionPilotPreEnableSignOffResult",
]
