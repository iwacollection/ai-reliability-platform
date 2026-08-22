from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

INVESTIGATION_SHADOW_SEMANTIC_REVIEW_ACKNOWLEDGEMENT = (
    "I_ATTEST_INVESTIGATION_SHADOW_SEMANTIC_REVIEW_V1"
)


class InvestigationEngineShadowPromotionStatus(str, Enum):
    """Advisory outcome; it can never mutate Runtime configuration."""

    NO_GO = "no_go"
    SEMANTIC_REVIEW_REQUIRED = "semantic_review_required"
    PROMOTION_READY = "promotion_ready"
    STABLE_AT_MAXIMUM = "stable_at_maximum"


class InvestigationEngineShadowPromotionReason(str, Enum):
    INSUFFICIENT_MATCHED_PAIRS = "insufficient_matched_pairs"
    UNMATCHED_SHADOW_SESSIONS = "unmatched_shadow_sessions"
    SESSIONS_IN_PROGRESS = "sessions_in_progress"
    PROTOCOL_MISMATCH = "protocol_mismatch"
    SHADOW_FAILURE = "shadow_failure"
    SHADOW_INDETERMINATE = "shadow_indeterminate"
    UNEXPECTED_RESUME_BLOCK = "unexpected_resume_block"
    SEMANTIC_REVIEW_MISSING = "semantic_review_missing"
    SEMANTIC_REVIEW_INCOMPLETE = "semantic_review_incomplete"
    SEMANTIC_MISMATCH = "semantic_mismatch"
    THRESHOLDS_PASSED = "thresholds_passed"
    MAXIMUM_SAMPLE_RATE_REACHED = "maximum_sample_rate_reached"


class InvestigationEngineShadowPromotionPolicy(BaseModel):
    """Bounded fail-closed thresholds for one sample-rate step."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    schema_version: Literal["v1"] = "v1"
    max_distinct_inputs: int = Field(default=100, ge=20, le=100)
    minimum_matched_pairs: int = Field(default=20, ge=10, le=100)
    maximum_unmatched_shadow_sessions: int = Field(default=0, ge=0, le=5)
    minimum_protocol_match_ratio: float = Field(
        default=1.0,
        ge=0.95,
        le=1.0,
    )
    maximum_shadow_failures: int = Field(default=0, ge=0, le=2)
    maximum_shadow_indeterminate: int = Field(default=0, ge=0, le=1)
    maximum_unexpected_resume_blocks: int = Field(default=0, ge=0, le=1)
    minimum_semantic_reviewed_pairs: int = Field(default=20, ge=10, le=100)
    minimum_semantic_equivalence_ratio: float = Field(
        default=1.0,
        ge=0.95,
        le=1.0,
    )
    maximum_sample_rate: float = Field(default=0.05, gt=0.0, le=0.05)

    @model_validator(mode="after")
    def validate_counts(self):
        if self.minimum_matched_pairs > self.max_distinct_inputs:
            raise ValueError("Shadow promotion matched-pair threshold is invalid")
        if self.minimum_semantic_reviewed_pairs > self.max_distinct_inputs:
            raise ValueError("Shadow promotion review threshold is invalid")
        return self


class InvestigationEngineShadowSemanticReviewEvidence(BaseModel):
    """Human-reviewed semantic evidence bound to one immutable input window."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    schema_version: Literal["v1"] = "v1"
    source_window_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewer_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$",
    )
    reviewed_pairs: int = Field(ge=1, le=100)
    semantically_equivalent_pairs: int = Field(ge=0, le=100)
    review_evidence_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewed_at: datetime
    acknowledgement: Literal[
        "I_ATTEST_INVESTIGATION_SHADOW_SEMANTIC_REVIEW_V1"
    ]

    @model_validator(mode="after")
    def validate_review(self):
        if self.semantically_equivalent_pairs > self.reviewed_pairs:
            raise ValueError("Shadow semantic review counts are invalid")
        if self.reviewed_at.tzinfo is None or self.reviewed_at.utcoffset() is None:
            raise ValueError("Shadow semantic review time must be timezone-aware")
        return self


