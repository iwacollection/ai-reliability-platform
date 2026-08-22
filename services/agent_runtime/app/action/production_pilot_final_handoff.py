import json

from collections.abc import Callable
from hashlib import sha256
from os import environ
from pathlib import Path
from re import fullmatch
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from services.agent_runtime.app.action.kubernetes_preflight import (
    KubernetesPreflightResolver,
)
from services.agent_runtime.app.action.production_pilot import (
    KubernetesProductionPilotControl,
)
from services.agent_runtime.app.action.production_pilot_pre_enable_evidence import (
    ProductionPilotPreEnableEvidenceService,
)


PRODUCTION_PILOT_FINAL_HANDOFF_ACKNOWLEDGEMENT = (
    "I_CONFIRM_OOM_PILOT_FINAL_ZERO_WRITE_HANDOFF_V1"
)
_SCHEMA_VERSION = "production_pilot_final_handoff_rehearsal/v1"
_SHA256_PATTERN = r"[0-9a-f]{64}"
_RELEASE_PATTERN = r"sha256:[0-9a-f]{64}"
_IDENTIFIER_PATTERN = (
    r"[A-Za-z0-9](?:[A-Za-z0-9_.:/-]{0,126}[A-Za-z0-9])?"
)
_MAX_REFERENCE_BYTES = 1024 * 1024


class ProductionPilotFinalHandoffError(RuntimeError):
    """The final zero-write handoff could not be evaluated safely."""


class ProductionPilotFinalHandoffConflictError(
    ProductionPilotFinalHandoffError
):
    """The handoff request is stale or conflicts with live evidence."""


