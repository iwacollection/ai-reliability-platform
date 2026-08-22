from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from services.agent_runtime.app.action.kubernetes_preflight import (
    KubernetesPreflightResolver,
)
from services.agent_runtime.app.action.preflight_artifact_models import (
    PreflightArtifactRecord,
    ProductionActionPreparationRequest,
    ProductionActionPreparationResult,
)
from services.agent_runtime.app.action.preflight_artifact_service import (
    PreflightArtifactService,
)
from services.agent_runtime.app.action.preflight_artifact_store import (
    PreflightArtifactConflictError,
)
from services.agent_runtime.app.approval.models import (
    ApprovalRequest,
    ApprovalStatus,
)
from services.agent_runtime.app.approval.service import (
    ApprovalService,
)
from services.agent_runtime.app.approval.store import (
    ApprovalConflictError,
)


class ProductionActionPreparationError(RuntimeError):
    """Trusted production Action preparation could not complete safely."""


class ProductionActionPreparationConflictError(
    ProductionActionPreparationError
):
    """A replay conflicts with the persisted Artifact or Approval binding."""


class ProductionActionPreparationService:
    """
    Persist trusted Kubernetes preflight before creating human Approval.

    Approval IDs are deterministic for one Artifact. If a process stops after
    ApprovalStore commits but before the Artifact binding CAS, replay finds the
    same Approval and completes the binding. No Action is executed here.
    """

    def __init__(
        self,
        *,
        resolver: KubernetesPreflightResolver,
        artifact_service: PreflightArtifactService,
        approval_service: ApprovalService,
    ) -> None:
        if not isinstance(resolver, KubernetesPreflightResolver):
            raise TypeError("Production preparation resolver is invalid")
        if not isinstance(artifact_service, PreflightArtifactService):
            raise TypeError("Production preparation Artifact service is invalid")
        if not isinstance(approval_service, ApprovalService):
            raise TypeError("Production preparation Approval service is invalid")

        self.resolver = resolver
        self.artifact_service = artifact_service
        self.approval_service = approval_service

    async def prepare(
        self,
        request: ProductionActionPreparationRequest,
        *,
        operator_id: str | None = None,
    ) -> ProductionActionPreparationResult:
        if not isinstance(request, ProductionActionPreparationRequest):
            raise TypeError(
                "Production preparation requires a validated request"
            )

        normalized_operator_id = self._operator_id(
            operator_id
        )

        existing = await self.artifact_service.get_by_request(
            request.preflight.incident_id,
            request.idempotency_key,
        )
        if existing is not None:
            self.artifact_service.require_matches_request(
                existing,
                request.preflight,
            )
            return await self._ensure_approval(
                record=existing,
                request=request,
                artifact_created=False,
                operator_id=normalized_operator_id,
            )

        artifact = await self.resolver.prepare(request.preflight)
        claim = await self.artifact_service.claim_prepared(
            artifact,
            request.idempotency_key,
        )
        self.artifact_service.require_matches_request(
            claim.record,
            request.preflight,
        )
        return await self._ensure_approval(
            record=claim.record,
            request=request,
            artifact_created=claim.created,
            operator_id=normalized_operator_id,
        )

    async def _ensure_approval(
        self,
        *,
        record: PreflightArtifactRecord,
        request: ProductionActionPreparationRequest,
        artifact_created: bool,
        operator_id: str | None,
    ) -> ProductionActionPreparationResult:
        approval_id = self._approval_id(record)
        approval = await self.approval_service.get(approval_id)
        approval_created = False

        if approval is None:
            try:
                approval = await self.approval_service.create_approval(
                    action=record.artifact.plan,
                    reason=request.approval_reason,
                    incident_id=record.incident_id,
                    request_id=approval_id,
                    metadata=self._approval_metadata(
                        record,
                        operator_id=operator_id,
                    ),
                )
                approval_created = True
            except ApprovalConflictError:
                approval = await self.approval_service.get(approval_id)
                if approval is None:
                    raise ProductionActionPreparationConflictError(
                        "Deterministic Approval conflict cannot be recovered"
                    ) from None

        self._require_matching_approval(
            record=record,
            request=request,
            approval=approval,
            operator_id=operator_id,
        )

        try:
            bound = await self.artifact_service.bind_approval(
                record,
                approval.id,
                updated_at=datetime.now(UTC),
            )
        except PreflightArtifactConflictError as exc:
            current = await self.artifact_service.get(record.artifact_id)
            if current is None or current.approval_id != approval.id:
                raise ProductionActionPreparationConflictError(
                    "Preflight Artifact Approval binding conflict"
                ) from exc
            bound = current

        return ProductionActionPreparationResult(
            record=bound,
            approval=approval,
            artifact_created=artifact_created,
            approval_created=approval_created,
        )

    @staticmethod
    def _approval_id(record: PreflightArtifactRecord) -> str:
        return str(
            uuid5(
                NAMESPACE_URL,
                "ai-reliability-platform:preflight-approval:"
                f"{record.artifact_id}",
            )
        )

    @staticmethod
    def _approval_metadata(
        record: PreflightArtifactRecord,
        *,
        operator_id: str | None = None,
    ) -> dict[str, str]:
        metadata = {
            "source": "production_action_preparation",
            "preflight_artifact_id": str(record.artifact_id),
            "preflight_idempotency_key": record.idempotency_key,
            "safety_contract_id": str(record.artifact.contract.contract_id),
            "safety_patch_sha256": record.artifact.contract.dry_run.patch_sha256,
        }

        if operator_id is not None:
            metadata[
                "preparation_operator_id"
            ] = operator_id

        return metadata

    @classmethod
    def _require_matching_approval(
        cls,
        *,
        record: PreflightArtifactRecord,
        request: ProductionActionPreparationRequest,
        approval: ApprovalRequest,
        operator_id: str | None = None,
    ) -> None:
        if not isinstance(approval, ApprovalRequest):
            raise ProductionActionPreparationConflictError(
                "Production preparation Approval is invalid"
            )

        expected_metadata = cls._approval_metadata(
            record,
            operator_id=operator_id,
        )
        approval_action = approval.action.model_dump(mode="json")
        artifact_action = record.artifact.plan.model_dump(mode="json")
        approval_action.pop("approved", None)
        artifact_action.pop("approved", None)
        expected_approved = approval.status == ApprovalStatus.APPROVED
        matches = (
            approval.id == cls._approval_id(record)
            and approval.incident_id == record.incident_id
            and approval_action == artifact_action
            and approval.action.approved is expected_approved
            and approval.reason == request.approval_reason
            and all(
                approval.metadata.get(key) == value
                for key, value in expected_metadata.items()
            )
        )
        if not matches:
            raise ProductionActionPreparationConflictError(
                "Persisted Approval does not match the Preflight Artifact"
            )

    @staticmethod
    def _operator_id(
        value: str | None,
    ) -> str | None:
        if value is None:
            return None

        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or len(value) > 128
        ):
            raise ValueError(
                "Production preparation operator ID is invalid"
            )

        return value


__all__ = [
    "ProductionActionPreparationConflictError",
    "ProductionActionPreparationError",
    "ProductionActionPreparationService",
]
