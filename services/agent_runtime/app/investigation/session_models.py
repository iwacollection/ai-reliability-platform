from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import Enum
from typing import Annotated, Any
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from services.agent_runtime.app.investigation.models import (
    EvidenceItem,
    InvestigationDecision,
    InvestigationProbe,
    InvestigationState,
    InvestigationStatus,
)


SessionKey = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=256,
        pattern=(
            r"^[A-Za-z0-9]"
            r"[A-Za-z0-9._:/-]{0,255}$"
        ),
    ),
]

Claimant = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=(
            r"^[A-Za-z0-9]"
            r"[A-Za-z0-9._:@/-]{0,127}$"
        ),
    ),
]

FailureCode = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z][A-Za-z0-9._:-]{0,255}$",
    ),
]

Digest = Annotated[
    str,
    StringConstraints(
        pattern=r"^[0-9a-f]{64}$",
    ),
]


class InvestigationSessionStatus(str, Enum):
    """
    Durable orchestration status around one bounded InvestigationState.

    PAUSED is a safe between-step boundary. INDETERMINATE means a read-only
    external call may have completed but no trusted result was durably
    recorded; automatic replay is therefore blocked.
    """

    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    INDETERMINATE = "indeterminate"


class InvestigationStepKind(str, Enum):
    REASONER = "reasoner"
    PROBE = "probe"


class InvestigationStepStatus(str, Enum):
    CLAIMED = "claimed"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INDETERMINATE = "indeterminate"


