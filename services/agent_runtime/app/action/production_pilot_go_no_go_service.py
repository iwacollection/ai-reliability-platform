from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid5

from services.agent_runtime.app.action.preflight_artifact_service import (
    PreflightArtifactService,
)
from services.agent_runtime.app.action.production_pilot import (
    KubernetesProductionPilotControl,
)
from services.agent_runtime.app.action.production_pilot_final_handoff import (
    ProductionPilotFinalHandoffConflictError,
    ProductionPilotFinalHandoffError,
    ProductionPilotFinalHandoffRehearsalService,
)
from services.agent_runtime.app.action.production_pilot_go_no_go_models import (
    PRODUCTION_PILOT_LIVE_PROBE_ACKNOWLEDGEMENT,
    ProductionPilotGoNoGoDecision,
    ProductionPilotGoNoGoRecord,
    ProductionPilotGoNoGoRequest,
    ProductionPilotLiveProbeRecord,
    ProductionPilotLiveProbeRequest,
    ProductionPilotLiveProbeStatus,
    aware_utc,
    digest_mapping,
    digest_model,
    required_identifier,
)
from services.agent_runtime.app.action.production_pilot_go_no_go_store import (
    ProductionPilotGoNoGoClaimResult,
    ProductionPilotGoNoGoConflictError,
    ProductionPilotGoNoGoStore,
    ProductionPilotLiveProbeClaimResult,
)
from services.agent_runtime.app.action.production_pilot_live_probe import (
    ProductionPilotLiveProbeError,
    ProductionPilotLiveReadinessProbe,
)


class ProductionPilotGoNoGoError(RuntimeError):
    """The final live readiness workflow failed closed."""


class ProductionPilotGoNoGoStaleEvidenceError(
    ProductionPilotGoNoGoError
):
    """The live workflow request no longer matches current evidence."""


@dataclass(frozen=True, slots=True)
class ProductionPilotLiveProbeRunResult:
    record: ProductionPilotLiveProbeRecord
    claim_created: bool
    live_probe_executed: bool

    @property
    def idempotent_replay(self) -> bool:
        return not self.claim_created


