from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from services.agent_runtime.app.investigation.engine_benchmark_matrix import (
    InvestigationEngineBenchmarkMatrixReport,
)
from services.agent_runtime.app.investigation.session_models import (
    canonical_digest,
)
from services.agent_runtime.app.investigation.session_runtime_settings import (
    InvestigationEngineBackend,
)
from services.agent_runtime.app.investigation.settings import (
    optional_text,
    parse_bool,
    parse_float,
    parse_int,
)

INVESTIGATION_LANGGRAPH_SHADOW_ACKNOWLEDGEMENT = (
    "I_ENABLE_GUARDED_LANGGRAPH_INVESTIGATION_SHADOW_V1"
)


class InvestigationEngineShadowConfigurationError(ValueError):
    """Guarded LangGraph Shadow configuration is invalid."""


class InvestigationEngineShadowGateCode(str, Enum):
    ALLOWED = "allowed"
    DISABLED = "disabled"
    KILL_SWITCH_ENGAGED = "kill_switch_engaged"
    EVIDENCE_MISSING = "evidence_missing"
    EVIDENCE_EXPIRED = "evidence_expired"
    EVIDENCE_TOO_OLD = "evidence_too_old"
    CLOCK_ROLLBACK = "clock_rollback"
    MATRIX_DIGEST_MISMATCH = "matrix_digest_mismatch"
    RELEASE_DIGEST_MISMATCH = "release_digest_mismatch"
    PRIMARY_ENGINE_NOT_CUSTOM = "primary_engine_not_custom"
    STORE_NOT_ISOLATED = "store_not_isolated"