class InvestigationStepRecord(BaseModel):
    """
    Immutable ledger entry for one external read-only step.

    The request body, prompt, credentials and raw tool response are never
    stored. request_digest binds the Claim to canonical inputs. A successful
    step stores only the already-bounded InvestigationDecision or EvidenceItem
    required for exact replay.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    step_id: UUID
    sequence: int = Field(
        ge=1,
        le=32,
    )
    kind: InvestigationStepKind
    status: InvestigationStepStatus
    claimant: Claimant
    request_digest: Digest
    probe: InvestigationProbe | None = None
    decision: InvestigationDecision | None = None
    evidence: EvidenceItem | None = None
    output_digest: Digest | None = None
    failure_code: FailureCode | None = None
    claimed_at: datetime
    completed_at: datetime | None = None

    @model_validator(mode="after")
    def validate_step_shape(self):
        _aware_utc(
            self.claimed_at,
            name="claimed_at",
        )

        if self.completed_at is not None:
            completed_at = _aware_utc(
                self.completed_at,
                name="completed_at",
            )
            if completed_at < _aware_utc(
                self.claimed_at,
                name="claimed_at",
            ):
                raise ValueError(
                    "Investigation step completed_at precedes claimed_at"
                )

        if self.kind == InvestigationStepKind.REASONER:
            if self.probe is not None or self.evidence is not None:
                raise ValueError(
                    "Reasoner step cannot contain Probe evidence"
                )
        else:
            if self.probe is None or self.decision is not None:
                raise ValueError(
                    "Probe step requires a symbolic probe only"
                )

        if self.status == InvestigationStepStatus.CLAIMED:
            if any(
                value is not None
                for value in (
                    self.decision,
                    self.evidence,
                    self.output_digest,
                    self.failure_code,
                    self.completed_at,
                )
            ):
                raise ValueError(
                    "Claimed Investigation step cannot contain an outcome"
                )
            return self

        if self.completed_at is None:
            raise ValueError(
                "Terminal Investigation step requires completed_at"
            )

        if self.status == InvestigationStepStatus.SUCCEEDED:
            if self.failure_code is not None:
                raise ValueError(
                    "Successful Investigation step cannot contain failure_code"
                )
            if self.output_digest is None:
                raise ValueError(
                    "Successful Investigation step requires output_digest"
                )
            if (
                self.kind == InvestigationStepKind.REASONER
                and self.decision is None
            ):
                raise ValueError(
                    "Successful Reasoner step requires a decision"
                )
            if (
                self.kind == InvestigationStepKind.PROBE
                and self.evidence is None
            ):
                raise ValueError(
                    "Successful Probe step requires bounded evidence"
                )
            return self

        if self.failure_code is None:
            raise ValueError(
                "Failed or indeterminate step requires failure_code"
            )
        if any(
            value is not None
            for value in (
                self.decision,
                self.evidence,
                self.output_digest,
            )
        ):
            raise ValueError(
                "Failed or indeterminate step cannot claim a trusted output"
            )

        return self


class InvestigationSessionRecord(BaseModel):
    """
    One bounded durable Investigation execution session.

    incident_id + run_key is the external idempotency identity. session_id is
    deterministic from that pair. input_digest binds immutable scope, limits
    and available probes. version is advanced only through Store CAS.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    schema_version: str = "v1"
    session_id: UUID
    incident_id: UUID
    run_key: SessionKey
    created_by: str = Field(
        default="runtime",
        min_length=1,
        max_length=128,
    )
    input_digest: Digest
    status: InvestigationSessionStatus
    state: InvestigationState
    version: int = Field(
        default=0,
        ge=0,
    )
    steps: tuple[
        InvestigationStepRecord,
        ...,
    ] = Field(
        default_factory=tuple,
        max_length=32,
    )
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_session_shape(self):
        created_at = _aware_utc(
            self.created_at,
            name="created_at",
        )
        updated_at = _aware_utc(
            self.updated_at,
            name="updated_at",
        )
        if updated_at < created_at:
            raise ValueError(
                "Investigation Session updated_at precedes created_at"
            )

        if self.state.investigation_id != str(
            self.session_id
        ):
            raise ValueError(
                "Investigation Session and state identity mismatch"
            )

        if self.input_digest != investigation_session_input_digest(
            self.state
        ):
            raise ValueError(
                "Investigation Session immutable input digest mismatch"
            )

        for expected, step in enumerate(
            self.steps,
            start=1,
        ):
            if step.sequence != expected:
                raise ValueError(
                    "Investigation Session step sequence is invalid"
                )

        if len(
            {
                step.step_id
                for step in self.steps
            }
        ) != len(self.steps):
            raise ValueError(
                "Investigation Session step identity is duplicated"
            )

        if any(
            step.status
            in {
                InvestigationStepStatus.CLAIMED,
                InvestigationStepStatus.INDETERMINATE,
            }
            for step in self.steps[:-1]
        ):
            raise ValueError(
                "Investigation Session contains an unresolved prior step"
            )

        latest = (
            self.steps[-1]
            if self.steps
            else None
        )

        if self.status == InvestigationSessionStatus.READY:
            if (
                self.state.status != InvestigationStatus.PENDING
                or latest is not None
            ):
                raise ValueError(
                    "Ready Investigation Session must contain fresh pending state"
                )
        elif self.status == InvestigationSessionStatus.RUNNING:
            if (
                self.state.status != InvestigationStatus.RUNNING
                or latest is None
                or latest.status != InvestigationStepStatus.CLAIMED
            ):
                raise ValueError(
                    "Running Investigation Session requires one claimed step"
                )
        elif self.status == InvestigationSessionStatus.PAUSED:
            if (
                self.state.status != InvestigationStatus.RUNNING
                or latest is None
                or latest.status
                not in {
                    InvestigationStepStatus.SUCCEEDED,
                    InvestigationStepStatus.FAILED,
                }
            ):
                raise ValueError(
                    "Paused Investigation Session requires a durable step outcome"
                )
        elif self.status == InvestigationSessionStatus.COMPLETED:
            if self.state.status not in {
                InvestigationStatus.CONCLUDED,
                InvestigationStatus.EXHAUSTED,
            }:
                raise ValueError(
                    "Completed Investigation Session requires terminal state"
                )
            if (
                latest is None
                or latest.status != InvestigationStepStatus.SUCCEEDED
            ):
                raise ValueError(
                    "Completed Investigation Session requires successful synthesis"
                )
        elif self.status == InvestigationSessionStatus.FAILED:
            if (
                self.state.status != InvestigationStatus.FAILED
                or latest is None
                or latest.status != InvestigationStepStatus.FAILED
            ):
                raise ValueError(
                    "Failed Investigation Session requires durable failure"
                )
        elif self.status == InvestigationSessionStatus.INDETERMINATE:
            if (
                self.state.status != InvestigationStatus.RUNNING
                or latest is None
                or latest.status
                != InvestigationStepStatus.INDETERMINATE
            ):
                raise ValueError(
                    "Indeterminate Investigation Session requires ambiguous step"
                )

        return self

    @property
    def automatic_resume_blocked(self) -> bool:
        return self.status in {
            InvestigationSessionStatus.COMPLETED,
            InvestigationSessionStatus.FAILED,
            InvestigationSessionStatus.INDETERMINATE,
        }


