from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from services.agent_runtime.app.investigation.models import (
    EvidenceItem,
    InvestigationDecision,
    InvestigationProbe,
    InvestigationState,
)
from services.agent_runtime.app.investigation.session_models import (
    InvestigationSessionRecord,
    InvestigationStepKind,
    InvestigationStepRecord,
    InvestigationStepStatus,
    build_investigation_session,
    canonical_digest,
    claim_investigation_step,
    complete_investigation_step,
)
from services.agent_runtime.app.investigation.session_store import (
    InvestigationSessionConflictError,
    InvestigationSessionCreateResult,
    InvestigationSessionStore,
)


class InvestigationSessionNotFoundError(LookupError):
    """The requested durable Investigation Session does not exist."""


@dataclass(frozen=True)
class InvestigationStepClaimResult:
    session: InvestigationSessionRecord
    step: InvestigationStepRecord
    applied: bool

    @property
    def replayed(self) -> bool:
        return not self.applied

    @property
    def call_granted(self) -> bool:
        """Only a newly persisted Claim may make one external read call."""

        return self.applied


@dataclass(frozen=True)
class InvestigationStepCompletionResult:
    session: InvestigationSessionRecord
    step: InvestigationStepRecord
    applied: bool

    @property
    def replayed(self) -> bool:
        return not self.applied