class InvestigationEngineShadowSettings(BaseModel):
    """Disabled-default, fail-closed LangGraph Shadow policy settings."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    enabled: bool = False
    acknowledgement: str | None = Field(
        default=None,
        max_length=128,
    )
    kill_switch_engaged: bool = True
    shadow_db_path: str = Field(
        default="data/investigation_langgraph_shadow.db",
        min_length=1,
        max_length=512,
    )
    sample_rate: float = Field(
        default=0.01,
        gt=0.0,
        le=0.05,
    )
    max_concurrent_sessions: int = Field(
        default=1,
        ge=1,
        le=4,
    )
    max_external_steps_per_invocation: Literal[1] = 1
    evidence_max_age_seconds: int = Field(
        default=3600,
        ge=60,
        le=86400,
    )
    expected_matrix_digest: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    expected_release_digest: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )

    @model_validator(mode="after")
    def validate_enablement(self):
        if (
            self.enabled
            and self.acknowledgement
            != INVESTIGATION_LANGGRAPH_SHADOW_ACKNOWLEDGEMENT
        ):
            raise ValueError(
                "enabled LangGraph Shadow requires exact acknowledgement"
            )
        if self.enabled and (
            self.expected_matrix_digest is None
            or self.expected_release_digest is None
        ):
            raise ValueError(
                "enabled LangGraph Shadow requires bound evidence digests"
            )
        if (
            "\x00" in self.shadow_db_path
            or Path(self.shadow_db_path).suffix.lower() != ".db"
        ):
            raise ValueError(
                "LangGraph Shadow database path is invalid"
            )
        return self

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> "InvestigationEngineShadowSettings":
        values = environment if environment is not None else os.environ
        try:
            return cls(
                enabled=parse_bool(
                    values.get(
                        "AGENT_INVESTIGATION_LANGGRAPH_SHADOW_ENABLED"
                    ),
                    default=False,
                    name=(
                        "AGENT_INVESTIGATION_LANGGRAPH_SHADOW_ENABLED"
                    ),
                ),
                acknowledgement=optional_text(
                    values.get(
                        "AGENT_INVESTIGATION_LANGGRAPH_SHADOW_ACKNOWLEDGEMENT"
                    )
                ),
                kill_switch_engaged=parse_bool(
                    values.get(
                        "AGENT_INVESTIGATION_LANGGRAPH_SHADOW_KILL_SWITCH_ENGAGED"
                    ),
                    default=True,
                    name=(
                        "AGENT_INVESTIGATION_LANGGRAPH_SHADOW_KILL_SWITCH_ENGAGED"
                    ),
                ),
                shadow_db_path=(
                    optional_text(
                        values.get(
                            "AGENT_INVESTIGATION_LANGGRAPH_SHADOW_DB_PATH"
                        )
                    )
                    or "data/investigation_langgraph_shadow.db"
                ),
                sample_rate=parse_float(
                    values.get(
                        "AGENT_INVESTIGATION_LANGGRAPH_SHADOW_SAMPLE_RATE"
                    ),
                    default=0.01,
                    name=(
                        "AGENT_INVESTIGATION_LANGGRAPH_SHADOW_SAMPLE_RATE"
                    ),
                ),
                max_concurrent_sessions=parse_int(
                    values.get(
                        "AGENT_INVESTIGATION_LANGGRAPH_SHADOW_MAX_CONCURRENT"
                    ),
                    default=1,
                    name=(
                        "AGENT_INVESTIGATION_LANGGRAPH_SHADOW_MAX_CONCURRENT"
                    ),
                ),
                evidence_max_age_seconds=parse_int(
                    values.get(
                        "AGENT_INVESTIGATION_LANGGRAPH_SHADOW_EVIDENCE_MAX_AGE_SECONDS"
                    ),
                    default=3600,
                    name=(
                        "AGENT_INVESTIGATION_LANGGRAPH_SHADOW_EVIDENCE_MAX_AGE_SECONDS"
                    ),
                ),
                expected_matrix_digest=optional_text(
                    values.get(
                        "AGENT_INVESTIGATION_LANGGRAPH_SHADOW_MATRIX_DIGEST"
                    )
                ),
                expected_release_digest=optional_text(
                    values.get(
                        "AGENT_INVESTIGATION_LANGGRAPH_SHADOW_RELEASE_DIGEST"
                    )
                ),
            )
        except (TypeError, ValueError) as error:
            raise InvestigationEngineShadowConfigurationError(
                "LangGraph Shadow configuration is invalid"
            ) from error


class InvestigationEngineShadowEvidence(BaseModel):
    """Immutable, release-bound evidence derived from one passing Matrix."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    schema_version: Literal["v1"] = "v1"
    source: Literal["investigation_engine_benchmark_matrix_v1"] = (
        "investigation_engine_benchmark_matrix_v1"
    )
    controlled_replay: Literal[True] = True
    read_only: Literal[True] = True
    report_passed: Literal[True] = True
    scenario_count: Literal[8] = 8
    passed_count: Literal[8] = 8
    all_semantically_equivalent: Literal[True] = True
    all_protocol_equivalent: Literal[True] = True
    all_call_budgets_equivalent: Literal[True] = True
    all_replay_safe: Literal[True] = True
    sensitive_output_absent: Literal[True] = True
    matrix_digest: str = Field(
        pattern=r"^[0-9a-f]{64}$",
    )
    release_digest: str = Field(
        pattern=r"^[0-9a-f]{64}$",
    )
    generated_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def validate_window(self):
        generated_at = _aware_utc(
            self.generated_at,
            name="generated_at",
        )
        expires_at = _aware_utc(
            self.expires_at,
            name="expires_at",
        )
        if expires_at <= generated_at:
            raise ValueError(
                "LangGraph Shadow evidence expiry is invalid"
            )
        return self


