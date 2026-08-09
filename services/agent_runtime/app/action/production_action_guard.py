from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

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
from services.agent_runtime.app.approval.store import (
    ApprovalConflictError,
)


class ProductionActionGuardError(
    ApprovalConflictError
):
    """Base fail-closed error for a prepared production Action."""


class ProductionActionContractExpiredError(
    ProductionActionGuardError
):
    """The immutable Safety Contract is no longer valid."""


class ProductionActionClockError(
    ProductionActionGuardError
):
    """The trusted clock is earlier than the preparation timestamp."""


class ProductionActionBindingError(
    ProductionActionGuardError
):
    """Artifact, Approval, Incident, Action, or digest binding is invalid."""


@dataclass(
    frozen=True,
    slots=True,
)
class ProductionActionGuardSnapshot:
    """Successful guard evaluation returned to the execution boundary."""

    record: PreflightArtifactRecord
    checked_at: datetime
    remaining_seconds: int


class ProductionActionExpiryGuard:
    """
    Enforce an immutable Preflight Safety Contract at write boundaries.

    Generic Approvals without production-preparation metadata remain
    compatible. A request that claims a production preparation always fails
    closed when its Artifact is missing, inconsistent, clock-invalid, or
    expired. Rejection remains permitted because it cannot authorize an
    Action.
    """

    def __init__(
        self,
        *,
        artifact_service: PreflightArtifactService,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(
            artifact_service,
            PreflightArtifactService,
        ):
            raise TypeError(
                "Production Action guard Artifact service is invalid"
            )

        self.artifact_service = artifact_service
        self._clock = clock or (
            lambda: datetime.now(
                UTC
            )
        )

    async def require_transition(
        self,
        request: ApprovalRequest,
        target_status: ApprovalStatus,
    ) -> None:
        if target_status != ApprovalStatus.APPROVED:
            return

        await self.require_active(
            request
        )

    async def require_resume(
        self,
        request: ApprovalRequest,
    ) -> ProductionActionGuardSnapshot | None:
        return await self.require_active(
            request
        )

    async def require_active(
        self,
        request: ApprovalRequest,
    ) -> ProductionActionGuardSnapshot | None:
        if not isinstance(
            request,
            ApprovalRequest,
        ):
            raise TypeError(
                "Production Action guard Approval request is invalid"
            )

        record = (
            await self.artifact_service.get_by_approval_id(
                request.id
            )
        )

        if record is None:
            if self._claims_preflight(
                request
            ):
                raise ProductionActionBindingError(
                    "Production preparation Artifact is unavailable"
                )

            return None

        self._require_binding(
            record=record,
            request=request,
        )

        checked_at = self._now()
        contract = record.artifact.contract

        if checked_at < contract.prepared_at:
            raise ProductionActionClockError(
                "Production preparation clock is invalid"
            )

        if contract.is_expired(
            checked_at
        ):
            raise ProductionActionContractExpiredError(
                "Production preparation Safety Contract has expired"
            )

        remaining_seconds = max(
            0,
            int(
                (
                    contract.expires_at
                    - checked_at
                ).total_seconds()
            ),
        )

        return ProductionActionGuardSnapshot(
            record=record,
            checked_at=checked_at,
            remaining_seconds=remaining_seconds,
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
            raise ProductionActionClockError(
                "Production preparation clock must be timezone-aware"
            )

        return value.astimezone(
            UTC
        )

    @classmethod
    def _require_binding(
        cls,
        *,
        record: PreflightArtifactRecord,
        request: ApprovalRequest,
    ) -> None:
        if (
            record.status
            != PreflightArtifactStatus.APPROVAL_BOUND
            or record.approval_id != request.id
            or request.incident_id != record.incident_id
            or not cls._same_action(
                record,
                request,
            )
        ):
            raise ProductionActionBindingError(
                "Production preparation binding is inconsistent"
            )

        expected_metadata = {
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

        if any(
            request.metadata.get(
                key
            ) != expected
            for key, expected
            in expected_metadata.items()
        ):
            raise ProductionActionBindingError(
                "Production preparation metadata is inconsistent"
            )

    @staticmethod
    def _same_action(
        record: PreflightArtifactRecord,
        request: ApprovalRequest,
    ) -> bool:
        prepared = (
            record.artifact.plan.model_dump(
                mode="json"
            )
        )
        approved = (
            request.action.model_dump(
                mode="json"
            )
        )
        prepared.pop(
            "approved",
            None,
        )
        approved.pop(
            "approved",
            None,
        )
        return prepared == approved

    @staticmethod
    def _claims_preflight(
        request: ApprovalRequest,
    ) -> bool:
        metadata = request.metadata
        return (
            metadata.get("source")
            == "production_action_preparation"
            or any(
                key in metadata
                for key in (
                    "preflight_artifact_id",
                    "preflight_idempotency_key",
                    "safety_contract_id",
                    "safety_patch_sha256",
                )
            )
        )


__all__ = [
    "ProductionActionBindingError",
    "ProductionActionClockError",
    "ProductionActionContractExpiredError",
    "ProductionActionExpiryGuard",
    "ProductionActionGuardError",
    "ProductionActionGuardSnapshot",
]
