from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from math import ceil
from uuid import UUID

from services.agent_runtime.app.action.preflight_artifact_models import (
    PreflightArtifactRecord,
    PreflightArtifactStatus,
)
from services.agent_runtime.app.action.preflight_artifact_service import (
    PreflightArtifactService,
)
from services.agent_runtime.app.approval.models import (
    ApprovalRequest,
    ApprovalStatus,
)
from services.agent_runtime.app.approval.service import (
    ApprovalService,
)
from services.agent_runtime.app.incident.enums import (
    IncidentStatus,
)
from services.agent_runtime.app.incident.state import (
    IncidentState,
)
from services.agent_runtime.app.incident.store import (
    IncidentStore,
)


@dataclass(
    frozen=True,
    slots=True,
)
class ProductionActionQueryResult:
    """Read-only aggregate for one persisted production preparation."""

    record: PreflightArtifactRecord
    approval: ApprovalRequest | None
    incident: IncidentState | None
    checked_at: datetime
    clock_valid: bool
    expired: bool
    remaining_seconds: int
    consistency_issues: tuple[str, ...]
    execution_blockers: tuple[str, ...]
    phase: str

    @property
    def consistency_passed(
        self,
    ) -> bool:
        return not self.consistency_issues

    @property
    def execution_eligible(
        self,
    ) -> bool:
        return not self.execution_blockers

    @property
    def approval_decision_required(
        self,
    ) -> bool:
        return (
            self.consistency_passed
            and self.clock_valid
            and not self.expired
            and self.approval is not None
            and self.approval.status
            == ApprovalStatus.PENDING
        )

    @property
    def replacement_preflight_required(
        self,
    ) -> bool:
        return self.expired