def canonical_digest(value: Any) -> str:
    payload = _json_value(
        value
    )
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(
        encoded
    ).hexdigest()


def investigation_session_input_digest(
    state: InvestigationState,
) -> str:
    if not isinstance(
        state,
        InvestigationState,
    ):
        raise TypeError(
            "Investigation Session state is invalid"
        )

    return canonical_digest(
        {
            "scope": state.scope,
            "limits": state.limits,
            "available_probes": (
                state.available_probes
            ),
        }
    )


def build_investigation_session(
    *,
    incident_id: UUID | str,
    run_key: str,
    initial_state: InvestigationState,
    created_by: str = "runtime",
    now: datetime | None = None,
) -> InvestigationSessionRecord:
    if not isinstance(
        initial_state,
        InvestigationState,
    ):
        raise TypeError(
            "Investigation Session initial state is invalid"
        )

    if (
        initial_state.status != InvestigationStatus.PENDING
        or initial_state.iteration_count != 0
        or initial_state.tool_call_count != 0
        or initial_state.hypotheses
        or initial_state.evidence
        or initial_state.attempted_probes
        or initial_state.decision_summaries
        or initial_state.stop_reason is not None
        or initial_state.failure_code is not None
        or initial_state.conclusion is not None
    ):
        raise ValueError(
            "Investigation Session requires a fresh initial state"
        )

    if not isinstance(run_key, str):
        raise TypeError(
            "Investigation Session run_key is invalid"
        )
    normalized_incident_id = UUID(
        str(
            incident_id
        )
    )
    # Pydantic applies the authoritative run_key constraints when the Record
    # is constructed below. The explicit string check avoids surprising
    # AttributeError leakage from an invalid caller value.
    normalized_run_key = run_key.strip()

    current_time = _aware_utc(
        now or datetime.now(UTC),
        name="now",
    )
    session_id = uuid5(
        NAMESPACE_URL,
        (
            "ai-reliability-platform:"
            "investigation-session:v1:"
            f"{normalized_incident_id}:"
            f"{normalized_run_key}"
        ),
    )

    state_payload = initial_state.model_dump(
        mode="python"
    )
    state_payload.update(
        {
            "investigation_id": str(
                session_id
            ),
            "started_at": current_time,
            "updated_at": current_time,
        }
    )
    normalized_state = InvestigationState.model_validate(
        state_payload
    )

    return InvestigationSessionRecord(
        session_id=session_id,
        incident_id=normalized_incident_id,
        run_key=normalized_run_key,
        created_by=created_by,
        input_digest=investigation_session_input_digest(
            normalized_state
        ),
        status=InvestigationSessionStatus.READY,
        state=normalized_state,
        version=0,
        steps=(),
        created_at=current_time,
        updated_at=current_time,
    )


def claim_investigation_step(
    session: InvestigationSessionRecord,
    *,
    kind: InvestigationStepKind,
    request_digest: str,
    claimant: str,
    probe: InvestigationProbe | None = None,
    now: datetime | None = None,
) -> InvestigationSessionRecord:
    if not isinstance(
        session,
        InvestigationSessionRecord,
    ):
        raise TypeError(
            "Investigation Session is invalid"
        )
    if session.status not in {
        InvestigationSessionStatus.READY,
        InvestigationSessionStatus.PAUSED,
    }:
        raise ValueError(
            "Investigation Session cannot claim another automatic step"
        )
    if not isinstance(kind, InvestigationStepKind):
        raise TypeError(
            "Investigation step kind is invalid"
        )

    sequence = len(session.steps) + 1
    step_id = uuid5(
        session.session_id,
        f"{sequence}:{kind.value}:{request_digest}",
    )
    claimed_at = _aware_utc(
        now or datetime.now(UTC),
        name="now",
    )
    step = InvestigationStepRecord(
        step_id=step_id,
        sequence=sequence,
        kind=kind,
        status=InvestigationStepStatus.CLAIMED,
        claimant=claimant,
        request_digest=request_digest,
        probe=probe,
        claimed_at=claimed_at,
    )

    state = InvestigationState.model_validate(
        {
            **session.state.model_dump(
                mode="python"
            ),
            "status": InvestigationStatus.RUNNING,
            "updated_at": claimed_at,
        }
    )

    updated = session.model_copy(
        update={
            "status": InvestigationSessionStatus.RUNNING,
            "state": state,
            "version": session.version + 1,
            "steps": (*session.steps, step),
            "updated_at": claimed_at,
        }
    )
    return InvestigationSessionRecord.model_validate(
        updated.model_dump(
            mode="python"
        )
    )