class ProductionPilotGoNoGoService:
    """Coordinate one read-only live probe and one independent decision."""

    _PROBE_NAMESPACE = UUID(
        "e37808ee-e643-5b74-93a4-2c1857c75d17"
    )
    _DECISION_NAMESPACE = UUID(
        "bff872e0-c45d-53c7-b43d-c7e3d17167a3"
    )

    def __init__(
        self,
        *,
        store: ProductionPilotGoNoGoStore,
        live_probe: ProductionPilotLiveReadinessProbe,
        final_handoff_service: (
            ProductionPilotFinalHandoffRehearsalService
        ),
        artifact_service: PreflightArtifactService,
        pilot_control: KubernetesProductionPilotControl,
        clock: Callable[[], datetime] | None = None,
        go_ttl_seconds: int = 300,
    ) -> None:
        if not isinstance(store, ProductionPilotGoNoGoStore):
            raise TypeError(
                "Production Pilot Go/No-Go store is invalid"
            )
        if not isinstance(
            live_probe,
            ProductionPilotLiveReadinessProbe,
        ):
            raise TypeError(
                "Production Pilot live readiness probe is invalid"
            )
        if not isinstance(
            final_handoff_service,
            ProductionPilotFinalHandoffRehearsalService,
        ):
            raise TypeError(
                "Production Pilot final handoff service is invalid"
            )
        if not isinstance(artifact_service, PreflightArtifactService):
            raise TypeError(
                "Production Pilot Artifact service is invalid"
            )
        if not isinstance(
            pilot_control,
            KubernetesProductionPilotControl,
        ):
            raise TypeError(
                "Production Pilot control is invalid"
            )
        if (
            isinstance(go_ttl_seconds, bool)
            or not isinstance(go_ttl_seconds, int)
            or go_ttl_seconds < 30
            or go_ttl_seconds > 300
        ):
            raise ValueError(
                "Production Pilot GO decision TTL is invalid"
            )
        self.store = store
        self.live_probe = live_probe
        self.final_handoff_service = final_handoff_service
        self.artifact_service = artifact_service
        self.pilot_control = pilot_control
        self._clock = clock or (lambda: datetime.now(UTC))
        self.go_ttl_seconds = go_ttl_seconds

    async def run_live_probe(
        self,
        *,
        approval_id: str,
        operator_id: str,
        idempotency_key: str,
        request: ProductionPilotLiveProbeRequest,
    ) -> ProductionPilotLiveProbeRunResult:
        required_identifier(
            approval_id,
            label="live probe Approval ID",
        )
        required_identifier(
            operator_id,
            label="live probe operator ID",
        )
        required_identifier(
            idempotency_key,
            label="live probe idempotency key",
        )
        if not isinstance(request, ProductionPilotLiveProbeRequest):
            raise TypeError(
                "Production Pilot live probe request is invalid"
            )

        handoff = await self._load_handoff(
            approval_id=approval_id,
            operator_id=operator_id,
            request=request,
        )
        if not handoff.passed:
            raise ProductionPilotGoNoGoError(
                "Production Pilot final handoff is blocked"
            )
        if (
            handoff.report_sha256
            != request.expected_handoff_report_sha256
        ):
            raise ProductionPilotGoNoGoStaleEvidenceError(
                "Production Pilot final handoff report changed"
            )
        if operator_id != handoff.executor_operator_id:
            raise ProductionPilotGoNoGoError(
                "Only the exact reviewed Executor may run live probe"
            )
        artifact = await self.artifact_service.get_by_approval_id(
            approval_id
        )
        if artifact is None:
            raise ProductionPilotGoNoGoError(
                "Production Pilot live probe Artifact is unavailable"
            )
        if (
            str(artifact.artifact_id) != handoff.artifact_id
            or str(artifact.incident_id) != handoff.incident_id
        ):
            raise ProductionPilotGoNoGoStaleEvidenceError(
                "Production Pilot live probe Artifact binding changed"
            )
        now = self._now()
        expires_at = min(
            artifact.artifact.contract.expires_at,
            self.pilot_control.config.expires_at,
            now + timedelta(seconds=self.go_ttl_seconds),
        )
        if expires_at <= now:
            raise ProductionPilotGoNoGoError(
                "Production Pilot live probe evidence has expired"
            )
        request_sha256 = digest_mapping(
            {
                "approval_id": approval_id,
                "operator_id": operator_id,
                "idempotency_key": idempotency_key,
                "request": request.model_dump(mode="json"),
            }
        )
        probe_id = uuid5(
            self._PROBE_NAMESPACE,
            approval_id
            + ":"
            + handoff.report_sha256
            + ":"
            + operator_id,
        )
        values: dict[str, Any] = {
            "probe_id": probe_id,
            "approval_id": approval_id,
            "incident_id": UUID(handoff.incident_id),
            "artifact_id": UUID(handoff.artifact_id),
            "ceremony_id": UUID(handoff.ceremony_id),
            "pilot_id": handoff.pilot_id,
            "change_ticket": handoff.change_ticket,
            "runbook_version": handoff.runbook_version,
            "executor_operator_id": operator_id,
            "idempotency_key": idempotency_key,
            "request_sha256": request_sha256,
            "evidence_sha256": handoff.evidence_sha256,
            "handoff_report_sha256": handoff.report_sha256,
            "configuration_sha256": handoff.configuration_sha256,
            "deployment_release_sha256": (
                handoff.deployment_release_sha256
            ),
            "handoff_request": request.handoff,
            "status": ProductionPilotLiveProbeStatus.RUNNING,
            "started_at": now,
            "updated_at": now,
            "expires_at": expires_at,
        }
        candidate = self._build_probe_record(values)
        claim = await self.store.claim_probe(candidate)
        if not claim.created:
            return ProductionPilotLiveProbeRunResult(
                record=claim.record,
                claim_created=False,
                live_probe_executed=False,
            )

        try:
            result = await self.live_probe.probe(artifact)
            completed_at = self._now()
            post_probe_blocker = None
            if completed_at >= expires_at:
                post_probe_blocker = "live_probe_expired_during_request"
            else:
                try:
                    current_handoff = await self._load_handoff(
                        approval_id=approval_id,
                        operator_id=operator_id,
                        request=request,
                    )
                    if (
                        not current_handoff.passed
                        or current_handoff.report_sha256
                        != handoff.report_sha256
                    ):
                        post_probe_blocker = (
                            "handoff_changed_after_live_probe"
                        )
                except ProductionPilotGoNoGoError:
                    post_probe_blocker = (
                        "handoff_changed_after_live_probe"
                    )

            if post_probe_blocker is None:
                completed = self._build_probe_record(
                    {
                        **values,
                        "status": ProductionPilotLiveProbeStatus.PASSED,
                        "updated_at": completed_at,
                        "completed_at": completed_at,
                        "preflight_credential_authenticated": True,
                        "production_credential_authenticated": True,
                        "tls_verified": True,
                        "target_state_consistent": True,
                        "live_resource_sha256": (
                            result.live_resource_sha256
                        ),
                        "network_call_count": (
                            result.network_call_count
                        ),
                        "kubernetes_read_count": (
                            result.network_call_count
                        ),
                    }
                )
            else:
                completed = self._build_probe_record(
                    {
                        **values,
                        "status": ProductionPilotLiveProbeStatus.FAILED,
                        "updated_at": completed_at,
                        "completed_at": completed_at,
                        "blocker_code": post_probe_blocker,
                        "network_call_count": (
                            result.network_call_count
                        ),
                        "kubernetes_read_count": (
                            result.network_call_count
                        ),
                    }
                )
        except ProductionPilotLiveProbeError as exc:
            completed_at = self._now()
            completed = self._build_probe_record(
                {
                    **values,
                    "status": ProductionPilotLiveProbeStatus.FAILED,
                    "updated_at": completed_at,
                    "completed_at": completed_at,
                    "blocker_code": exc.blocker_code,
                    "network_call_count": exc.calls_made,
                    "kubernetes_read_count": exc.calls_made,
                }
            )
        completion = await self.store.complete_probe(completed)
        return ProductionPilotLiveProbeRunResult(
            record=completion.record,
            claim_created=True,
            live_probe_executed=True,
        )

    async def decide(
        self,
        *,
        approval_id: str,
        reviewer_operator_id: str,
        idempotency_key: str,
        request: ProductionPilotGoNoGoRequest,
    ) -> ProductionPilotGoNoGoClaimResult:
        required_identifier(
            approval_id,
            label="Go/No-Go Approval ID",
        )
        required_identifier(
            reviewer_operator_id,
            label="Go/No-Go reviewer ID",
        )
        required_identifier(
            idempotency_key,
            label="Go/No-Go idempotency key",
        )
        if not isinstance(request, ProductionPilotGoNoGoRequest):
            raise TypeError(
                "Production Pilot Go/No-Go request is invalid"
            )
        probe = await self.store.get_probe_by_approval(
            approval_id
        )
        if probe is None:
            raise ProductionPilotGoNoGoError(
                "Production Pilot live probe was not found"
            )
        if (
            request.expected_probe_record_sha256
            != probe.record_sha256
        ):
            raise ProductionPilotGoNoGoStaleEvidenceError(
                "Production Pilot live probe record changed"
            )
        forbidden_reviewers = {
            probe.executor_operator_id,
            probe.handoff_request.on_call_owner_id,
            probe.handoff_request.rollback_owner_id,
            probe.handoff_request.reconciliation_owner_id,
        }
        if reviewer_operator_id in forbidden_reviewers:
            raise ProductionPilotGoNoGoError(
                "Production Pilot reviewer duties are not separated"
            )

        is_go = request.decision == ProductionPilotGoNoGoDecision.GO.value
        now = self._now()
        if is_go:
            if probe.status != ProductionPilotLiveProbeStatus.PASSED.value:
                raise ProductionPilotGoNoGoError(
                    "Production Pilot GO requires a passed live probe"
                )
            if now >= probe.expires_at:
                raise ProductionPilotGoNoGoError(
                    "Production Pilot live probe has expired"
                )
            handoff_request = ProductionPilotLiveProbeRequest(
                expected_handoff_report_sha256=(
                    probe.handoff_report_sha256
                ),
                handoff=probe.handoff_request,
                acknowledgement=(
                    PRODUCTION_PILOT_LIVE_PROBE_ACKNOWLEDGEMENT
                ),
            )
            current_handoff = await self._load_handoff(
                approval_id=approval_id,
                operator_id=probe.executor_operator_id,
                request=handoff_request,
            )
            if (
                not current_handoff.passed
                or current_handoff.report_sha256
                != probe.handoff_report_sha256
            ):
                raise ProductionPilotGoNoGoStaleEvidenceError(
                    "Production Pilot handoff changed after live probe"
                )
            separated = {
                current_handoff.approval_operator_id,
                current_handoff.ceremony_reviewer_operator_id,
            }
            if reviewer_operator_id in separated:
                raise ProductionPilotGoNoGoError(
                    "Production Pilot final reviewer must be independent"
                )

        request_sha256 = digest_mapping(
            {
                "approval_id": approval_id,
                "reviewer_operator_id": reviewer_operator_id,
                "idempotency_key": idempotency_key,
                "request": request.model_dump(mode="json"),
            }
        )
        decision_id = uuid5(
            self._DECISION_NAMESPACE,
            str(probe.probe_id)
            + ":"
            + reviewer_operator_id,
        )
        values = {
            "decision_id": decision_id,
            "probe_id": probe.probe_id,
            "approval_id": probe.approval_id,
            "incident_id": probe.incident_id,
            "artifact_id": probe.artifact_id,
            "ceremony_id": probe.ceremony_id,
            "pilot_id": probe.pilot_id,
            "change_ticket": probe.change_ticket,
            "runbook_version": probe.runbook_version,
            "executor_operator_id": probe.executor_operator_id,
            "reviewer_operator_id": reviewer_operator_id,
            "idempotency_key": idempotency_key,
            "request_sha256": request_sha256,
            "probe_record_sha256": probe.record_sha256,
            "handoff_report_sha256": probe.handoff_report_sha256,
            "configuration_sha256": probe.configuration_sha256,
            "deployment_release_sha256": (
                probe.deployment_release_sha256
            ),
            "live_resource_sha256": probe.live_resource_sha256,
            "decision": request.decision,
            "reason": request.reason,
            "live_probe_reviewed": request.live_probe_reviewed,
            "monitoring_owner_confirmed": (
                request.monitoring_owner_confirmed
            ),
            "rollback_owner_confirmed": (
                request.rollback_owner_confirmed
            ),
            "reconciliation_owner_confirmed": (
                request.reconciliation_owner_confirmed
            ),
            "controlled_change_window_confirmed": (
                request.controlled_change_window_confirmed
            ),
            "decided_at": now,
            "expires_at": probe.expires_at if is_go else None,
            "allows_guarded_enablement_procedure": is_go,
        }
        record = self._build_decision_record(values)
        return await self.store.claim_decision(record)

    async def get_decision(
        self,
        approval_id: str,
    ) -> ProductionPilotGoNoGoRecord | None:
        required_identifier(
            approval_id,
            label="Go/No-Go Approval ID",
        )
        return await self.store.get_decision_by_approval(
            approval_id
        )

    async def _load_handoff(
        self,
        *,
        approval_id: str,
        operator_id: str,
        request: ProductionPilotLiveProbeRequest,
    ):
        try:
            return await self.final_handoff_service.rehearse(
                approval_id=approval_id,
                operator_id=operator_id,
                request=request.handoff,
            )
        except ProductionPilotFinalHandoffConflictError as exc:
            raise ProductionPilotGoNoGoStaleEvidenceError(
                "Production Pilot final handoff evidence changed"
            ) from exc
        except ProductionPilotFinalHandoffError as exc:
            raise ProductionPilotGoNoGoError(
                "Production Pilot final handoff is unavailable"
            ) from exc

    @staticmethod
    def _build_probe_record(
        values: dict[str, Any],
    ) -> ProductionPilotLiveProbeRecord:
        unsigned = ProductionPilotLiveProbeRecord.model_construct(
            **values,
            record_sha256="0" * 64,
        )
        return ProductionPilotLiveProbeRecord(
            **values,
            record_sha256=digest_model(
                unsigned,
                excluded={"record_sha256"},
            ),
        )

    @staticmethod
    def _build_decision_record(
        values: dict[str, Any],
    ) -> ProductionPilotGoNoGoRecord:
        unsigned = ProductionPilotGoNoGoRecord.model_construct(
            **values,
            record_sha256="0" * 64,
        )
        return ProductionPilotGoNoGoRecord(
            **values,
            record_sha256=digest_model(
                unsigned,
                excluded={"record_sha256"},
            ),
        )

    def _now(self) -> datetime:
        return aware_utc(
            self._clock(),
            label="Production Pilot Go/No-Go clock",
        )


__all__ = [
    "ProductionPilotGoNoGoError",
    "ProductionPilotGoNoGoService",
    "ProductionPilotGoNoGoStaleEvidenceError",
    "ProductionPilotLiveProbeRunResult",
]
