from __future__ import annotations

from datetime import UTC, datetime
from math import isfinite
from typing import Annotated, Any
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
)

from services.agent_runtime.app.investigation.models import (
    InvestigationState,
)


BoundedText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=512,
    ),
]

LongBoundedText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=2000,
    ),
]


class IncidentAnalysisScope(BaseModel):
    """
    Stable, non-secret Incident scope retained for ChatOps queries.

    The scope is copied from the original StandardEvent. It does not contain
    credentials, endpoint URLs or raw backend payloads.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    alert_name: BoundedText
    resource: BoundedText | None = None
    namespace: BoundedText | None = None
    cluster: BoundedText | None = None


class IncidentPrimaryRCA(BaseModel):
    """
    Bounded durable projection of the authoritative Planner RCA.

    Legacy MemoryStore remains historical similarity memory. This model is
    per-Incident and therefore does not use the service/alert memory key.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    root_cause: LongBoundedText
    confidence: float = Field(
        ge=0.0,
        le=1.0,
    )
    evidence: tuple[
        LongBoundedText,
        ...,
    ] = Field(
        default_factory=tuple,
        max_length=32,
    )
    recorded_at: datetime = Field(
        default_factory=lambda: datetime.now(
            UTC
        )
    )


class IncidentAnalysisRecord(BaseModel):
    """
    One durable analysis snapshot per Incident.

    Incident lifecycle state remains owned by IncidentStore.
    Approval, Action Execution and Verification remain owned by their existing
    stores. This record owns only analysis facts that previously lived in
    request-local context.variables/context.metadata.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    schema_version: str = "v1"
    incident_id: UUID
    request_id: BoundedText | None = None
    scope: IncidentAnalysisScope
    primary_rca: IncidentPrimaryRCA | None = None
    investigation: InvestigationState | None = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(
            UTC
        )
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(
            UTC
        )
    )


def build_incident_analysis_record(
    *,
    incident_id: UUID | str,
    event: Any,
    request_id: Any = None,
    primary_rca: Any = None,
    investigation_snapshot: Any = None,
    existing: IncidentAnalysisRecord | None = None,
    now: datetime | None = None,
) -> IncidentAnalysisRecord:
    """
    Build one bounded record from Runtime-owned values.

    Invalid optional RCA/Investigation data is ignored rather than guessed.
    The caller may persist a scope-only record and enrich it later.
    """

    normalized_incident_id = UUID(
        str(
            incident_id
        )
    )

    current_time = (
        now
        or datetime.now(
            UTC
        )
    )

    if (
        current_time.tzinfo is None
        or current_time.utcoffset()
        is None
    ):
        raise ValueError(
            "Incident analysis clock must be timezone-aware"
        )

    current_time = (
        current_time.astimezone(
            UTC
        )
    )

    scope = (
        _scope_from_event(
            event
        )
        or (
            existing.scope
            if existing is not None
            else None
        )
    )

    if scope is None:
        raise ValueError(
            "Incident analysis requires bounded event scope"
        )

    parsed_primary = (
        _primary_rca(
            primary_rca,
            recorded_at=current_time,
        )
    )

    if (
        parsed_primary is None
        and existing is not None
    ):
        parsed_primary = (
            existing.primary_rca
        )

    parsed_investigation = (
        _investigation(
            investigation_snapshot
        )
    )

    if (
        parsed_investigation is None
        and existing is not None
    ):
        parsed_investigation = (
            existing.investigation
        )

    return IncidentAnalysisRecord(
        incident_id=(
            normalized_incident_id
        ),
        request_id=(
            _optional_text(
                request_id,
                max_length=512,
            )
            or (
                existing.request_id
                if existing is not None
                else None
            )
        ),
        scope=scope,
        primary_rca=parsed_primary,
        investigation=parsed_investigation,
        created_at=(
            existing.created_at
            if existing is not None
            else current_time
        ),
        updated_at=current_time,
    )


def _scope_from_event(
    event: Any,
) -> IncidentAnalysisScope | None:
    signal = getattr(
        event,
        "signal",
        None,
    )

    alert_name = _optional_text(
        getattr(
            signal,
            "name",
            None,
        ),
        max_length=512,
    )

    resources = getattr(
        event,
        "resources",
        None,
    )

    resource = None

    if isinstance(
        resources,
        (
            list,
            tuple,
        ),
    ) and resources:
        resource = resources[
            0
        ]

    resource_name = _optional_text(
        getattr(
            resource,
            "name",
            None,
        ),
        max_length=512,
    )

    if alert_name is None:
        return None

    return IncidentAnalysisScope(
        alert_name=alert_name,
        resource=resource_name,
        namespace=_optional_text(
            getattr(
                resource,
                "namespace",
                None,
            ),
            max_length=512,
        ),
        cluster=_optional_text(
            getattr(
                resource,
                "cluster",
                None,
            ),
            max_length=512,
        ),
    )


def _primary_rca(
    value: Any,
    *,
    recorded_at: datetime,
) -> IncidentPrimaryRCA | None:
    if not isinstance(
        value,
        dict,
    ):
        return None

    root_cause = _optional_text(
        value.get(
            "root_cause"
        ),
        max_length=2000,
    )

    confidence = value.get(
        "confidence"
    )

    if (
        root_cause is None
        or isinstance(
            confidence,
            bool,
        )
        or not isinstance(
            confidence,
            (
                int,
                float,
            ),
        )
    ):
        return None

    confidence_value = float(
        confidence
    )

    if (
        not isfinite(
            confidence_value
        )
        or confidence_value < 0.0
        or confidence_value > 1.0
    ):
        return None

    evidence_value = value.get(
        "evidence",
        []
    )

    evidence: list[str] = []

    if isinstance(
        evidence_value,
        (
            list,
            tuple,
        ),
    ):
        for item in evidence_value[
            :32
        ]:
            normalized = _optional_text(
                item,
                max_length=2000,
            )

            if normalized is not None:
                evidence.append(
                    normalized
                )

    return IncidentPrimaryRCA(
        root_cause=root_cause,
        confidence=confidence_value,
        evidence=tuple(
            evidence
        ),
        recorded_at=recorded_at,
    )


def _investigation(
    value: Any,
) -> InvestigationState | None:
    if value is None:
        return None

    if isinstance(
        value,
        InvestigationState,
    ):
        return value.model_copy(
            deep=True
        )

    if not isinstance(
        value,
        dict,
    ):
        return None

    try:
        return (
            InvestigationState
            .model_validate(
                value
            )
        )

    except Exception:
        return None


def _optional_text(
    value: Any,
    *,
    max_length: int,
) -> str | None:
    if value is None:
        return None

    if not isinstance(
        value,
        str,
    ):
        value = str(
            value
        )

    normalized = value.strip()

    if (
        not normalized
        or "\x00" in normalized
    ):
        return None

    return normalized[
        :max_length
    ]


__all__ = [
    "IncidentAnalysisRecord",
    "IncidentAnalysisScope",
    "IncidentPrimaryRCA",
    "build_incident_analysis_record",
]
