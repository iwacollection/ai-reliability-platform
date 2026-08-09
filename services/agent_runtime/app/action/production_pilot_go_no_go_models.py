import json

from datetime import UTC, datetime
from enum import Enum
from hashlib import sha256
from re import fullmatch
from typing import Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from services.agent_runtime.app.action.production_pilot_final_handoff import (
    ProductionPilotFinalHandoffRequest,
)


PRODUCTION_PILOT_LIVE_PROBE_ACKNOWLEDGEMENT = (
    "I_AUTHORIZE_READ_ONLY_OOM_PILOT_LIVE_PROBE_V1"
)
PRODUCTION_PILOT_GO_NO_GO_ACKNOWLEDGEMENT = (
    "I_CONFIRM_OOM_PILOT_FINAL_GO_NO_GO_DECISION_V1"
)

_IDENTIFIER_PATTERN = (
    r"[A-Za-z0-9](?:[A-Za-z0-9_.:/-]{0,126}[A-Za-z0-9])?"
)
_SHA256_PATTERN = r"[0-9a-f]{64}"
_BLOCKER_PATTERN = r"[a-z][a-z0-9_]{0,127}"


class ProductionPilotLiveProbeStatus(str, Enum):
    """Durable state of the single read-only Kubernetes live probe."""

    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"


class ProductionPilotGoNoGoDecision(str, Enum):
    """Final human decision for the current bounded Pilot evidence."""

    GO = "go"
    NO_GO = "no_go"