class InvestigationEngineShadowGateDecision(BaseModel):
    """Bounded Go/No-Go decision; it contains no request identity."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    allowed: bool
    code: InvestigationEngineShadowGateCode
    read_only: Literal[True] = True
    writes_allowed: Literal[False] = False
    primary_engine: Literal["custom"] = "custom"
    shadow_engine: Literal["langgraph"] = "langgraph"
    sample_rate: float = Field(
        ge=0.0,
        le=0.05,
    )
    max_concurrent_sessions: int = Field(
        ge=0,
        le=4,
    )
    matrix_digest: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    release_digest: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )


def investigation_engine_benchmark_matrix_digest(
    report: InvestigationEngineBenchmarkMatrixReport,
) -> str:
    """Build a performance-independent digest of Matrix safety evidence."""

    if not isinstance(
        report,
        InvestigationEngineBenchmarkMatrixReport,
    ):
        raise TypeError(
            "Investigation Engine Benchmark Matrix report is invalid"
        )
    payload = report.model_dump(
        mode="json"
    )
    for scenario in payload["scenarios"]:
        scenario["custom"].pop(
            "elapsed_ms",
            None,
        )
        scenario["langgraph"].pop(
            "elapsed_ms",
            None,
        )
    return canonical_digest(
        payload
    )


def build_investigation_engine_shadow_evidence(
    *,
    report: InvestigationEngineBenchmarkMatrixReport,
    release_digest: str,
    generated_at: datetime,
    expires_at: datetime,
) -> InvestigationEngineShadowEvidence:
    if not isinstance(
        report,
        InvestigationEngineBenchmarkMatrixReport,
    ):
        raise TypeError(
            "Investigation Engine Benchmark Matrix report is invalid"
        )
    if not (
        report.passed
        and report.scenario_count == 8
        and report.passed_count == 8
        and report.all_semantically_equivalent
        and report.all_protocol_equivalent
        and report.all_call_budgets_equivalent
        and report.all_replay_safe
        and report.sensitive_output_absent
    ):
        raise ValueError(
            "LangGraph Shadow requires one completely passing Matrix"
        )
    return InvestigationEngineShadowEvidence(
        matrix_digest=(
            investigation_engine_benchmark_matrix_digest(
                report
            )
        ),
        release_digest=release_digest,
        generated_at=generated_at,
        expires_at=expires_at,
    )


class InvestigationEngineShadowGate:
    """Evaluate bounded LangGraph Shadow readiness without side effects."""

    def evaluate(
        self,
        *,
        settings: InvestigationEngineShadowSettings,
        evidence: InvestigationEngineShadowEvidence | None,
        primary_backend: InvestigationEngineBackend,
        primary_db_path: str,
        now: datetime,
    ) -> InvestigationEngineShadowGateDecision:
        if not isinstance(
            settings,
            InvestigationEngineShadowSettings,
        ):
            raise TypeError(
                "LangGraph Shadow settings are invalid"
            )
        if not settings.enabled:
            return self._deny(
                InvestigationEngineShadowGateCode.DISABLED
            )
        if settings.kill_switch_engaged:
            return self._deny(
                InvestigationEngineShadowGateCode.KILL_SWITCH_ENGAGED
            )
        if evidence is None:
            return self._deny(
                InvestigationEngineShadowGateCode.EVIDENCE_MISSING
            )
        if not isinstance(
            evidence,
            InvestigationEngineShadowEvidence,
        ):
            raise TypeError(
                "LangGraph Shadow evidence is invalid"
            )
        if primary_backend != InvestigationEngineBackend.CUSTOM:
            return self._deny(
                InvestigationEngineShadowGateCode.PRIMARY_ENGINE_NOT_CUSTOM
            )
        if not self._stores_are_isolated(
            primary_db_path=primary_db_path,
            shadow_db_path=settings.shadow_db_path,
        ):
            return self._deny(
                InvestigationEngineShadowGateCode.STORE_NOT_ISOLATED
            )

        current = _aware_utc(
            now,
            name="now",
        )
        generated_at = evidence.generated_at.astimezone(
            UTC
        )
        expires_at = evidence.expires_at.astimezone(
            UTC
        )
        if current < generated_at:
            return self._deny(
                InvestigationEngineShadowGateCode.CLOCK_ROLLBACK
            )
        if current >= expires_at:
            return self._deny(
                InvestigationEngineShadowGateCode.EVIDENCE_EXPIRED
            )
        age_seconds = (
            current - generated_at
        ).total_seconds()
        window_seconds = (
            expires_at - generated_at
        ).total_seconds()
        if (
            age_seconds > settings.evidence_max_age_seconds
            or window_seconds > settings.evidence_max_age_seconds
        ):
            return self._deny(
                InvestigationEngineShadowGateCode.EVIDENCE_TOO_OLD
            )
        if (
            evidence.matrix_digest
            != settings.expected_matrix_digest
        ):
            return self._deny(
                InvestigationEngineShadowGateCode.MATRIX_DIGEST_MISMATCH
            )
        if (
            evidence.release_digest
            != settings.expected_release_digest
        ):
            return self._deny(
                InvestigationEngineShadowGateCode.RELEASE_DIGEST_MISMATCH
            )

        return InvestigationEngineShadowGateDecision(
            allowed=True,
            code=InvestigationEngineShadowGateCode.ALLOWED,
            sample_rate=settings.sample_rate,
            max_concurrent_sessions=(
                settings.max_concurrent_sessions
            ),
            matrix_digest=evidence.matrix_digest,
            release_digest=evidence.release_digest,
        )

    @staticmethod
    def selected_for_shadow(
        *,
        decision: InvestigationEngineShadowGateDecision,
        incident_id: UUID | str,
        run_key: str,
    ) -> bool:
        if not isinstance(
            decision,
            InvestigationEngineShadowGateDecision,
        ):
            raise TypeError(
                "LangGraph Shadow Gate decision is invalid"
            )
        if not decision.allowed:
            return False
        normalized_incident_id = UUID(
            str(incident_id)
        )
        if (
            not isinstance(run_key, str)
            or not run_key.strip()
            or len(run_key.strip()) > 256
            or "\x00" in run_key
        ):
            raise ValueError(
                "LangGraph Shadow sample key is invalid"
            )
        material = (
            "investigation-langgraph-shadow:v1:"
            f"{normalized_incident_id}:"
            f"{run_key.strip()}:"
            f"{decision.matrix_digest}:"
            f"{decision.release_digest}"
        ).encode(
            "utf-8"
        )
        bucket = int.from_bytes(
            hashlib.sha256(material).digest(),
            byteorder="big",
        )
        threshold = int(
            decision.sample_rate
            * (1 << 256)
        )
        return bucket < threshold

    @staticmethod
    def _deny(
        code: InvestigationEngineShadowGateCode,
    ) -> InvestigationEngineShadowGateDecision:
        return InvestigationEngineShadowGateDecision(
            allowed=False,
            code=code,
            sample_rate=0.0,
            max_concurrent_sessions=0,
        )

    @staticmethod
    def _stores_are_isolated(
        *,
        primary_db_path: str,
        shadow_db_path: str,
    ) -> bool:
        if (
            not isinstance(primary_db_path, str)
            or not primary_db_path.strip()
            or "\x00" in primary_db_path
        ):
            return False
        primary = os.path.normcase(
            os.path.abspath(
                os.path.normpath(
                    os.path.expanduser(
                        primary_db_path.strip()
                    )
                )
            )
        )
        shadow = os.path.normcase(
            os.path.abspath(
                os.path.normpath(
                    os.path.expanduser(
                        shadow_db_path.strip()
                    )
                )
            )
        )
        return primary != shadow


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
            f"{name} must be timezone-aware"
        )
    return value.astimezone(
        UTC
    )


__all__ = [
    "INVESTIGATION_LANGGRAPH_SHADOW_ACKNOWLEDGEMENT",
    "InvestigationEngineShadowConfigurationError",
    "InvestigationEngineShadowEvidence",
    "InvestigationEngineShadowGate",
    "InvestigationEngineShadowGateCode",
    "InvestigationEngineShadowGateDecision",
    "InvestigationEngineShadowSettings",
    "build_investigation_engine_shadow_evidence",
    "investigation_engine_benchmark_matrix_digest",
]