class ProductionPilotFinalHandoffRequest(BaseModel):
    """Operator attestations bound to one exact pre-enable evidence pack."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    expected_evidence_sha256: str = Field(
        pattern=_SHA256_PATTERN,
    )
    expected_pilot_id: str = Field(
        min_length=1,
        max_length=128,
    )
    expected_change_ticket: str = Field(
        min_length=1,
        max_length=128,
    )
    expected_runbook_version: str = Field(
        min_length=1,
        max_length=128,
    )
    deployment_release_sha256: str = Field(
        pattern=_RELEASE_PATTERN,
    )
    on_call_owner_id: str = Field(
        min_length=1,
        max_length=128,
    )
    rollback_owner_id: str = Field(
        min_length=1,
        max_length=128,
    )
    reconciliation_owner_id: str = Field(
        min_length=1,
        max_length=128,
    )
    deployment_release_evidence_reviewed: Literal[True]
    preflight_credential_reference_reviewed: Literal[True]
    production_credential_reference_reviewed: Literal[True]
    tls_policy_evidence_reviewed: Literal[True]
    security_matrix_evidence_reviewed: Literal[True]
    monitoring_evidence_reviewed: Literal[True]
    rollback_evidence_reviewed: Literal[True]
    reconciliation_evidence_reviewed: Literal[True]
    acknowledgement: str = Field(
        min_length=1,
        max_length=128,
    )

    @model_validator(mode="after")
    def validate_handoff(
        self,
    ) -> "ProductionPilotFinalHandoffRequest":
        if (
            self.acknowledgement
            != PRODUCTION_PILOT_FINAL_HANDOFF_ACKNOWLEDGEMENT
        ):
            raise ValueError(
                "Production Pilot final handoff acknowledgement is invalid"
            )
        owner_ids = (
            self.on_call_owner_id,
            self.rollback_owner_id,
            self.reconciliation_owner_id,
        )
        if any(
            fullmatch(_IDENTIFIER_PATTERN, item) is None
            for item in owner_ids
        ):
            raise ValueError(
                "Production Pilot final handoff owner is invalid"
            )
        if len(set(owner_ids)) != len(owner_ids):
            raise ValueError(
                "Production Pilot final handoff owners must be distinct"
            )
        return self


class ProductionPilotFinalHandoffReport(BaseModel):
    """Bounded result of the final offline, zero-write handoff rehearsal."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    schema_version: Literal[
        "production_pilot_final_handoff_rehearsal/v1"
    ] = _SCHEMA_VERSION
    approval_id: str
    incident_id: str
    artifact_id: str
    ceremony_id: str
    pilot_id: str
    change_ticket: str
    runbook_version: str
    deployment_release_sha256: str = Field(
        pattern=_RELEASE_PATTERN,
    )
    evidence_sha256: str = Field(
        pattern=_SHA256_PATTERN,
    )
    configuration_sha256: str = Field(
        pattern=_SHA256_PATTERN,
    )
    operator_id: str
    approval_operator_id: str | None
    ceremony_reviewer_operator_id: str
    executor_operator_id: str
    on_call_owner_id: str
    rollback_owner_id: str
    reconciliation_owner_id: str

    feature_gate_disabled: bool
    production_executor_absent: bool
    action_runtime_production_executor_absent: bool
    kill_switch_engaged: bool
    pilot_window_active: bool
    exact_single_target: bool
    pre_enable_evidence_ready: bool
    preflight_runtime_configured: bool
    preflight_runtime_binding_consistent: bool
    clean_https_origin_configured: bool
    tls_verification_required: bool
    tls_runtime_matches_configuration: bool
    ca_mode: Literal[
        "system_trust_store",
        "custom_ca_file",
        "invalid",
    ]
    ca_reference_available: bool
    preflight_credential_reference_type: Literal[
        "environment",
        "file",
        "invalid",
    ]
    production_credential_reference_type: Literal[
        "environment",
        "file",
        "invalid",
    ]
    credential_references_separate: bool
    preflight_credential_reference_available: bool
    production_credential_reference_available: bool
    credential_content_read_count: Literal[0] = 0
    credential_content_validated: Literal[False] = False
    tls_handshake_performed: Literal[False] = False
    requires_guarded_startup_credential_validation: Literal[True] = True
    requires_live_tls_recheck_before_enablement: Literal[True] = True

    approval_reviewer_separated: bool
    approval_executor_separated: bool
    reviewer_executor_separated: bool
    handoff_owner_ids_distinct: bool
    executor_handoff_owners_separated: bool
    operator_attestations_complete: bool
    deployment_release_evidence_reviewed: bool
    security_route_count: Literal[25] = 25
    security_role_count: Literal[7] = 7
    security_matrix_reviewed: bool

    passed: bool
    blockers: tuple[str, ...]
    read_only: Literal[True] = True
    zero_write: Literal[True] = True
    storage_read_only: Literal[True] = True
    storage_write_count: Literal[0] = 0
    durable_claim_created: Literal[False] = False
    budget_reservation_count: Literal[0] = 0
    network_call_count: Literal[0] = 0
    external_call_count: Literal[0] = 0
    kubernetes_call_count: Literal[0] = 0
    production_executor_call_count: Literal[0] = 0
    verification_call_count: Literal[0] = 0
    real_write_attempted: Literal[False] = False
    authorizes_feature_enablement: Literal[False] = False
    authorizes_execution: Literal[False] = False
    automatic_resume_allowed: Literal[False] = False
    requires_controlled_change_record: Literal[True] = True
    requires_live_recheck_before_resume: Literal[True] = True
    report_sha256: str = Field(
        pattern=_SHA256_PATTERN,
    )

    @model_validator(mode="after")
    def validate_report(
        self,
    ) -> "ProductionPilotFinalHandoffReport":
        if self.blockers and self.passed:
            raise ValueError(
                "Blocked Production Pilot handoff cannot pass"
            )
        if not self.blockers and not self.passed:
            raise ValueError(
                "Unblocked Production Pilot handoff must pass"
            )
        expected = _digest_model(
            self,
            excluded={"report_sha256"},
        )
        if self.report_sha256 != expected:
            raise ValueError(
                "Production Pilot final handoff report digest is invalid"
            )
        return self