class ProductionPilotLiveProbeRequest(BaseModel):
    """Exact handoff evidence authorized for one read-only live probe."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    expected_handoff_report_sha256: str = Field(
        pattern=_SHA256_PATTERN,
    )
    handoff: ProductionPilotFinalHandoffRequest
    acknowledgement: str = Field(
        min_length=1,
        max_length=128,
    )

    @model_validator(mode="after")
    def validate_acknowledgement(
        self,
    ) -> "ProductionPilotLiveProbeRequest":
        if (
            self.acknowledgement
            != PRODUCTION_PILOT_LIVE_PROBE_ACKNOWLEDGEMENT
        ):
            raise ValueError(
                "Production Pilot live probe acknowledgement is invalid"
            )
        return self


class ProductionPilotLiveProbeRecord(BaseModel):
    """Bounded durable audit record for one two-credential GET probe."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        use_enum_values=True,
        validate_default=True,
    )

    probe_id: UUID
    approval_id: str = Field(
        min_length=1,
        max_length=128,
    )
    incident_id: UUID
    artifact_id: UUID
    ceremony_id: UUID
    pilot_id: str = Field(
        min_length=1,
        max_length=128,
    )
    change_ticket: str = Field(
        min_length=1,
        max_length=128,
    )
    runbook_version: str = Field(
        min_length=1,
        max_length=128,
    )
    executor_operator_id: str = Field(
        min_length=1,
        max_length=128,
    )
    idempotency_key: str = Field(
        min_length=1,
        max_length=128,
    )
    request_sha256: str = Field(
        pattern=_SHA256_PATTERN,
    )
    evidence_sha256: str = Field(
        pattern=_SHA256_PATTERN,
    )
    handoff_report_sha256: str = Field(
        pattern=_SHA256_PATTERN,
    )
    configuration_sha256: str = Field(
        pattern=_SHA256_PATTERN,
    )
    deployment_release_sha256: str = Field(
        pattern=r"sha256:[0-9a-f]{64}",
    )
    handoff_request: ProductionPilotFinalHandoffRequest
    status: ProductionPilotLiveProbeStatus = (
        ProductionPilotLiveProbeStatus.RUNNING
    )
    started_at: datetime
    updated_at: datetime
    expires_at: datetime
    completed_at: datetime | None = None
    preflight_credential_authenticated: bool = False
    production_credential_authenticated: bool = False
    tls_verified: bool = False
    target_state_consistent: bool = False
    live_resource_sha256: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN,
    )
    blocker_code: str | None = Field(
        default=None,
        pattern=_BLOCKER_PATTERN,
    )
    network_call_count: int = Field(
        default=0,
        ge=0,
        le=2,
    )
    kubernetes_read_count: int = Field(
        default=0,
        ge=0,
        le=2,
    )
    kubernetes_write_count: Literal[0] = 0
    patch_request_count: Literal[0] = 0
    dry_run_request_count: Literal[0] = 0
    durable_action_claim_created: Literal[False] = False
    pilot_budget_reserved: Literal[False] = False
    verification_started: Literal[False] = False
    production_feature_gate_changed: Literal[False] = False
    kill_switch_changed: Literal[False] = False
    automatic_retry_allowed: Literal[False] = False
    record_sha256: str = Field(
        pattern=_SHA256_PATTERN,
    )

    @field_validator(
        "approval_id",
        "pilot_id",
        "change_ticket",
        "runbook_version",
        "executor_operator_id",
        "idempotency_key",
        mode="before",
    )
    @classmethod
    def validate_identifier(
        cls,
        value: Any,
    ) -> str:
        if (
            not isinstance(value, str)
            or value != value.strip()
            or fullmatch(_IDENTIFIER_PATTERN, value) is None
        ):
            raise ValueError(
                "Production Pilot live probe identifier is invalid"
            )
        return value

    @field_validator(
        "started_at",
        "updated_at",
        "expires_at",
        "completed_at",
        mode="before",
    )
    @classmethod
    def validate_time(
        cls,
        value: Any,
        info,
    ) -> datetime | None:
        if value is None:
            return None
        return _aware_utc(
            value,
            label=info.field_name,
        )

    @model_validator(mode="after")
    def validate_lifecycle(
        self,
    ) -> "ProductionPilotLiveProbeRecord":
        if self.updated_at < self.started_at:
            raise ValueError(
                "Production Pilot live probe update time is invalid"
            )
        if self.expires_at <= self.started_at:
            raise ValueError(
                "Production Pilot live probe expiry is invalid"
            )
        if self.completed_at is not None and (
            self.completed_at < self.started_at
            or self.completed_at > self.updated_at
        ):
            raise ValueError(
                "Production Pilot live probe completion time is invalid"
            )

        status = ProductionPilotLiveProbeStatus(self.status)
        if status == ProductionPilotLiveProbeStatus.RUNNING:
            if (
                self.completed_at is not None
                or self.blocker_code is not None
                or self.live_resource_sha256 is not None
                or self.network_call_count != 0
                or self.kubernetes_read_count != 0
                or self.preflight_credential_authenticated
                or self.production_credential_authenticated
                or self.tls_verified
                or self.target_state_consistent
            ):
                raise ValueError(
                    "RUNNING Production Pilot live probe contains a result"
                )
        elif status == ProductionPilotLiveProbeStatus.PASSED:
            if (
                self.completed_at is None
                or self.blocker_code is not None
                or self.live_resource_sha256 is None
                or self.network_call_count != 2
                or self.kubernetes_read_count != 2
                or not self.preflight_credential_authenticated
                or not self.production_credential_authenticated
                or not self.tls_verified
                or not self.target_state_consistent
            ):
                raise ValueError(
                    "PASSED Production Pilot live probe is incomplete"
                )
        elif status == ProductionPilotLiveProbeStatus.FAILED:
            if (
                self.completed_at is None
                or self.blocker_code is None
                or self.live_resource_sha256 is not None
                or self.network_call_count != self.kubernetes_read_count
            ):
                raise ValueError(
                    "FAILED Production Pilot live probe is invalid"
                )

        expected = _digest_model(
            self,
            excluded={"record_sha256"},
        )
        if self.record_sha256 != expected:
            raise ValueError(
                "Production Pilot live probe record digest is invalid"
            )
        return self

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            ProductionPilotLiveProbeStatus.PASSED.value,
            ProductionPilotLiveProbeStatus.FAILED.value,
        }