class InvestigationEngineShadowEvaluationSnapshot(BaseModel):
    """Immutable aggregate with no raw reasoning, evidence, or credentials."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    schema_version: Literal["v1"] = "v1"
    evaluation_id: UUID
    incident_id: UUID
    source_window_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    assessment_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    matrix_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    release_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    generated_at: datetime
    current_sample_rate: float = Field(gt=0.0, le=0.05)
    evaluated_distinct_primary_inputs: int = Field(ge=0, le=100)
    evaluated_distinct_shadow_inputs: int = Field(ge=0, le=100)
    matched_pairs: int = Field(ge=0, le=100)
    unmatched_primary_sessions: int = Field(ge=0, le=100)
    unmatched_shadow_sessions: int = Field(ge=0, le=100)
    terminal_pairs: int = Field(ge=0, le=100)
    sessions_in_progress: int = Field(ge=0, le=100)
    protocol_matched_pairs: int = Field(ge=0, le=100)
    protocol_mismatched_pairs: int = Field(ge=0, le=100)
    shadow_failures: int = Field(ge=0, le=100)
    shadow_indeterminate: int = Field(ge=0, le=100)
    unexpected_resume_blocks: int = Field(ge=0, le=100)
    semantic_reviewed_pairs: int = Field(ge=0, le=100)
    semantically_equivalent_pairs: int = Field(ge=0, le=100)
    semantic_review_evidence_digest: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    promotion_status: InvestigationEngineShadowPromotionStatus
    promotion_reasons: tuple[
        InvestigationEngineShadowPromotionReason,
        ...,
    ] = Field(min_length=1, max_length=10)
    recommended_sample_rate: float | None = Field(default=None, gt=0.0, le=0.05)
    read_only: Literal[True] = True
    advisory_only: Literal[True] = True
    applies_configuration: Literal[False] = False
    primary_result_influence: Literal[False] = False

    @model_validator(mode="after")
    def validate_snapshot(self):
        if self.generated_at.tzinfo is None or self.generated_at.utcoffset() is None:
            raise ValueError("Shadow evaluation time must be timezone-aware")
        if self.protocol_matched_pairs + self.protocol_mismatched_pairs != self.matched_pairs:
            raise ValueError("Shadow evaluation protocol counts are invalid")
        if self.terminal_pairs + self.sessions_in_progress != self.matched_pairs:
            raise ValueError("Shadow evaluation lifecycle counts are invalid")
        if (
            self.unmatched_primary_sessions + self.matched_pairs
            != self.evaluated_distinct_primary_inputs
        ):
            raise ValueError("Shadow evaluation primary counts are invalid")
        if (
            self.unmatched_shadow_sessions + self.matched_pairs
            != self.evaluated_distinct_shadow_inputs
        ):
            raise ValueError("Shadow evaluation Shadow counts are invalid")
        if self.semantic_reviewed_pairs > self.matched_pairs:
            raise ValueError("Shadow evaluation review coverage is invalid")
        if self.semantically_equivalent_pairs > self.semantic_reviewed_pairs:
            raise ValueError("Shadow evaluation semantic counts are invalid")
        if self.semantic_reviewed_pairs == 0 and self.semantic_review_evidence_digest is not None:
            raise ValueError("Shadow evaluation contains unbound review evidence")
        if self.semantic_reviewed_pairs > 0 and self.semantic_review_evidence_digest is None:
            raise ValueError("Shadow evaluation review evidence is missing")
        if self.promotion_status == InvestigationEngineShadowPromotionStatus.PROMOTION_READY:
            if (
                self.recommended_sample_rate is None
                or self.recommended_sample_rate <= self.current_sample_rate
                or self.promotion_reasons
                != (InvestigationEngineShadowPromotionReason.THRESHOLDS_PASSED,)
            ):
                raise ValueError("Shadow promotion-ready result is invalid")
        elif (
            self.promotion_status
            == InvestigationEngineShadowPromotionStatus.STABLE_AT_MAXIMUM
        ):
            if self.promotion_reasons != (
                InvestigationEngineShadowPromotionReason
                .MAXIMUM_SAMPLE_RATE_REACHED,
            ) or self.recommended_sample_rate is not None:
                raise ValueError("Shadow maximum-rate result is invalid")
        elif self.recommended_sample_rate is not None:
            raise ValueError("No-Go Shadow evaluation recommends a sample rate")
        return self


class InvestigationEngineShadowEvaluationCreateResult(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    snapshot: InvestigationEngineShadowEvaluationSnapshot
    created: bool


def aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Shadow evaluation time must be timezone-aware")
    return value.astimezone(UTC)


__all__ = [
    "INVESTIGATION_SHADOW_SEMANTIC_REVIEW_ACKNOWLEDGEMENT",
    "InvestigationEngineShadowEvaluationCreateResult",
    "InvestigationEngineShadowEvaluationSnapshot",
    "InvestigationEngineShadowPromotionPolicy",
    "InvestigationEngineShadowPromotionReason",
    "InvestigationEngineShadowPromotionStatus",
    "InvestigationEngineShadowSemanticReviewEvidence",
    "aware_utc",
]
