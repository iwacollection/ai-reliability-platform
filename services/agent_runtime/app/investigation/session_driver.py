from __future__ import annotations

import asyncio
import re

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from services.agent_runtime.app.investigation.epistemic_guard import (
    EpistemicConclusionGuard,
)
from services.agent_runtime.app.investigation.models import (
    EvidenceItem,
    InvestigationDecision,
    InvestigationProbe,
    InvestigationState,
    InvestigationStatus,
    InvestigationStopReason,
)
from services.agent_runtime.app.investigation.reasoner import (
    BaseInvestigationReasoner,
    InvestigationReasonerError,
)
from services.agent_runtime.app.investigation.session_models import (
    InvestigationSessionRecord,
    InvestigationSessionStatus,
    InvestigationStepKind,
    InvestigationStepRecord,
    InvestigationStepStatus,
    canonical_digest,
)
from services.agent_runtime.app.investigation.session_service import (
    InvestigationSessionService,
)


class InvestigationSessionDriverError(RuntimeError):
    """Sanitized failure at the durable single-step execution boundary."""


class InvestigationSessionDriverBlockedError(
    InvestigationSessionDriverError
):
    """A safe precondition prevented a new external read call."""


class InvestigationSessionDecisionError(
    InvestigationSessionDriverError
):
    """A Reasoner decision conflicts with the current durable state."""


@dataclass(frozen=True)
class InvestigationSessionDriverResult:
    session: InvestigationSessionRecord
    step: InvestigationStepRecord
    external_call_made: bool
    replayed: bool

    @property
    def recovery_required(self) -> bool:
        return self.step.status in {
            InvestigationStepStatus.CLAIMED,
            InvestigationStepStatus.INDETERMINATE,
        }


