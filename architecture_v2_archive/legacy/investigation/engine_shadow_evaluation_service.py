from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from services.agent_runtime.app.investigation.engine_shadow_evaluation_models import (
    InvestigationEngineShadowEvaluationCreateResult,
    InvestigationEngineShadowEvaluationSnapshot,
    InvestigationEngineShadowPromotionPolicy,
    InvestigationEngineShadowPromotionReason,
    InvestigationEngineShadowPromotionStatus,
    InvestigationEngineShadowSemanticReviewEvidence,
    aware_utc,
)
from services.agent_runtime.app.investigation.engine_shadow_evaluation_store import (
    InvestigationEngineShadowEvaluationStore,
)
from services.agent_runtime.app.investigation.engine_shadow_gate import (
    InvestigationEngineShadowGateDecision,
)
from services.agent_runtime.app.investigation.session_models import (
    InvestigationSessionRecord,
    InvestigationSessionStatus,
    canonical_digest,
)
from services.agent_runtime.app.investigation.session_service import (
    InvestigationSessionService,
)


class InvestigationEngineShadowEvaluationError(RuntimeError):
    """A fail-closed Shadow evaluation could not be produced."""


class InvestigationEngineShadowEvaluationService:
    """Persist advisory promotion evidence without changing Runtime behavior."""

    _TERMINAL = {
        InvestigationSessionStatus.COMPLETED,
        InvestigationSessionStatus.FAILED,
        InvestigationSessionStatus.INDETERMINATE,
    }

    def __init__(
        self,
        *,
        store: InvestigationEngineShadowEvaluationStore,
        primary_service: InvestigationSessionService,
        shadow_service: InvestigationSessionService,
        decision: InvestigationEngineShadowGateDecision,
        policy: InvestigationEngineShadowPromotionPolicy | None = None,
        utc_clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(store, InvestigationEngineShadowEvaluationStore):
            raise TypeError("Shadow evaluation Store is invalid")
        if not isinstance(primary_service, InvestigationSessionService):
            raise TypeError("Shadow evaluation primary service is invalid")
        if not isinstance(shadow_service, InvestigationSessionService):
            raise TypeError("Shadow evaluation Shadow service is invalid")
        if primary_service is shadow_service:
            raise ValueError("Shadow evaluation Session stores must be isolated")
        if not isinstance(decision, InvestigationEngineShadowGateDecision):
            raise TypeError("Shadow evaluation Gate decision is invalid")
        if (
            not decision.allowed
            or decision.matrix_digest is None
            or decision.release_digest is None
            or decision.sample_rate <= 0.0
        ):
            raise ValueError("Shadow evaluation requires a bound Allow decision")
        if policy is not None and not isinstance(
            policy,
            InvestigationEngineShadowPromotionPolicy,
        ):
            raise TypeError("Shadow evaluation promotion policy is invalid")
        if utc_clock is not None and not callable(utc_clock):
            raise TypeError("Shadow evaluation clock is invalid")

        self.store = store
        self.primary_service = primary_service
        self.shadow_service = shadow_service
        self.decision = decision
        self.policy = policy or InvestigationEngineShadowPromotionPolicy()
        self._utc_clock = utc_clock or (lambda: datetime.now(UTC))

    async def evaluate(
        self,
        incident_id: UUID | str,
        *,
        semantic_review: InvestigationEngineShadowSemanticReviewEvidence
        | None = None,
    ) -> InvestigationEngineShadowEvaluationCreateResult:
        normalized_incident_id = UUID(str(incident_id))
        try:
            query_limit = self.policy.max_distinct_inputs + 1
            primary_records, shadow_records = await asyncio.gather(
                self.primary_service.list_recent_by_incident(
                    normalized_incident_id,
                    limit=query_limit,
                ),
                self.shadow_service.list_recent_by_incident(
                    normalized_incident_id,
                    limit=query_limit,
                ),
            )
            snapshot = self._build_snapshot(
                incident_id=normalized_incident_id,
                primary_records=primary_records[-self.policy.max_distinct_inputs :],
                shadow_records=shadow_records[-self.policy.max_distinct_inputs :],
                semantic_review=semantic_review,
            )
            return await self.store.create_or_get(snapshot)
        except InvestigationEngineShadowEvaluationError:
            raise
        except Exception as error:
            raise InvestigationEngineShadowEvaluationError(
                "Shadow evaluation is unavailable"
            ) from error

    def _build_snapshot(
        self,
        *,
        incident_id: UUID,
        primary_records: list[InvestigationSessionRecord],
        shadow_records: list[InvestigationSessionRecord],
        semantic_review: InvestigationEngineShadowSemanticReviewEvidence | None,
    ) -> InvestigationEngineShadowEvaluationSnapshot:
        primary = self._latest_by_input(primary_records)
        shadow = self._latest_by_input(shadow_records)
        matched_inputs = sorted(set(primary) & set(shadow))
        generated_at = aware_utc(self._utc_clock())

        source_window_digest = canonical_digest(
            {
                "schema_version": "v1",
                "incident_id": str(incident_id),
                "matrix_digest": self.decision.matrix_digest,
                "release_digest": self.decision.release_digest,
                "current_sample_rate": self.decision.sample_rate,
                "primary": self._window_projection(primary),
                "shadow": self._window_projection(shadow),
            }
        )
        policy_digest = canonical_digest(self.policy.model_dump(mode="json"))

        if semantic_review is not None:
            if not isinstance(
                semantic_review,
                InvestigationEngineShadowSemanticReviewEvidence,
            ):
                raise TypeError("Shadow semantic review is invalid")
            if semantic_review.source_window_digest != source_window_digest:
                raise InvestigationEngineShadowEvaluationError(
                    "Shadow semantic review is stale"
                )
            if semantic_review.reviewed_pairs > len(matched_inputs):
                raise InvestigationEngineShadowEvaluationError(
                    "Shadow semantic review exceeds the matched window"
                )
            latest_window_update = max(
                (
                    record.updated_at
                    for record in (*primary.values(), *shadow.values())
                ),
                default=generated_at,
            )
            if aware_utc(semantic_review.reviewed_at) < aware_utc(
                latest_window_update
            ):
                raise InvestigationEngineShadowEvaluationError(
                    "Shadow semantic review predates the matched window"
                )
            if aware_utc(semantic_review.reviewed_at) > generated_at:
                raise InvestigationEngineShadowEvaluationError(
                    "Shadow semantic review time is invalid"
                )

        matched = [(primary[key], shadow[key]) for key in matched_inputs]
        terminal_pairs = sum(
            left.status in self._TERMINAL and right.status in self._TERMINAL
            for left, right in matched
        )
        protocol_matched_pairs = sum(
            self._protocol_projection(left) == self._protocol_projection(right)
            for left, right in matched
        )
        shadow_values = list(shadow.values())
        semantic_reviewed_pairs = (
            semantic_review.reviewed_pairs if semantic_review is not None else 0
        )
        semantically_equivalent_pairs = (
            semantic_review.semantically_equivalent_pairs
            if semantic_review is not None
            else 0
        )

        status, reasons, recommended_sample_rate = self._promotion_result(
            matched_pairs=len(matched),
            unmatched_shadow_sessions=len(set(shadow) - set(primary)),
            sessions_in_progress=len(matched) - terminal_pairs,
            protocol_matched_pairs=protocol_matched_pairs,
            shadow_failures=sum(
                record.status == InvestigationSessionStatus.FAILED
                for record in shadow_values
            ),
            shadow_indeterminate=sum(
                record.status == InvestigationSessionStatus.INDETERMINATE
                for record in shadow_values
            ),
            unexpected_resume_blocks=sum(
                record.automatic_resume_blocked
                and record.status not in self._TERMINAL
                for record in shadow_values
            ),
            semantic_reviewed_pairs=semantic_reviewed_pairs,
            semantically_equivalent_pairs=semantically_equivalent_pairs,
        )
        assessment_digest = canonical_digest(
            {
                "source_window_digest": source_window_digest,
                "policy_digest": policy_digest,
                "semantic_review": (
                    semantic_review.model_dump(mode="json")
                    if semantic_review is not None
                    else None
                ),
            }
        )

        return InvestigationEngineShadowEvaluationSnapshot(
            evaluation_id=uuid5(
                NAMESPACE_URL,
                f"investigation-shadow-evaluation:{assessment_digest}",
            ),
            incident_id=incident_id,
            source_window_digest=source_window_digest,
            assessment_digest=assessment_digest,
            policy_digest=policy_digest,
            matrix_digest=self.decision.matrix_digest,
            release_digest=self.decision.release_digest,
            generated_at=generated_at,
            current_sample_rate=self.decision.sample_rate,
            evaluated_distinct_primary_inputs=len(primary),
            evaluated_distinct_shadow_inputs=len(shadow),
            matched_pairs=len(matched),
            unmatched_primary_sessions=len(set(primary) - set(shadow)),
            unmatched_shadow_sessions=len(set(shadow) - set(primary)),
            terminal_pairs=terminal_pairs,
            sessions_in_progress=len(matched) - terminal_pairs,
            protocol_matched_pairs=protocol_matched_pairs,
            protocol_mismatched_pairs=len(matched) - protocol_matched_pairs,
            shadow_failures=sum(
                record.status == InvestigationSessionStatus.FAILED
                for record in shadow_values
            ),
            shadow_indeterminate=sum(
                record.status == InvestigationSessionStatus.INDETERMINATE
                for record in shadow_values
            ),
            unexpected_resume_blocks=sum(
                record.automatic_resume_blocked
                and record.status not in self._TERMINAL
                for record in shadow_values
            ),
            semantic_reviewed_pairs=semantic_reviewed_pairs,
            semantically_equivalent_pairs=semantically_equivalent_pairs,
            semantic_review_evidence_digest=(
                semantic_review.review_evidence_digest
                if semantic_review is not None
                else None
            ),
            promotion_status=status,
            promotion_reasons=reasons,
            recommended_sample_rate=recommended_sample_rate,
        )

    def _promotion_result(
        self,
        *,
        matched_pairs: int,
        unmatched_shadow_sessions: int,
        sessions_in_progress: int,
        protocol_matched_pairs: int,
        shadow_failures: int,
        shadow_indeterminate: int,
        unexpected_resume_blocks: int,
        semantic_reviewed_pairs: int,
        semantically_equivalent_pairs: int,
    ) -> tuple[
        InvestigationEngineShadowPromotionStatus,
        tuple[InvestigationEngineShadowPromotionReason, ...],
        float | None,
    ]:
        reasons: list[InvestigationEngineShadowPromotionReason] = []
        if matched_pairs < self.policy.minimum_matched_pairs:
            reasons.append(
                InvestigationEngineShadowPromotionReason.INSUFFICIENT_MATCHED_PAIRS
            )
        if (
            unmatched_shadow_sessions
            > self.policy.maximum_unmatched_shadow_sessions
        ):
            reasons.append(
                InvestigationEngineShadowPromotionReason.UNMATCHED_SHADOW_SESSIONS
            )
        if sessions_in_progress:
            reasons.append(
                InvestigationEngineShadowPromotionReason.SESSIONS_IN_PROGRESS
            )
        protocol_ratio = (
            protocol_matched_pairs / matched_pairs if matched_pairs else 0.0
        )
        if protocol_ratio < self.policy.minimum_protocol_match_ratio:
            reasons.append(
                InvestigationEngineShadowPromotionReason.PROTOCOL_MISMATCH
            )
        if shadow_failures > self.policy.maximum_shadow_failures:
            reasons.append(InvestigationEngineShadowPromotionReason.SHADOW_FAILURE)
        if shadow_indeterminate > self.policy.maximum_shadow_indeterminate:
            reasons.append(
                InvestigationEngineShadowPromotionReason.SHADOW_INDETERMINATE
            )
        if (
            unexpected_resume_blocks
            > self.policy.maximum_unexpected_resume_blocks
        ):
            reasons.append(
                InvestigationEngineShadowPromotionReason.UNEXPECTED_RESUME_BLOCK
            )
        if reasons:
            return (
                InvestigationEngineShadowPromotionStatus.NO_GO,
                tuple(dict.fromkeys(reasons)),
                None,
            )

        if semantic_reviewed_pairs == 0:
            return (
                InvestigationEngineShadowPromotionStatus.SEMANTIC_REVIEW_REQUIRED,
                (InvestigationEngineShadowPromotionReason.SEMANTIC_REVIEW_MISSING,),
                None,
            )
        if semantic_reviewed_pairs < self.policy.minimum_semantic_reviewed_pairs:
            return (
                InvestigationEngineShadowPromotionStatus.SEMANTIC_REVIEW_REQUIRED,
                (
                    InvestigationEngineShadowPromotionReason
                    .SEMANTIC_REVIEW_INCOMPLETE,
                ),
                None,
            )
        semantic_ratio = (
            semantically_equivalent_pairs / semantic_reviewed_pairs
        )
        if semantic_ratio < self.policy.minimum_semantic_equivalence_ratio:
            return (
                InvestigationEngineShadowPromotionStatus.NO_GO,
                (InvestigationEngineShadowPromotionReason.SEMANTIC_MISMATCH,),
                None,
            )
        if self.decision.sample_rate >= self.policy.maximum_sample_rate:
            return (
                InvestigationEngineShadowPromotionStatus.STABLE_AT_MAXIMUM,
                (
                    InvestigationEngineShadowPromotionReason
                    .MAXIMUM_SAMPLE_RATE_REACHED,
                ),
                None,
            )

        return (
            InvestigationEngineShadowPromotionStatus.PROMOTION_READY,
            (InvestigationEngineShadowPromotionReason.THRESHOLDS_PASSED,),
            min(
                self.policy.maximum_sample_rate,
                self.decision.sample_rate * 2.0,
            ),
        )

    @staticmethod
    def _latest_by_input(
        records: list[InvestigationSessionRecord],
    ) -> dict[str, InvestigationSessionRecord]:
        latest: dict[str, InvestigationSessionRecord] = {}
        for record in records:
            current = latest.get(record.input_digest)
            if current is None or (record.updated_at, str(record.session_id)) > (
                current.updated_at,
                str(current.session_id),
            ):
                latest[record.input_digest] = record
        return latest

    @staticmethod
    def _protocol_projection(record: InvestigationSessionRecord) -> dict:
        latest = record.steps[-1] if record.steps else None
        return {
            "status": record.status.value,
            "step_count": len(record.steps),
            "latest_kind": latest.kind.value if latest is not None else None,
            "latest_status": latest.status.value if latest is not None else None,
            "latest_probe": (
                latest.probe.value
                if latest is not None and latest.probe is not None
                else None
            ),
            "latest_failure_code": (
                latest.failure_code if latest is not None else None
            ),
            "automatic_resume_blocked": record.automatic_resume_blocked,
        }

    @classmethod
    def _window_projection(
        cls,
        records: dict[str, InvestigationSessionRecord],
    ) -> list[dict]:
        return [
            {
                "input_digest": input_digest,
                "session_id": str(record.session_id),
                "version": record.version,
                "updated_at": record.updated_at.isoformat(),
                "protocol": cls._protocol_projection(record),
            }
            for input_digest, record in sorted(records.items())
        ]


__all__ = [
    "InvestigationEngineShadowEvaluationError",
    "InvestigationEngineShadowEvaluationService",
]