class ProductionPilotGoNoGoRequest(BaseModel):
    """Independent human decision over one immutable live probe result."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        use_enum_values=True,
        validate_default=True,
    )

    expected_probe_record_sha256: str = Field(
        pattern=_SHA256_PATTERN,
    )
    decision: ProductionPilotGoNoGoDecision
    reason: str = Field(
        min_length=1,
        max_length=1000,
    )
    live_probe_reviewed: bool
    monitoring_owner_confirmed: bool
    rollback_owner_confirmed: bool
    reconciliation_owner_confirmed: bool
    controlled_change_window_confirmed: bool
    acknowledgement: str = Field(
        min_length=1,
        max_length=128,
    )

    @field_validator("reason", mode="before")
    @classmethod
    def validate_reason(
        cls,
        value: Any,
    ) -> str:
        if (
            not isinstance(value, str)
            or value != value.strip()
            or not value
        ):
            raise ValueError(
                "Production Pilot Go/No-Go reason is invalid"
            )
        return value

    @model_validator(mode="after")
    def validate_decision(
        self,
    ) -> "ProductionPilotGoNoGoRequest":
        if (
            self.acknowledgement
            != PRODUCTION_PILOT_GO_NO_GO_ACKNOWLEDGEMENT
        ):
            raise ValueError(
                "Production Pilot Go/No-Go acknowledgement is invalid"
            )
        if self.decision == ProductionPilotGoNoGoDecision.GO.value:
            checks = (
                self.live_probe_reviewed,
                self.monitoring_owner_confirmed,
                self.rollback_owner_confirmed,
                self.reconciliation_owner_confirmed,
                self.controlled_change_window_confirmed,
            )
            if not all(checks):
                raise ValueError(
                    "Production Pilot GO requires every human check"
                )
        return self


class ProductionPilotGoNoGoRecord(BaseModel):
    """Durable final decision pack; it never enables or executes anything."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        use_enum_values=True,
        validate_default=True,
    )

    decision_id: UUID
    probe_id: UUID
    approval_id: str = Field(
        min_length=1,
        max_length=128,
    )
    incident_id: UUID
    artifact_id: UUID
    ceremony_id: UUID
    pilot_id: str = Field(
        min_length=1,
        max_length=128,
    )
    change_ticket: str = Field(
        min_length=1,
        max_length=128,
    )
    runbook_version: str = Field(
        min_length=1,
        max_length=128,
    )
    executor_operator_id: str = Field(
        min_length=1,
        max_length=128,
    )
    reviewer_operator_id: str = Field(
        min_length=1,
        max_length=128,
    )
    idempotency_key: str = Field(
        min_length=1,
        max_length=128,
    )
    request_sha256: str = Field(
        pattern=_SHA256_PATTERN,
    )
    probe_record_sha256: str = Field(
        pattern=_SHA256_PATTERN,
    )
    handoff_report_sha256: str = Field(
        pattern=_SHA256_PATTERN,
    )
    configuration_sha256: str = Field(
        pattern=_SHA256_PATTERN,
    )
    deployment_release_sha256: str = Field(
        pattern=r"sha256:[0-9a-f]{64}",
    )
    live_resource_sha256: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN,
    )
    decision: ProductionPilotGoNoGoDecision
    reason: str = Field(
        min_length=1,
        max_length=1000,
    )
    live_probe_reviewed: bool
    monitoring_owner_confirmed: bool
    rollback_owner_confirmed: bool
    reconciliation_owner_confirmed: bool
    controlled_change_window_confirmed: bool
    decided_at: datetime
    expires_at: datetime | None
    allows_guarded_enablement_procedure: bool
    feature_gate_changed: Literal[False] = False
    kill_switch_changed: Literal[False] = False
    kubernetes_network_call_count: Literal[0] = 0
    kubernetes_write_count: Literal[0] = 0
    action_execution_claim_created: Literal[False] = False
    budget_reserved: Literal[False] = False
    verification_started: Literal[False] = False
    authorizes_action_execution: Literal[False] = False
    automatic_enablement_allowed: Literal[False] = False
    requires_manual_guarded_enablement_step: Literal[True] = True
    record_sha256: str = Field(
        pattern=_SHA256_PATTERN,
    )

    @field_validator(
        "approval_id",
        "pilot_id",
        "change_ticket",
        "runbook_version",
        "executor_operator_id",
        "reviewer_operator_id",
        "idempotency_key",
        mode="before",
    )
    @classmethod
    def validate_identifier(
        cls,
        value: Any,
    ) -> str:
        if (
            not isinstance(value, str)
            or value != value.strip()
            or fullmatch(_IDENTIFIER_PATTERN, value) is None
        ):
            raise ValueError(
                "Production Pilot Go/No-Go identifier is invalid"
            )
        return value

    @field_validator("decided_at", "expires_at", mode="before")
    @classmethod
    def validate_time(
        cls,
        value: Any,
        info,
    ) -> datetime | None:
        if value is None:
            return None
        return _aware_utc(
            value,
            label=info.field_name,
        )

    @model_validator(mode="after")
    def validate_record(
        self,
    ) -> "ProductionPilotGoNoGoRecord":
        is_go = self.decision == ProductionPilotGoNoGoDecision.GO.value
        checks = (
            self.live_probe_reviewed,
            self.monitoring_owner_confirmed,
            self.rollback_owner_confirmed,
            self.reconciliation_owner_confirmed,
            self.controlled_change_window_confirmed,
        )
        if is_go and not all(checks):
            raise ValueError(
                "Production Pilot GO record requires every human check"
            )
        if is_go and (
            self.expires_at is None
            or self.expires_at <= self.decided_at
        ):
            raise ValueError(
                "Production Pilot GO expiry is invalid"
            )
        if not is_go and self.expires_at is not None:
            raise ValueError(
                "Production Pilot NO-GO must not carry an expiry"
            )
        if self.allows_guarded_enablement_procedure != is_go:
            raise ValueError(
                "Production Pilot Go/No-Go permission is inconsistent"
            )
        if is_go and self.live_resource_sha256 is None:
            raise ValueError(
                "Production Pilot GO requires live resource evidence"
            )
        expected = _digest_model(
            self,
            excluded={"record_sha256"},
        )
        if self.record_sha256 != expected:
            raise ValueError(
                "Production Pilot Go/No-Go record digest is invalid"
            )
        return self