class ProductionPilotFinalHandoffRehearsalService:
    """Evaluate final deployment and operator evidence without side effects."""

    def __init__(
        self,
        *,
        pilot_control: KubernetesProductionPilotControl,
        pre_enable_evidence_service: (
            ProductionPilotPreEnableEvidenceService
        ),
        preflight_resolver: KubernetesPreflightResolver | None,
        production_executor_configured: bool,
        action_runtime_production_executor_configured: bool,
        reference_probe: Callable[[str, str], bool] | None = None,
    ) -> None:
        if not isinstance(
            pilot_control,
            KubernetesProductionPilotControl,
        ):
            raise TypeError(
                "Production Pilot final handoff control is invalid"
            )
        if not isinstance(
            pre_enable_evidence_service,
            ProductionPilotPreEnableEvidenceService,
        ):
            raise TypeError(
                "Production Pilot final handoff evidence service is invalid"
            )
        if (
            preflight_resolver is not None
            and not isinstance(
                preflight_resolver,
                KubernetesPreflightResolver,
            )
        ):
            raise TypeError(
                "Production Pilot final handoff preflight resolver is invalid"
            )
        if reference_probe is not None and not callable(reference_probe):
            raise TypeError(
                "Production Pilot final handoff reference probe is invalid"
            )
        self.pilot_control = pilot_control
        self.pre_enable_evidence_service = pre_enable_evidence_service
        self.preflight_resolver = preflight_resolver
        self.production_executor_configured = bool(
            production_executor_configured
        )
        self.action_runtime_production_executor_configured = bool(
            action_runtime_production_executor_configured
        )
        self.reference_probe = (
            reference_probe
            or _default_reference_probe
        )

    async def rehearse(
        self,
        *,
        approval_id: str,
        operator_id: str,
        request: ProductionPilotFinalHandoffRequest,
    ) -> ProductionPilotFinalHandoffReport:
        _required_identifier(
            approval_id,
            "Approval ID",
        )
        _required_identifier(
            operator_id,
            "operator ID",
        )
        if not isinstance(
            request,
            ProductionPilotFinalHandoffRequest,
        ):
            raise TypeError(
                "Production Pilot final handoff request is invalid"
            )

        evidence = await self.pre_enable_evidence_service.get(
            approval_id
        )
        if evidence is None:
            raise ProductionPilotFinalHandoffError(
                "Production Pilot pre-enable evidence is unavailable"
            )
        if (
            request.expected_evidence_sha256
            != evidence.evidence_sha256
        ):
            raise ProductionPilotFinalHandoffConflictError(
                "Production Pilot pre-enable evidence has changed"
            )
        expected_bindings = (
            (request.expected_pilot_id, evidence.pilot_id),
            (request.expected_change_ticket, evidence.change_ticket),
            (
                request.expected_runbook_version,
                evidence.runbook_version,
            ),
        )
        if any(
            expected != actual
            for expected, actual in expected_bindings
        ):
            raise ProductionPilotFinalHandoffConflictError(
                "Production Pilot final handoff binding has changed"
            )
        if operator_id != evidence.executor_operator_id:
            raise ProductionPilotFinalHandoffError(
                "Only the exact reviewed Executor may run final handoff"
            )

        preflight = self.pilot_control.preflight_config
        execution = self.pilot_control.execution_config
        preflight_reference = _credential_reference(
            preflight.bearer_token_env,
            preflight.bearer_token_file,
        )
        production_reference = _credential_reference(
            execution.bearer_token_env,
            execution.bearer_token_file,
        )
        preflight_reference_available = self._probe(
            preflight_reference
        )
        production_reference_available = self._probe(
            production_reference
        )

        ca_mode: str
        if preflight.ca_file is None:
            ca_mode = "system_trust_store"
            ca_reference_available = True
        else:
            ca_mode = "custom_ca_file"
            ca_reference_available = self._safe_probe(
                "ca_file",
                preflight.ca_file,
            )

        resolver = self.preflight_resolver
        preflight_runtime_configured = resolver is not None
        clean_https = _clean_https_origin(
            preflight.api_url
        )
        tls_verification_required = (
            resolver is not None
            and (
                resolver.verify_tls is True
                or (
                    isinstance(resolver.verify_tls, str)
                    and bool(resolver.verify_tls.strip())
                )
            )
        )
        tls_runtime_matches_configuration = (
            resolver is not None
            and (
                (
                    preflight.ca_file is None
                    and resolver.verify_tls is True
                )
                or (
                    preflight.ca_file is not None
                    and resolver.verify_tls
                    == preflight.ca_file
                )
            )
        )
        preflight_binding_consistent = (
            _preflight_binding_consistent(
                resolver=resolver,
                evidence=evidence,
                control=self.pilot_control,
            )
        )

        approval_operator_id = (
            evidence.approval_decision_operator_id
        )
        approval_reviewer_separated = (
            approval_operator_id is not None
            and approval_operator_id
            != evidence.ceremony_reviewer_operator_id
        )
        approval_executor_separated = (
            approval_operator_id is not None
            and approval_operator_id
            != evidence.executor_operator_id
        )
        reviewer_executor_separated = (
            evidence.ceremony_reviewer_operator_id
            != evidence.executor_operator_id
        )
        handoff_owners = {
            request.on_call_owner_id,
            request.rollback_owner_id,
            request.reconciliation_owner_id,
        }
        handoff_owner_ids_distinct = len(handoff_owners) == 3
        executor_handoff_owners_separated = (
            evidence.executor_operator_id
            not in handoff_owners
        )

        blockers = list(
            evidence.evidence_blockers
        )
        checks = (
            (
                evidence.ready_for_sign_off,
                "pre_enable_evidence_not_ready",
            ),
            (
                not evidence.production_execution_enabled,
                "production_execution_must_remain_disabled",
            ),
            (
                not self.production_executor_configured,
                "production_executor_must_remain_absent",
            ),
            (
                not self.action_runtime_production_executor_configured,
                "action_runtime_production_executor_must_remain_absent",
            ),
            (
                evidence.kill_switch_state == "engaged",
                "kill_switch_must_remain_engaged",
            ),
            (
                evidence.pilot_window_state == "active",
                "pilot_window_not_active",
            ),
            (
                evidence.exact_single_target,
                "exact_single_target_required",
            ),
            (
                preflight_runtime_configured,
                "preflight_runtime_unavailable",
            ),
            (
                preflight_binding_consistent,
                "preflight_runtime_binding_inconsistent",
            ),
            (
                clean_https,
                "clean_https_origin_required",
            ),
            (
                tls_verification_required,
                "tls_verification_required",
            ),
            (
                tls_runtime_matches_configuration,
                "tls_runtime_configuration_mismatch",
            ),
            (
                ca_reference_available,
                "ca_reference_unavailable",
            ),
            (
                preflight_reference is not None,
                "preflight_credential_reference_invalid",
            ),
            (
                production_reference is not None,
                "production_credential_reference_invalid",
            ),
            (
                preflight_reference is not None
                and production_reference is not None
                and _references_separate(
                    preflight_reference,
                    production_reference,
                ),
                "credential_references_not_separate",
            ),
            (
                preflight_reference_available,
                "preflight_credential_reference_unavailable",
            ),
            (
                production_reference_available,
                "production_credential_reference_unavailable",
            ),
            (
                approval_reviewer_separated,
                "approval_reviewer_not_separated",
            ),
            (
                approval_executor_separated,
                "approval_executor_not_separated",
            ),
            (
                reviewer_executor_separated,
                "reviewer_executor_not_separated",
            ),
            (
                handoff_owner_ids_distinct,
                "handoff_owners_not_distinct",
            ),
            (
                executor_handoff_owners_separated,
                "executor_handoff_owners_not_separated",
            ),
        )
        for passed, blocker in checks:
            if not passed:
                blockers.append(blocker)
        unique_blockers = tuple(
            dict.fromkeys(blockers)
        )

        configuration_values = _safe_configuration_values(
            control=self.pilot_control,
            preflight_reference=preflight_reference,
            production_reference=production_reference,
            ca_mode=ca_mode,
        )
        values = {
            "approval_id": evidence.approval_id,
            "incident_id": evidence.incident_id,
            "artifact_id": evidence.artifact_id,
            "ceremony_id": evidence.ceremony_id,
            "pilot_id": evidence.pilot_id,
            "change_ticket": evidence.change_ticket,
            "runbook_version": evidence.runbook_version,
            "deployment_release_sha256": (
                request.deployment_release_sha256
            ),
            "evidence_sha256": evidence.evidence_sha256,
            "configuration_sha256": _digest_mapping(
                configuration_values
            ),
            "operator_id": operator_id,
            "approval_operator_id": approval_operator_id,
            "ceremony_reviewer_operator_id": (
                evidence.ceremony_reviewer_operator_id
            ),
            "executor_operator_id": evidence.executor_operator_id,
            "on_call_owner_id": request.on_call_owner_id,
            "rollback_owner_id": request.rollback_owner_id,
            "reconciliation_owner_id": (
                request.reconciliation_owner_id
            ),
            "feature_gate_disabled": (
                not evidence.production_execution_enabled
            ),
            "production_executor_absent": (
                not self.production_executor_configured
            ),
            "action_runtime_production_executor_absent": (
                not self.action_runtime_production_executor_configured
            ),
            "kill_switch_engaged": (
                evidence.kill_switch_state == "engaged"
            ),
            "pilot_window_active": (
                evidence.pilot_window_state == "active"
            ),
            "exact_single_target": evidence.exact_single_target,
            "pre_enable_evidence_ready": evidence.ready_for_sign_off,
            "preflight_runtime_configured": (
                preflight_runtime_configured
            ),
            "preflight_runtime_binding_consistent": (
                preflight_binding_consistent
            ),
            "clean_https_origin_configured": clean_https,
            "tls_verification_required": tls_verification_required,
            "tls_runtime_matches_configuration": (
                tls_runtime_matches_configuration
            ),
            "ca_mode": ca_mode,
            "ca_reference_available": ca_reference_available,
            "preflight_credential_reference_type": (
                _reference_type(preflight_reference)
            ),
            "production_credential_reference_type": (
                _reference_type(production_reference)
            ),
            "credential_references_separate": (
                preflight_reference is not None
                and production_reference is not None
                and _references_separate(
                    preflight_reference,
                    production_reference,
                )
            ),
            "preflight_credential_reference_available": (
                preflight_reference_available
            ),
            "production_credential_reference_available": (
                production_reference_available
            ),
            "approval_reviewer_separated": (
                approval_reviewer_separated
            ),
            "approval_executor_separated": (
                approval_executor_separated
            ),
            "reviewer_executor_separated": (
                reviewer_executor_separated
            ),
            "handoff_owner_ids_distinct": handoff_owner_ids_distinct,
            "executor_handoff_owners_separated": (
                executor_handoff_owners_separated
            ),
            "operator_attestations_complete": True,
            "deployment_release_evidence_reviewed": (
                request.deployment_release_evidence_reviewed
            ),
            "security_matrix_reviewed": (
                request.security_matrix_evidence_reviewed
            ),
            "passed": not unique_blockers,
            "blockers": unique_blockers,
        }
        unsigned = ProductionPilotFinalHandoffReport.model_construct(
            **values,
            report_sha256="0" * 64,
        )
        return ProductionPilotFinalHandoffReport(
            **values,
            report_sha256=_digest_model(
                unsigned,
                excluded={"report_sha256"},
            ),
        )

    def _probe(
        self,
        reference: tuple[str, str] | None,
    ) -> bool:
        if reference is None:
            return False
        return self._safe_probe(
            reference[0],
            reference[1],
        )

    def _safe_probe(
        self,
        kind: str,
        reference: str,
    ) -> bool:
        try:
            return self.reference_probe(
                kind,
                reference,
            ) is True
        except Exception:
            return False