def complete_investigation_step(
    session: InvestigationSessionRecord,
    *,
    outcome: InvestigationStepStatus,
    next_state: InvestigationState,
    decision: InvestigationDecision | None = None,
    evidence: EvidenceItem | None = None,
    failure_code: str | None = None,
    now: datetime | None = None,
) -> InvestigationSessionRecord:
    if (
        not isinstance(
            session,
            InvestigationSessionRecord,
        )
        or session.status != InvestigationSessionStatus.RUNNING
        or not session.steps
        or session.steps[-1].status != InvestigationStepStatus.CLAIMED
    ):
        raise ValueError(
            "Investigation Session has no claimed step to complete"
        )
    if outcome == InvestigationStepStatus.CLAIMED:
        raise ValueError(
            "Investigation step completion requires a terminal outcome"
        )
    if not isinstance(
        next_state,
        InvestigationState,
    ):
        raise TypeError(
            "Investigation next state is invalid"
        )
    if next_state.investigation_id != str(
        session.session_id
    ):
        raise ValueError(
            "Investigation next state identity mismatch"
        )
    if investigation_session_input_digest(
        next_state
    ) != session.input_digest:
        raise ValueError(
            "Investigation next state changes immutable input"
        )

    completed_at = _aware_utc(
        now or datetime.now(UTC),
        name="now",
    )
    current_step = session.steps[-1]

    output = (
        decision
        if current_step.kind == InvestigationStepKind.REASONER
        else evidence
    )
    completed_step = current_step.model_copy(
        update={
            "status": outcome,
            "decision": decision,
            "evidence": evidence,
            "output_digest": (
                canonical_digest(output)
                if outcome == InvestigationStepStatus.SUCCEEDED
                else None
            ),
            "failure_code": failure_code,
            "completed_at": completed_at,
        }
    )
    # model_copy does not revalidate updates. Rebuild before the Session is
    # constructed so outcome-shape validation remains authoritative.
    completed_step = InvestigationStepRecord.model_validate(
        completed_step.model_dump(
            mode="python"
        )
    )

    if outcome == InvestigationStepStatus.INDETERMINATE:
        status = InvestigationSessionStatus.INDETERMINATE
    elif next_state.status in {
        InvestigationStatus.CONCLUDED,
        InvestigationStatus.EXHAUSTED,
    }:
        status = InvestigationSessionStatus.COMPLETED
    elif next_state.status == InvestigationStatus.FAILED:
        status = InvestigationSessionStatus.FAILED
    elif next_state.status == InvestigationStatus.RUNNING:
        status = InvestigationSessionStatus.PAUSED
    else:
        raise ValueError(
            "Investigation next state status is invalid"
        )

    updated = session.model_copy(
        update={
            "status": status,
            "state": next_state,
            "version": session.version + 1,
            "steps": (
                *session.steps[:-1],
                completed_step,
            ),
            "updated_at": completed_at,
        }
    )
    return InvestigationSessionRecord.model_validate(
        updated.model_dump(
            mode="python"
        )
    )


def _json_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(
            mode="json"
        )
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return _aware_utc(
            value,
            name="digest datetime",
        ).isoformat()
    if isinstance(value, dict):
        return {
            str(key): _json_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [
            _json_value(item)
            for item in value
        ]
    if value is None or isinstance(
        value,
        (
            bool,
            int,
            float,
            str,
        ),
    ):
        return value
    raise TypeError(
        "Investigation digest input is not JSON-compatible"
    )


def _aware_utc(
    value: datetime,
    *,
    name: str,
) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(
            f"Investigation Session {name} must be timezone-aware"
        )
    return value.astimezone(
        UTC
    )


__all__ = [
    "InvestigationSessionRecord",
    "InvestigationSessionStatus",
    "InvestigationStepKind",
    "InvestigationStepRecord",
    "InvestigationStepStatus",
    "build_investigation_session",
    "canonical_digest",
    "claim_investigation_step",
    "complete_investigation_step",
    "investigation_session_input_digest",
]