def aware_utc(
    value: Any,
    *,
    label: str,
) -> datetime:
    return _aware_utc(
        value,
        label=label,
    )


def digest_mapping(
    values: dict[str, Any],
) -> str:
    canonical = json.dumps(
        values,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )
    return sha256(
        canonical.encode("utf-8")
    ).hexdigest()


def digest_model(
    model: BaseModel,
    *,
    excluded: set[str],
) -> str:
    return _digest_model(
        model,
        excluded=excluded,
    )


def required_identifier(
    value: Any,
    *,
    label: str,
) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or fullmatch(_IDENTIFIER_PATTERN, value) is None
    ):
        raise ValueError(
            f"Production Pilot {label} is invalid"
        )
    return value


def _aware_utc(
    value: Any,
    *,
    label: str,
) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(
                value.replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise ValueError(
                f"{label} must be an ISO-8601 datetime"
            ) from exc
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(
            f"{label} must be timezone-aware"
        )
    return value.astimezone(UTC)


def _digest_model(
    model: BaseModel,
    *,
    excluded: set[str],
) -> str:
    return digest_mapping(
        model.model_dump(
            mode="json",
            exclude=excluded,
        )
    )


__all__ = [
    "PRODUCTION_PILOT_GO_NO_GO_ACKNOWLEDGEMENT",
    "PRODUCTION_PILOT_LIVE_PROBE_ACKNOWLEDGEMENT",
    "ProductionPilotGoNoGoDecision",
    "ProductionPilotGoNoGoRecord",
    "ProductionPilotGoNoGoRequest",
    "ProductionPilotLiveProbeRecord",
    "ProductionPilotLiveProbeRequest",
    "ProductionPilotLiveProbeStatus",
    "aware_utc",
    "digest_mapping",
    "digest_model",
    "required_identifier",
]