def _default_reference_probe(
    kind: str,
    reference: str,
) -> bool:
    if kind == "environment":
        return reference in environ
    if kind not in {"file", "ca_file"}:
        return False
    try:
        path = Path(reference)
        size = path.stat().st_size
        return (
            path.is_file()
            and size > 0
            and size <= _MAX_REFERENCE_BYTES
        )
    except OSError:
        return False


def _credential_reference(
    environment_name: str | None,
    file_name: str | None,
) -> tuple[str, str] | None:
    if environment_name is not None and file_name is None:
        return "environment", environment_name
    if file_name is not None and environment_name is None:
        return "file", file_name
    return None


def _reference_type(
    reference: tuple[str, str] | None,
) -> str:
    return reference[0] if reference is not None else "invalid"


def _normalized_reference(
    reference: tuple[str, str],
) -> str:
    kind, value = reference
    if kind == "file":
        return str(
            Path(value).expanduser().resolve(
                strict=False
            )
        )
    return value


def _reference_fingerprint(
    reference: tuple[str, str] | None,
) -> str:
    if reference is None:
        return "invalid"
    kind, _ = reference
    return sha256(
        (
            kind
            + "\0"
            + _normalized_reference(reference)
        ).encode("utf-8")
    ).hexdigest()


def _references_separate(
    first: tuple[str, str],
    second: tuple[str, str],
) -> bool:
    return _reference_fingerprint(first) != _reference_fingerprint(second)