class DurableInvestigationSessionDriver:
    """
    Execute one Reasoner or Probe step behind a durable Claim.

    A new external call is possible only when InvestigationSessionService has
    atomically persisted a new Claim and returned call_granted=True. Exact
    replay, an unresolved Claim, and an indeterminate result never call the
    external dependency again.

    This driver does not publish Context metadata, mutate Incident state, or
    invoke Approval, Action, Verification, budget, or Kubernetes writes.
    """

    _REASONER_OPERATION = "investigation.reasoner.decide.v1"
    _PROBE_OPERATION = "investigation.probe.collect.v1"

    def __init__(
        self,
        *,
        session_service: InvestigationSessionService,
        reasoner: BaseInvestigationReasoner,
        probe_executor,
        require_cluster_verified_evidence: bool = False,
        utc_clock=None,
    ) -> None:
        if not isinstance(
            session_service,
            InvestigationSessionService,
        ):
            raise TypeError(
                "Investigation Session Service is invalid"
            )
        if not isinstance(
            reasoner,
            BaseInvestigationReasoner,
        ):
            raise TypeError(
                "Investigation Session Reasoner is invalid"
            )
        if probe_executor is None or not callable(
            getattr(probe_executor, "collect", None)
        ):
            raise TypeError(
                "Investigation Session Probe executor is invalid"
            )
        if not isinstance(
            require_cluster_verified_evidence,
            bool,
        ):
            raise TypeError(
                "Investigation cluster evidence policy is invalid"
            )

        self.session_service = session_service
        self.reasoner = reasoner
        self.probe_executor = probe_executor
        self.require_cluster_verified_evidence = (
            require_cluster_verified_evidence
        )
        self._utc_clock = utc_clock or (
            lambda: datetime.now(UTC)
        )
        self._guard = EpistemicConclusionGuard()

    async def execute_reasoner_step(
        self,
        session_id: UUID | str,
        *,
        claimant: str,
        expected_version: int | None = None,
    ) -> InvestigationSessionDriverResult:
        current = await self.session_service.require(
            session_id
        )
        self._assert_expected_version(
            current,
            expected_version=expected_version,
        )
        replay = self._latest_step_replay(
            current,
            kind=InvestigationStepKind.REASONER,
            claimant=claimant,
        )
        if replay is not None:
            return replay
        self._assert_reasoner_sequence(current)
        timeout = self._remaining_seconds(current)
        request_digest = canonical_digest(
            {
                "operation": self._REASONER_OPERATION,
                "session_id": current.session_id,
                "input_digest": current.input_digest,
                "state": current.state,
            }
        )
        claim = await self.session_service.claim_step(
            current.session_id,
            kind=InvestigationStepKind.REASONER,
            request_digest=request_digest,
            claimant=claimant,
            now=self._now(),
        )
        if not claim.call_granted:
            return InvestigationSessionDriverResult(
                session=claim.session,
                step=claim.step,
                external_call_made=False,
                replayed=True,
            )

        try:
            decision = await asyncio.wait_for(
                self.reasoner.decide(
                    claim.session.state.scope,
                    claim.session.state.model_copy(
                        deep=True
                    ),
                ),
                timeout=timeout,
            )
        except InvestigationReasonerError as error:
            failed_state = self._failed_reasoner_state(
                claim.session.state,
                failure_code=self._failure_code(error),
            )
            completion = await self.session_service.complete_step(
                claim.session.session_id,
                step_id=claim.step.step_id,
                request_digest=request_digest,
                outcome=InvestigationStepStatus.FAILED,
                next_state=failed_state,
                failure_code=self._failure_code(error),
                now=self._now(),
            )
        except TimeoutError:
            completion = await self._complete_indeterminate_reasoner(
                claim.session,
                step=claim.step,
                request_digest=request_digest,
                failure_code="ReasonerTimeout",
            )
        except Exception as error:
            completion = await self._complete_indeterminate_reasoner(
                claim.session,
                step=claim.step,
                request_digest=request_digest,
                failure_code=self._failure_code(error),
            )
        else:
            try:
                next_state = self._apply_decision(
                    claim.session.state,
                    decision,
                )
            except InvestigationSessionDecisionError as error:
                failed_state = self._failed_reasoner_state(
                    claim.session.state,
                    failure_code=self._failure_code(error),
                )
                completion = await self.session_service.complete_step(
                    claim.session.session_id,
                    step_id=claim.step.step_id,
                    request_digest=request_digest,
                    outcome=InvestigationStepStatus.FAILED,
                    next_state=failed_state,
                    failure_code=self._failure_code(error),
                    now=self._now(),
                )
            else:
                completion = await self.session_service.complete_step(
                    claim.session.session_id,
                    step_id=claim.step.step_id,
                    request_digest=request_digest,
                    outcome=InvestigationStepStatus.SUCCEEDED,
                    next_state=next_state,
                    decision=decision,
                    now=self._now(),
                )

        return InvestigationSessionDriverResult(
            session=completion.session,
            step=completion.step,
            external_call_made=True,
            replayed=completion.replayed,
        )

    async def execute_probe_step(
        self,
        session_id: UUID | str,
        *,
        context,
        claimant: str,
        expected_version: int | None = None,
    ) -> InvestigationSessionDriverResult:
        current = await self.session_service.require(
            session_id
        )
        self._assert_expected_version(
            current,
            expected_version=expected_version,
        )
        replay = self._latest_step_replay(
            current,
            kind=InvestigationStepKind.PROBE,
            claimant=claimant,
        )
        if replay is not None:
            return replay
        probe = self._required_probe(current)
        timeout = self._remaining_seconds(current)
        request_digest = canonical_digest(
            {
                "operation": self._PROBE_OPERATION,
                "session_id": current.session_id,
                "input_digest": current.input_digest,
                "state": current.state,
                "probe": probe,
            }
        )
        claim = await self.session_service.claim_step(
            current.session_id,
            kind=InvestigationStepKind.PROBE,
            request_digest=request_digest,
            claimant=claimant,
            probe=probe,
            now=self._now(),
        )
        if not claim.call_granted:
            return InvestigationSessionDriverResult(
                session=claim.session,
                step=claim.step,
                external_call_made=False,
                replayed=True,
            )

        try:
            evidence = await asyncio.wait_for(
                self.probe_executor.collect(
                    context,
                    claim.session.state.scope,
                    probe,
                ),
                timeout=timeout,
            )
            evidence = self._sanitize_evidence(
                scope_cluster=claim.session.state.scope.cluster,
                probe=probe,
                evidence=evidence,
            )
        except TimeoutError:
            evidence = self._failed_evidence(
                probe=probe,
                error_code="ProbeTimeout",
            )
        except Exception as error:
            evidence = self._failed_evidence(
                probe=probe,
                error_code=type(error).__name__[:256],
            )

        next_state = self._apply_evidence(
            claim.session.state,
            probe=probe,
            evidence=evidence,
        )
        completion = await self.session_service.complete_step(
            claim.session.session_id,
            step_id=claim.step.step_id,
            request_digest=request_digest,
            outcome=InvestigationStepStatus.SUCCEEDED,
            next_state=next_state,
            evidence=evidence,
            now=self._now(),
        )
        return InvestigationSessionDriverResult(
            session=completion.session,
            step=completion.step,
            external_call_made=True,
            replayed=completion.replayed,
        )

    @staticmethod
    def _assert_expected_version(
        session: InvestigationSessionRecord,
        *,
        expected_version: int | None,
    ) -> None:
        """
        Bind one external step to the caller's observed durable version.

        The Store CAS still resolves races after this check. This additional
        precondition prevents an API retry that arrives after a completed
        step from silently advancing the next protocol phase.
        """

        if expected_version is None:
            return
        if (
            not isinstance(expected_version, int)
            or isinstance(expected_version, bool)
            or expected_version < 0
        ):
            raise InvestigationSessionDriverBlockedError(
                "Investigation Session expected version is invalid"
            )
        if session.version != expected_version:
            raise InvestigationSessionDriverBlockedError(
                "Investigation Session version changed before execution"
            )

    def _assert_reasoner_sequence(
        self,
        session: InvestigationSessionRecord,
    ) -> None:
        if session.status not in {
            InvestigationSessionStatus.READY,
            InvestigationSessionStatus.PAUSED,
        }:
            raise InvestigationSessionDriverBlockedError(
                "Investigation Reasoner step is not safely resumable"
            )
        if session.state.iteration_count >= session.state.limits.max_iterations:
            raise InvestigationSessionDriverBlockedError(
                "Investigation Reasoner iteration budget is exhausted"
            )
        if session.steps:
            latest = session.steps[-1]
            if (
                latest.kind != InvestigationStepKind.PROBE
                or latest.status != InvestigationStepStatus.SUCCEEDED
            ):
                raise InvestigationSessionDriverBlockedError(
                    "Investigation Reasoner sequence is invalid"
                )

    @staticmethod
    def _latest_step_replay(
        session: InvestigationSessionRecord,
        *,
        kind: InvestigationStepKind,
        claimant: str,
    ) -> InvestigationSessionDriverResult | None:
        if not session.steps or session.steps[-1].kind != kind:
            return None
        step = session.steps[-1]
        if step.claimant != claimant:
            raise InvestigationSessionDriverBlockedError(
                "Investigation step replay claimant does not match"
            )
        return InvestigationSessionDriverResult(
            session=session,
            step=step,
            external_call_made=False,
            replayed=True,
        )

    def _required_probe(
        self,
        session: InvestigationSessionRecord,
    ) -> InvestigationProbe:
        if (
            session.status != InvestigationSessionStatus.PAUSED
            or not session.steps
        ):
            raise InvestigationSessionDriverBlockedError(
                "Investigation Probe step is not safely resumable"
            )
        latest = session.steps[-1]
        decision = latest.decision
        if (
            latest.kind != InvestigationStepKind.REASONER
            or latest.status != InvestigationStepStatus.SUCCEEDED
            or decision is None
            or decision.stop
            or decision.next_probe is None
        ):
            raise InvestigationSessionDriverBlockedError(
                "Investigation Probe requires one continuing Reasoner decision"
            )
        probe = decision.next_probe
        if probe not in session.state.available_probes:
            raise InvestigationSessionDriverBlockedError(
                "Investigation Probe is outside the trusted allowlist"
            )
        if probe in session.state.attempted_probes:
            raise InvestigationSessionDriverBlockedError(
                "Investigation Probe was already attempted"
            )
        if session.state.tool_call_count >= session.state.limits.max_tool_calls:
            raise InvestigationSessionDriverBlockedError(
                "Investigation Probe budget is exhausted"
            )
        return probe

    def _apply_decision(
        self,
        state: InvestigationState,
        decision: InvestigationDecision,
    ) -> InvestigationState:
        if not isinstance(decision, InvestigationDecision):
            raise InvestigationSessionDecisionError(
                "Investigation Reasoner decision is invalid"
            )
        if not self._evidence_references_are_valid(
            decision=decision,
            state=state,
        ):
            raise InvestigationSessionDecisionError(
                "Investigation Reasoner cited unknown evidence"
            )
        if (
            not decision.stop
            and (
                decision.next_probe not in state.available_probes
                or decision.next_probe in state.attempted_probes
                or state.tool_call_count >= state.limits.max_tool_calls
                or state.iteration_count + 1 >= state.limits.max_iterations
            )
        ):
            raise InvestigationSessionDecisionError(
                "Investigation Reasoner continuation is outside safe limits"
            )

        payload = state.model_dump(mode="python")
        payload.update(
            {
                "iteration_count": state.iteration_count + 1,
                "hypotheses": [
                    item.model_copy(deep=True)
                    for item in decision.hypotheses
                ],
                "decision_summaries": [
                    *state.decision_summaries,
                    decision.rationale_summary,
                ],
                "updated_at": self._now(),
            }
        )

        if decision.stop:
            guard_result = self._guard.evaluate(
                decision=decision,
                state=InvestigationState.model_validate(payload),
            )
            payload["status"] = InvestigationStatus.CONCLUDED
            if guard_result.allowed:
                payload["stop_reason"] = decision.stop_reason
                payload["conclusion"] = decision.conclusion
            else:
                payload["stop_reason"] = (
                    InvestigationStopReason.INSUFFICIENT_EVIDENCE
                )
                payload["epistemic_guard_code"] = guard_result.code
                payload["conclusion"] = None
        else:
            payload["status"] = InvestigationStatus.RUNNING

        return InvestigationState.model_validate(payload)

    def _apply_evidence(
        self,
        state: InvestigationState,
        *,
        probe: InvestigationProbe,
        evidence: EvidenceItem,
    ) -> InvestigationState:
        payload = state.model_dump(mode="python")
        payload.update(
            {
                "status": InvestigationStatus.RUNNING,
                "tool_call_count": state.tool_call_count + 1,
                "attempted_probes": [
                    *state.attempted_probes,
                    probe,
                ],
                "evidence": [
                    *state.evidence,
                    evidence,
                ],
                "updated_at": self._now(),
            }
        )
        return InvestigationState.model_validate(payload)

    def _failed_reasoner_state(
        self,
        state: InvestigationState,
        *,
        failure_code: str,
    ) -> InvestigationState:
        return InvestigationState.model_validate(
            {
                **state.model_dump(mode="python"),
                "status": InvestigationStatus.FAILED,
                "stop_reason": InvestigationStopReason.REASONER_ERROR,
                "failure_code": failure_code[:256],
                "updated_at": self._now(),
            }
        )

    async def _complete_indeterminate_reasoner(
        self,
        session: InvestigationSessionRecord,
        *,
        step: InvestigationStepRecord,
        request_digest: str,
        failure_code: str,
    ):
        next_state = InvestigationState.model_validate(
            {
                **session.state.model_dump(mode="python"),
                "status": InvestigationStatus.RUNNING,
                "updated_at": self._now(),
            }
        )
        return await self.session_service.complete_step(
            session.session_id,
            step_id=step.step_id,
            request_digest=request_digest,
            outcome=InvestigationStepStatus.INDETERMINATE,
            next_state=next_state,
            failure_code=failure_code[:256],
            now=self._now(),
        )

    def _sanitize_evidence(
        self,
        *,
        scope_cluster: str | None,
        probe: InvestigationProbe,
        evidence: EvidenceItem,
    ) -> EvidenceItem:
        if not isinstance(evidence, EvidenceItem):
            return self._failed_evidence(
                probe=probe,
                error_code="InvalidProbeEvidence",
            )
        if evidence.probe != probe:
            return self._failed_evidence(
                probe=probe,
                error_code="ProbeEvidenceMismatch",
            )
        mismatch = (
            (
                evidence.cluster is None
                and evidence.cluster_verified
            )
            or (
                evidence.cluster is not None
                and (
                    scope_cluster is None
                    or evidence.cluster != scope_cluster
                )
            )
        )
        verification_missing = (
            self.require_cluster_verified_evidence
            and evidence.success
            and evidence.trusted
            and evidence.production_signal
            and not evidence.cluster_verified
        )
        if mismatch:
            return self._failed_evidence(
                probe=probe,
                error_code="ClusterEvidenceMismatch",
            )
        if verification_missing:
            return self._failed_evidence(
                probe=probe,
                error_code="ClusterVerificationRequired",
            )
        return evidence

    def _failed_evidence(
        self,
        *,
        probe: InvestigationProbe,
        error_code: str,
    ) -> EvidenceItem:
        return EvidenceItem(
            probe=probe,
            source="investigation_probe",
            success=False,
            trusted=False,
            production_signal=False,
            reliability=0.0,
            observed_at=self._now(),
            facts={},
            error_code=error_code[:256],
        )

    def _remaining_seconds(
        self,
        session: InvestigationSessionRecord,
    ) -> float:
        now = self._now()
        started_at = session.state.started_at.astimezone(UTC)
        elapsed = (now - started_at).total_seconds()
        if elapsed < -1.0:
            raise InvestigationSessionDriverBlockedError(
                "Investigation Session clock moved backwards"
            )
        remaining = session.state.limits.timeout_seconds - max(0.0, elapsed)
        if remaining <= 0.0:
            raise InvestigationSessionDriverBlockedError(
                "Investigation Session timeout budget is exhausted"
            )
        return remaining

    def _now(self) -> datetime:
        value = self._utc_clock()
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise InvestigationSessionDriverError(
                "Investigation Session clock is invalid"
            )
        return value.astimezone(UTC)

    @staticmethod
    def _failure_code(error: Exception) -> str:
        value = re.sub(
            r"[^A-Za-z0-9._:-]",
            "_",
            type(error).__name__,
        )[:250]
        if not value or not value[0].isalpha():
            value = f"Error.{value}"[:256]
        return value

    @staticmethod
    def _evidence_references_are_valid(
        *,
        decision: InvestigationDecision,
        state: InvestigationState,
    ) -> bool:
        known_ids = {
            item.evidence_id
            for item in state.evidence
        }
        for hypothesis in decision.hypotheses:
            referenced = set(
                hypothesis.supporting_evidence_ids
            ) | set(
                hypothesis.conflicting_evidence_ids
            )
            if not referenced.issubset(known_ids):
                return False

        conclusion = decision.conclusion
        if conclusion is None:
            return True
        conclusion_ids = set(conclusion.evidence_ids)
        trusted_ids = {
            item.evidence_id
            for item in state.evidence
            if item.trusted
        }
        return (
            bool(conclusion_ids)
            and conclusion_ids.issubset(known_ids)
            and conclusion_ids.issubset(trusted_ids)
        )


__all__ = [
    "DurableInvestigationSessionDriver",
    "InvestigationSessionDecisionError",
    "InvestigationSessionDriverBlockedError",
    "InvestigationSessionDriverError",
    "InvestigationSessionDriverResult",
]