class InvestigationSessionService:
    """
    Replay-safe lifecycle service for durable Investigation Sessions.

    This service grants an external read call only after a new step Claim is
    durably committed. Exact retries return the persisted Claim or outcome and
    never grant another call. It owns no LLM, Probe, Incident, Approval,
    Action, Verification, budget, or Kubernetes capability.
    """

    def __init__(
        self,
        store: InvestigationSessionStore,
    ) -> None:
        if not isinstance(
            store,
            InvestigationSessionStore,
        ):
            raise TypeError(
                "Investigation Session Store is invalid"
            )
        self.store = store

    async def create_or_get(
        self,
        *,
        incident_id: UUID | str,
        run_key: str,
        initial_state: InvestigationState,
        created_by: str = "runtime",
        now: datetime | None = None,
    ) -> InvestigationSessionCreateResult:
        session = build_investigation_session(
            incident_id=incident_id,
            run_key=run_key,
            initial_state=initial_state,
            created_by=created_by,
            now=now,
        )
        return await self.store.create_or_get(
            session
        )

    async def get(
        self,
        session_id: UUID | str,
    ) -> InvestigationSessionRecord | None:
        return await self.store.get(
            session_id
        )

    async def require(
        self,
        session_id: UUID | str,
    ) -> InvestigationSessionRecord:
        session = await self.get(
            session_id
        )
        if session is None:
            raise InvestigationSessionNotFoundError(
                "Investigation Session not found"
            )
        return session

    async def get_by_run(
        self,
        *,
        incident_id: UUID | str,
        run_key: str,
    ) -> InvestigationSessionRecord | None:
        return await self.store.get_by_run(
            incident_id=incident_id,
            run_key=run_key,
        )

    async def list_by_incident(
        self,
        incident_id: UUID | str,
    ) -> list[InvestigationSessionRecord]:
        return await self.store.list_by_incident(
            incident_id
        )

    async def list_recent_by_incident(
        self,
        incident_id: UUID | str,
        *,
        limit: int = 20,
    ) -> list[InvestigationSessionRecord]:
        return await self.store.list_recent_by_incident(
            incident_id,
            limit=limit,
        )

    async def claim_step(
        self,
        session_id: UUID | str,
        *,
        kind: InvestigationStepKind,
        request_digest: str,
        claimant: str,
        probe: InvestigationProbe | None = None,
        now: datetime | None = None,
    ) -> InvestigationStepClaimResult:
        current = await self.require(
            session_id
        )
        replay = self._find_claim_replay(
            current,
            kind=kind,
            request_digest=request_digest,
            claimant=claimant,
            probe=probe,
        )
        if replay is not None:
            return InvestigationStepClaimResult(
                session=current,
                step=replay,
                applied=False,
            )

        candidate = claim_investigation_step(
            current,
            kind=kind,
            request_digest=request_digest,
            claimant=claimant,
            probe=probe,
            now=now,
        )

        try:
            persisted = await self.store.compare_and_swap(
                candidate,
                expected_version=current.version,
            )
        except InvestigationSessionConflictError:
            latest = await self.require(
                session_id
            )
            replay = self._find_claim_replay(
                latest,
                kind=kind,
                request_digest=request_digest,
                claimant=claimant,
                probe=probe,
            )
            if replay is None:
                raise
            return InvestigationStepClaimResult(
                session=latest,
                step=replay,
                applied=False,
            )

        return InvestigationStepClaimResult(
            session=persisted,
            step=persisted.steps[-1],
            applied=True,
        )

    async def complete_step(
        self,
        session_id: UUID | str,
        *,
        step_id: UUID | str,
        request_digest: str,
        outcome: InvestigationStepStatus,
        next_state: InvestigationState,
        decision: InvestigationDecision | None = None,
        evidence: EvidenceItem | None = None,
        failure_code: str | None = None,
        now: datetime | None = None,
    ) -> InvestigationStepCompletionResult:
        normalized_step_id = UUID(
            str(step_id)
        )
        current = await self.require(
            session_id
        )
        replay = self._find_completion_replay(
            current,
            step_id=normalized_step_id,
            request_digest=request_digest,
            outcome=outcome,
            next_state=next_state,
            decision=decision,
            evidence=evidence,
            failure_code=failure_code,
        )
        if replay is not None:
            return InvestigationStepCompletionResult(
                session=current,
                step=replay,
                applied=False,
            )

        claimed = self._require_active_claim(
            current,
            step_id=normalized_step_id,
            request_digest=request_digest,
        )
        candidate = complete_investigation_step(
            current,
            outcome=outcome,
            next_state=next_state,
            decision=decision,
            evidence=evidence,
            failure_code=failure_code,
            now=now,
        )

        try:
            persisted = await self.store.compare_and_swap(
                candidate,
                expected_version=current.version,
            )
        except InvestigationSessionConflictError:
            latest = await self.require(
                session_id
            )
            replay = self._find_completion_replay(
                latest,
                step_id=claimed.step_id,
                request_digest=request_digest,
                outcome=outcome,
                next_state=next_state,
                decision=decision,
                evidence=evidence,
                failure_code=failure_code,
            )
            if replay is None:
                raise
            return InvestigationStepCompletionResult(
                session=latest,
                step=replay,
                applied=False,
            )

        return InvestigationStepCompletionResult(
            session=persisted,
            step=persisted.steps[-1],
            applied=True,
        )

    @staticmethod
    def _find_claim_replay(
        session: InvestigationSessionRecord,
        *,
        kind: InvestigationStepKind,
        request_digest: str,
        claimant: str,
        probe: InvestigationProbe | None,
    ) -> InvestigationStepRecord | None:
        matches = [
            step
            for step in session.steps
            if step.request_digest == request_digest
        ]
        if not matches:
            return None

        step = matches[-1]
        if (
            step.kind != kind
            or step.claimant != claimant
            or step.probe != probe
        ):
            raise InvestigationSessionConflictError(
                "Investigation step Claim idempotency conflict"
            )
        return step

    @staticmethod
    def _require_active_claim(
        session: InvestigationSessionRecord,
        *,
        step_id: UUID,
        request_digest: str,
    ) -> InvestigationStepRecord:
        if not session.steps:
            raise InvestigationSessionConflictError(
                "Investigation step Claim not found"
            )
        step = session.steps[-1]
        if (
            step.step_id != step_id
            or step.request_digest != request_digest
            or step.status != InvestigationStepStatus.CLAIMED
        ):
            raise InvestigationSessionConflictError(
                "Investigation step Claim does not match"
            )
        return step

    @classmethod
    def _find_completion_replay(
        cls,
        session: InvestigationSessionRecord,
        *,
        step_id: UUID,
        request_digest: str,
        outcome: InvestigationStepStatus,
        next_state: InvestigationState,
        decision: InvestigationDecision | None,
        evidence: EvidenceItem | None,
        failure_code: str | None,
    ) -> InvestigationStepRecord | None:
        matching = [
            step
            for step in session.steps
            if step.step_id == step_id
        ]
        if not matching:
            return None
        step = matching[-1]
        if step.request_digest != request_digest:
            raise InvestigationSessionConflictError(
                "Investigation step completion request conflict"
            )
        if step.status == InvestigationStepStatus.CLAIMED:
            return None

        output = (
            decision
            if step.kind == InvestigationStepKind.REASONER
            else evidence
        )
        expected_output_digest = (
            canonical_digest(output)
            if outcome == InvestigationStepStatus.SUCCEEDED
            else None
        )
        if (
            step.status != outcome
            or step.decision != decision
            or step.evidence != evidence
            or step.output_digest != expected_output_digest
            or step.failure_code != failure_code
        ):
            raise InvestigationSessionConflictError(
                "Investigation step completion idempotency conflict"
            )

        if (
            step is session.steps[-1]
            and next_state != session.state
        ):
            raise InvestigationSessionConflictError(
                "Investigation step completion state conflict"
            )
        return step


__all__ = [
    "InvestigationSessionNotFoundError",
    "InvestigationSessionService",
    "InvestigationStepClaimResult",
    "InvestigationStepCompletionResult",
]