def _clean_https_origin(
    value: str | None,
) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return (
        parsed.scheme == "https"
        and bool(parsed.netloc)
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
        and parsed.path in {"", "/"}
    )


def _preflight_binding_consistent(
    *,
    resolver: KubernetesPreflightResolver | None,
    evidence,
    control: KubernetesProductionPilotControl,
) -> bool:
    if resolver is None:
        return False
    targets = tuple(
        resolver.policy.allowed_targets
    )
    if len(targets) != 1:
        return False
    target = targets[0]
    return (
        resolver.api_url == control.preflight_config.api_url
        and resolver.cluster_name == control.preflight_config.cluster_name
        and target.cluster == evidence.cluster
        and target.namespace == evidence.namespace
        and target.name == evidence.workload_name
        and target.container == evidence.container
        and resolver.policy.policy_version
        == evidence.safety_policy_version
    )


def _safe_configuration_values(
    *,
    control: KubernetesProductionPilotControl,
    preflight_reference: tuple[str, str] | None,
    production_reference: tuple[str, str] | None,
    ca_mode: str,
) -> dict[str, Any]:
    preflight = control.preflight_config
    execution = control.execution_config
    pilot = control.config
    return {
        "pilot_enabled": pilot.enabled,
        "pilot_id": pilot.pilot_id,
        "change_ticket": pilot.change_ticket,
        "runbook_version": pilot.runbook_version,
        "authorized_operator_count": len(
            pilot.authorized_operator_ids
        ),
        "preflight_enabled": preflight.enabled,
        "cluster": preflight.cluster_name,
        "target_count": len(preflight.allowed_targets),
        "policy_version": preflight.policy_version,
        "increase_percent": preflight.increase_percent,
        "contract_ttl_seconds": preflight.contract_ttl_seconds,
        "request_timeout_seconds": preflight.request_timeout_seconds,
        "field_manager": preflight.field_manager,
        "clean_https_origin": _clean_https_origin(preflight.api_url),
        "ca_mode": ca_mode,
        "preflight_credential_reference_type": (
            _reference_type(preflight_reference)
        ),
        "preflight_credential_reference_sha256": (
            _reference_fingerprint(preflight_reference)
        ),
        "production_execution_enabled": execution.enabled,
        "production_credential_reference_type": (
            _reference_type(production_reference)
        ),
        "production_credential_reference_sha256": (
            _reference_fingerprint(production_reference)
        ),
        "ca_reference_sha256": (
            "system_trust_store"
            if preflight.ca_file is None
            else sha256(
                str(
                    Path(preflight.ca_file).expanduser().resolve(
                        strict=False
                    )
                ).encode("utf-8")
            ).hexdigest()
        ),
        "production_request_timeout_seconds": (
            execution.request_timeout_seconds
        ),
        "minimum_remaining_seconds": (
            execution.minimum_remaining_seconds
        ),
    }


def _required_identifier(
    value: Any,
    label: str,
) -> str:
    if (
        not isinstance(value, str)
        or fullmatch(_IDENTIFIER_PATTERN, value) is None
    ):
        raise ValueError(
            f"Production Pilot final handoff {label} is invalid"
        )
    return value


def _digest_mapping(
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


def _digest_model(
    model: BaseModel,
    *,
    excluded: set[str],
) -> str:
    return _digest_mapping(
        model.model_dump(
            mode="json",
            exclude=excluded,
        )
    )


__all__ = [
    "PRODUCTION_PILOT_FINAL_HANDOFF_ACKNOWLEDGEMENT",
    "ProductionPilotFinalHandoffConflictError",
    "ProductionPilotFinalHandoffError",
    "ProductionPilotFinalHandoffRehearsalService",
    "ProductionPilotFinalHandoffReport",
    "ProductionPilotFinalHandoffRequest",
]