class ProductionActionQueryService:
    """
    Build one side-effect-free Artifact, Approval and Incident snapshot.

    The service performs only indexed reads. It does not contact Kubernetes,
    alter Approval or Incident state, execute an Action, or start Verification.
    """

    def __init__(
        self,
        *,
        artifact_service: PreflightArtifactService,
        approval_service: ApprovalService,
        incident_store: IncidentStore,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(
            artifact_service,
            PreflightArtifactService,
        ):
            raise TypeError(
                "Production Action query Artifact service is invalid"
            )
        if not isinstance(
            approval_service,
            ApprovalService,
        ):
            raise TypeError(
                "Production Action query Approval service is invalid"
            )
        if not isinstance(
            incident_store,
            IncidentStore,
        ):
            raise TypeError(
                "Production Action query Incident store is invalid"
            )

        self.artifact_service = artifact_service
        self.approval_service = approval_service
        self.incident_store = incident_store
        self._clock = clock or (
            lambda: datetime.now(
                UTC
            )
        )

    async def get(
        self,
        artifact_id: UUID | str,
    ) -> ProductionActionQueryResult | None:
        record = await self.artifact_service.get(
            artifact_id
        )
        if record is None:
            return None

        approval = None
        if record.approval_id is not None:
            approval = await self.approval_service.get(
                record.approval_id
            )

        incident = await self.incident_store.get(
            str(
                record.incident_id
            )
        )
        checked_at = self._now()
        contract = record.artifact.contract
        clock_valid = (
            checked_at
            >= contract.prepared_at
        )
        expired = contract.is_expired(
            checked_at
        )
        remaining_seconds = (
            max(
                0,
                ceil(
                    (
                        contract.expires_at
                        - checked_at
                    ).total_seconds()
                ),
            )
            if clock_valid
            else 0
        )
        issues = self._consistency_issues(
            record=record,
            approval=approval,
            incident=incident,
        )
        blockers = self._execution_blockers(
            record=record,
            approval=approval,
            incident=incident,
            expired=expired,
            clock_valid=clock_valid,
            consistency_issues=issues,
        )
        phase = self._phase(
            record=record,
            approval=approval,
            expired=expired,
            clock_valid=clock_valid,
            consistency_issues=issues,
            execution_blockers=blockers,
        )

        return ProductionActionQueryResult(
            record=record,
            approval=approval,
            incident=incident,
            checked_at=checked_at,
            clock_valid=clock_valid,
            expired=expired,
            remaining_seconds=remaining_seconds,
            consistency_issues=issues,
            execution_blockers=blockers,
            phase=phase,
        )

    def _now(
        self,
    ) -> datetime:
        value = self._clock()
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise ValueError(
                "Production Action query clock must return an aware datetime"
            )
        return value.astimezone(
            UTC
        )

    @classmethod
    def _consistency_issues(
        cls,
        *,
        record: PreflightArtifactRecord,
        approval: ApprovalRequest | None,
        incident: IncidentState | None,
    ) -> tuple[str, ...]:
        issues: list[str] = []

        if (
            record.status
            != PreflightArtifactStatus.APPROVAL_BOUND
        ):
            issues.append(
                "artifact_approval_binding_incomplete"
            )

        if record.approval_id is None:
            issues.append(
                "artifact_missing_approval_id"
            )
        elif approval is None:
            issues.append(
                "linked_approval_not_found"
            )

        if approval is not None:
            if approval.id != record.approval_id:
                issues.append(
                    "approval_id_mismatch"
                )

            if approval.incident_id != record.incident_id:
                issues.append(
                    "approval_incident_link_mismatch"
                )

            if not cls._same_action(
                record,
                approval,
            ):
                issues.append(
                    "approval_action_mismatch"
                )

            expected_approved = (
                approval.status
                == ApprovalStatus.APPROVED
            )
            if (
                approval.action.approved
                is not expected_approved
            ):
                issues.append(
                    "approval_action_flag_mismatch"
                )

            expected_metadata = (
                cls._expected_approval_metadata(
                    record
                )
            )
            if any(
                approval.metadata.get(
                    key
                ) != expected
                for key, expected
                in expected_metadata.items()
            ):
                issues.append(
                    "approval_artifact_metadata_mismatch"
                )

        if incident is None:
            issues.append(
                "linked_incident_not_found"
            )
        elif incident.id != record.incident_id:
            issues.append(
                "incident_id_mismatch"
            )

        return tuple(
            issues
        )

    @staticmethod
    def _same_action(
        record: PreflightArtifactRecord,
        approval: ApprovalRequest,
    ) -> bool:
        artifact_action = (
            record.artifact.plan.model_dump(
                mode="json"
            )
        )
        approval_action = (
            approval.action.model_dump(
                mode="json"
            )
        )
        artifact_action.pop(
            "approved",
            None,
        )
        approval_action.pop(
            "approved",
            None,
        )
        return (
            artifact_action
            == approval_action
        )

    @staticmethod
    def _expected_approval_metadata(
        record: PreflightArtifactRecord,
    ) -> dict[str, str]:
        return {
            "source": (
                "production_action_preparation"
            ),
            "preflight_artifact_id": str(
                record.artifact_id
            ),
            "preflight_idempotency_key": (
                record.idempotency_key
            ),
            "safety_contract_id": str(
                record.artifact.contract.contract_id
            ),
            "safety_patch_sha256": (
                record.artifact.contract
                .dry_run.patch_sha256
            ),
        }

    @staticmethod
    def _execution_blockers(
        *,
        record: PreflightArtifactRecord,
        approval: ApprovalRequest | None,
        incident: IncidentState | None,
        expired: bool,
        clock_valid: bool,
        consistency_issues: tuple[str, ...],
    ) -> tuple[str, ...]:
        blockers: list[str] = []

        if consistency_issues:
            blockers.append(
                "inconsistent_preparation_state"
            )

        if expired:
            blockers.append(
                "safety_contract_expired"
            )

        if not clock_valid:
            blockers.append(
                "query_clock_invalid"
            )

        if (
            record.status
            != PreflightArtifactStatus.APPROVAL_BOUND
        ):
            blockers.append(
                "approval_binding_incomplete"
            )

        if approval is None:
            blockers.append(
                "approval_unavailable"
            )
        elif approval.status == ApprovalStatus.PENDING:
            blockers.append(
                "approval_pending"
            )
        elif approval.status == ApprovalStatus.REJECTED:
            blockers.append(
                "approval_rejected"
            )
        elif approval.status == ApprovalStatus.EXPIRED:
            blockers.append(
                "approval_expired"
            )
        elif approval.status != ApprovalStatus.APPROVED:
            blockers.append(
                "approval_status_unknown"
            )

        if incident is None:
            blockers.append(
                "incident_unavailable"
            )
        elif incident.status != IncidentStatus.CONFIRMED:
            blockers.append(
                "incident_not_confirmed"
            )

        return tuple(
            blockers
        )

    @staticmethod
    def _phase(
        *,
        record: PreflightArtifactRecord,
        approval: ApprovalRequest | None,
        expired: bool,
        clock_valid: bool,
        consistency_issues: tuple[str, ...],
        execution_blockers: tuple[str, ...],
    ) -> str:
        if not clock_valid:
            return "clock_invalid"

        if (
            record.status
            == PreflightArtifactStatus.PREPARED
        ):
            return "approval_binding_incomplete"

        if consistency_issues:
            return "inconsistent"

        if approval is None:
            return "approval_unavailable"

        if approval.status == ApprovalStatus.REJECTED:
            return "rejected"

        if approval.status == ApprovalStatus.EXPIRED:
            return "approval_expired"

        if expired:
            if approval.status == ApprovalStatus.PENDING:
                return "expired_pending_approval"
            if approval.status == ApprovalStatus.APPROVED:
                return "approved_contract_expired"
            return "contract_expired"

        if approval.status == ApprovalStatus.PENDING:
            return "pending_approval"

        if approval.status == ApprovalStatus.APPROVED:
            return (
                "ready_for_execution"
                if not execution_blockers
                else "approved_but_blocked"
            )

        return "unknown"


__all__ = [
    "ProductionActionQueryResult",
    "ProductionActionQueryService",
]
