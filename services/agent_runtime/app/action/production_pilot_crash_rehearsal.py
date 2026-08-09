import json

from enum import Enum
from hashlib import sha256
from re import fullmatch
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)


_IDENTIFIER_PATTERN = (
    r"[A-Za-z0-9](?:[A-Za-z0-9_.:/-]{0,126}[A-Za-z0-9])?"
)
_REPORT_SCHEMA_VERSION = (
    "production_pilot_crash_recovery_rehearsal/v1"
)


class ProductionPilotCrashCutPoint(str, Enum):
    """Every durable boundary in the bounded OOMKilled Pilot workflow."""

    PREFLIGHT_ARTIFACT_COMMITTED = (
        "preflight_artifact_committed"
    )
    APPROVAL_PENDING = "approval_pending"
    APPROVAL_APPROVED_CEREMONY_READY = (
        "approval_approved_ceremony_ready"
    )
    ACTION_EXECUTION_CLAIMED = (
        "action_execution_claimed"
    )
    CEREMONY_ACTIVATED = "ceremony_activated"
    PILOT_BUDGET_RESERVED = "pilot_budget_reserved"
    PILOT_BUDGET_CONSUMED = "pilot_budget_consumed"
    ACTION_EXECUTION_SUCCEEDED = (
        "action_execution_succeeded"
    )
    ACTION_EXECUTION_FAILED = "action_execution_failed"
    ACTION_EXECUTION_INDETERMINATE = (
        "action_execution_indeterminate"
    )
    VERIFICATION_CLAIMED = "verification_claimed"
    VERIFICATION_PASSED = "verification_passed"
    VERIFICATION_NOT_PASSED = "verification_not_passed"


class ProductionPilotCrashRecoveryMode(str, Enum):
    """Only allowlisted recovery modes may leave the rehearsal boundary."""

    COMPLETE_PREPARATION = "complete_preparation"
    AWAIT_HUMAN_APPROVAL = "await_human_approval"
    FIRST_RESUME_ONLY = "first_authenticated_resume_only"
    MANUAL_RECONCILIATION = "manual_reconciliation"
    VERIFICATION_ONLY = "verification_only"
    TERMINAL_NO_ACTION_RETRY = "terminal_no_action_retry"


class ProductionPilotCrashRecoveryGuidance(str, Enum):
    """Bounded guidance codes without persisted error or credential text."""

    CREATE_PENDING_APPROVAL = "create_pending_approval"
    COMPLETE_HUMAN_APPROVAL = "complete_human_approval"
    RERUN_LIVE_READINESS = "rerun_live_readiness_before_resume"
    USE_ONE_AUTHENTICATED_RESUME = "use_one_authenticated_resume"
    ENGAGE_KILL_SWITCH = "engage_kill_switch"
    DO_NOT_RETRY_RESUME = "do_not_retry_resume"
    INSPECT_DEPLOYMENT_READ_ONLY = (
        "inspect_deployment_state_read_only"
    )
    RECONCILE_EXISTING_EXECUTION = (
        "reconcile_existing_action_execution"
    )
    OBSERVE_EXACTLY_ONCE_VERIFICATION = (
        "observe_exactly_once_verification"
    )
    DO_NOT_REPEAT_ACTION = "do_not_repeat_action"
    CREATE_NEW_PILOT = "create_new_pilot_for_another_attempt"
    KEEP_INCIDENT_OPEN = "keep_incident_open"
    CLOSE_PILOT_AND_REVIEW = "close_pilot_and_review"


_CHECKPOINT_BLUEPRINTS = (
    {
        "cut_point": "preflight_artifact_committed",
        "artifact_state": "committed",
        "approval_state": "missing",
        "ceremony_state": "missing",
        "execution_state": "missing",
        "budget_state": "missing",
        "verification_state": "missing",
        "recovery_mode": "complete_preparation",
        "authenticated_first_resume_allowed": False,
        "verification_recovery_allowed": False,
        "manual_reconciliation_required": False,
        "guidance": ("create_pending_approval",),
    },
    {
        "cut_point": "approval_pending",
        "artifact_state": "committed",
        "approval_state": "pending",
        "ceremony_state": "missing",
        "execution_state": "missing",
        "budget_state": "missing",
        "verification_state": "missing",
        "recovery_mode": "await_human_approval",
        "authenticated_first_resume_allowed": False,
        "verification_recovery_allowed": False,
        "manual_reconciliation_required": False,
        "guidance": ("complete_human_approval",),
    },
    {
        "cut_point": "approval_approved_ceremony_ready",
        "artifact_state": "committed",
        "approval_state": "approved",
        "ceremony_state": "ready",
        "execution_state": "missing",
        "budget_state": "missing",
        "verification_state": "missing",
        "recovery_mode": "first_authenticated_resume_only",
        "authenticated_first_resume_allowed": True,
        "verification_recovery_allowed": False,
        "manual_reconciliation_required": False,
        "guidance": (
            "rerun_live_readiness_before_resume",
            "use_one_authenticated_resume",
        ),
    },
    {
        "cut_point": "action_execution_claimed",
        "artifact_state": "committed",
        "approval_state": "approved",
        "ceremony_state": "ready",
        "execution_state": "running",
        "budget_state": "missing",
        "verification_state": "missing",
        "recovery_mode": "manual_reconciliation",
        "authenticated_first_resume_allowed": False,
        "verification_recovery_allowed": False,
        "manual_reconciliation_required": True,
        "guidance": (
            "engage_kill_switch",
            "do_not_retry_resume",
            "inspect_deployment_state_read_only",
            "reconcile_existing_action_execution",
        ),
    },
    {
        "cut_point": "ceremony_activated",
        "artifact_state": "committed",
        "approval_state": "approved",
        "ceremony_state": "activated",
        "execution_state": "running",
        "budget_state": "missing",
        "verification_state": "missing",
        "recovery_mode": "manual_reconciliation",
        "authenticated_first_resume_allowed": False,
        "verification_recovery_allowed": False,
        "manual_reconciliation_required": True,
        "guidance": (
            "engage_kill_switch",
            "do_not_retry_resume",
            "inspect_deployment_state_read_only",
            "reconcile_existing_action_execution",
        ),
    },
    {
        "cut_point": "pilot_budget_reserved",
        "artifact_state": "committed",
        "approval_state": "approved",
        "ceremony_state": "activated",
        "execution_state": "running",
        "budget_state": "reserved",
        "verification_state": "missing",
        "recovery_mode": "manual_reconciliation",
        "authenticated_first_resume_allowed": False,
        "verification_recovery_allowed": False,
        "manual_reconciliation_required": True,
        "guidance": (
            "engage_kill_switch",
            "do_not_retry_resume",
            "inspect_deployment_state_read_only",
            "reconcile_existing_action_execution",
        ),
    },
    {
        "cut_point": "pilot_budget_consumed",
        "artifact_state": "committed",
        "approval_state": "approved",
        "ceremony_state": "activated",
        "execution_state": "running",
        "budget_state": "consumed",
        "verification_state": "missing",
        "recovery_mode": "manual_reconciliation",
        "authenticated_first_resume_allowed": False,
        "verification_recovery_allowed": False,
        "manual_reconciliation_required": True,
        "guidance": (
            "engage_kill_switch",
            "do_not_retry_resume",
            "inspect_deployment_state_read_only",
            "reconcile_existing_action_execution",
        ),
    },
    {
        "cut_point": "action_execution_succeeded",
        "artifact_state": "committed",
        "approval_state": "approved",
        "ceremony_state": "activated",
        "execution_state": "succeeded",
        "budget_state": "consumed",
        "verification_state": "missing",
        "recovery_mode": "verification_only",
        "authenticated_first_resume_allowed": False,
        "verification_recovery_allowed": True,
        "manual_reconciliation_required": False,
        "guidance": (
            "do_not_repeat_action",
            "observe_exactly_once_verification",
        ),
    },
    {
        "cut_point": "action_execution_failed",
        "artifact_state": "committed",
        "approval_state": "approved",
        "ceremony_state": "activated",
        "execution_state": "failed",
        "budget_state": "reserved",
        "verification_state": "missing",
        "recovery_mode": "terminal_no_action_retry",
        "authenticated_first_resume_allowed": False,
        "verification_recovery_allowed": False,
        "manual_reconciliation_required": False,
        "guidance": (
            "do_not_repeat_action",
            "create_new_pilot_for_another_attempt",
        ),
    },
    {
        "cut_point": "action_execution_indeterminate",
        "artifact_state": "committed",
        "approval_state": "approved",
        "ceremony_state": "activated",
        "execution_state": "indeterminate",
        "budget_state": "consumed",
        "verification_state": "missing",
        "recovery_mode": "manual_reconciliation",
        "authenticated_first_resume_allowed": False,
        "verification_recovery_allowed": False,
        "manual_reconciliation_required": True,
        "guidance": (
            "engage_kill_switch",
            "do_not_retry_resume",
            "inspect_deployment_state_read_only",
            "reconcile_existing_action_execution",
        ),
    },
    {
        "cut_point": "verification_claimed",
        "artifact_state": "committed",
        "approval_state": "approved",
        "ceremony_state": "activated",
        "execution_state": "succeeded",
        "budget_state": "consumed",
        "verification_state": "running",
        "recovery_mode": "verification_only",
        "authenticated_first_resume_allowed": False,
        "verification_recovery_allowed": True,
        "manual_reconciliation_required": False,
        "guidance": (
            "do_not_repeat_action",
            "observe_exactly_once_verification",
        ),
    },
    {
        "cut_point": "verification_passed",
        "artifact_state": "committed",
        "approval_state": "approved",
        "ceremony_state": "activated",
        "execution_state": "succeeded",
        "budget_state": "consumed",
        "verification_state": "passed",
        "recovery_mode": "terminal_no_action_retry",
        "authenticated_first_resume_allowed": False,
        "verification_recovery_allowed": False,
        "manual_reconciliation_required": False,
        "guidance": (
            "do_not_repeat_action",
            "close_pilot_and_review",
        ),
    },
    {
        "cut_point": "verification_not_passed",
        "artifact_state": "committed",
        "approval_state": "approved",
        "ceremony_state": "activated",
        "execution_state": "succeeded",
        "budget_state": "consumed",
        "verification_state": "failed_or_inconclusive",
        "recovery_mode": "terminal_no_action_retry",
        "authenticated_first_resume_allowed": False,
        "verification_recovery_allowed": False,
        "manual_reconciliation_required": False,
        "guidance": (
            "do_not_repeat_action",
            "keep_incident_open",
            "close_pilot_and_review",
        ),
    },
)


class ProductionPilotCrashCheckpointResult(BaseModel):
    """One synthetic crash boundary and its only safe recovery policy."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        use_enum_values=True,
        validate_default=True,
    )

    sequence: int = Field(
        ge=1,
        le=len(_CHECKPOINT_BLUEPRINTS),
    )
    cut_point: ProductionPilotCrashCutPoint
    artifact_state: Literal[
        "missing",
        "committed",
    ]
    approval_state: Literal[
        "missing",
        "pending",
        "approved",
    ]
    ceremony_state: Literal[
        "missing",
        "ready",
        "activated",
    ]
    execution_state: Literal[
        "missing",
        "running",
        "succeeded",
        "failed",
        "indeterminate",
    ]
    budget_state: Literal[
        "missing",
        "reserved",
        "consumed",
    ]
    verification_state: Literal[
        "missing",
        "running",
        "passed",
        "failed_or_inconclusive",
    ]
    recovery_mode: ProductionPilotCrashRecoveryMode
    authenticated_first_resume_allowed: bool
    automatic_action_replay_allowed: Literal[False] = False
    production_executor_call_allowed: Literal[False] = False
    budget_reset_allowed: Literal[False] = False
    verification_recovery_allowed: bool
    manual_reconciliation_required: bool
    guidance: tuple[
        ProductionPilotCrashRecoveryGuidance,
        ...,
    ]
    invariant_passed: Literal[True] = True

    @model_validator(mode="after")
    def validate_safe_blueprint(
        self,
    ) -> "ProductionPilotCrashCheckpointResult":
        expected = _CHECKPOINT_BLUEPRINTS[
            self.sequence - 1
        ]
        actual = self.model_dump(
            mode="json",
            exclude={
                "sequence",
                "automatic_action_replay_allowed",
                "production_executor_call_allowed",
                "budget_reset_allowed",
                "invariant_passed",
            },
        )
        expected_json = {
            **expected,
            "guidance": list(
                expected["guidance"]
            ),
        }
        if actual != expected_json:
            raise ValueError(
                "Production Pilot crash recovery checkpoint is unsafe"
            )
        return self


class ProductionPilotCrashRecoveryRehearsalReport(BaseModel):
    """Digest-bound proof of the pure, zero-write recovery policy."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    schema_version: Literal[
        "production_pilot_crash_recovery_rehearsal/v1"
    ] = _REPORT_SCHEMA_VERSION
    requested_by: str = Field(
        min_length=1,
        max_length=128,
    )
    passed: bool
    synthetic_rehearsal: Literal[True] = True
    live_state_checked: Literal[False] = False
    authorizes_enablement: Literal[False] = False
    authorizes_execution: Literal[False] = False
    automatic_action_replay_allowed: Literal[False] = False
    checkpoint_count: Literal[13] = len(
        _CHECKPOINT_BLUEPRINTS
    )
    passed_checkpoint_count: int = Field(
        ge=0,
        le=len(_CHECKPOINT_BLUEPRINTS),
    )
    durable_claim_created: Literal[False] = False
    storage_read_count: Literal[0] = 0
    storage_write_count: Literal[0] = 0
    external_call_count: Literal[0] = 0
    kubernetes_call_count: Literal[0] = 0
    production_executor_call_count: Literal[0] = 0
    verification_call_count: Literal[0] = 0
    budget_reservation_count: Literal[0] = 0
    real_write_attempted: Literal[False] = False
    checkpoints: tuple[
        ProductionPilotCrashCheckpointResult,
        ...,
    ]
    report_sha256: str = Field(
        pattern=r"[0-9a-f]{64}",
    )

    @model_validator(mode="after")
    def validate_complete_report(
        self,
    ) -> "ProductionPilotCrashRecoveryRehearsalReport":
        if fullmatch(
            _IDENTIFIER_PATTERN,
            self.requested_by,
        ) is None:
            raise ValueError(
                "Production Pilot rehearsal operator is invalid"
            )
        expected_count = len(
            _CHECKPOINT_BLUEPRINTS
        )
        if (
            len(self.checkpoints) != expected_count
            or self.passed_checkpoint_count != expected_count
            or not self.passed
            or not all(
                item.invariant_passed
                for item in self.checkpoints
            )
        ):
            raise ValueError(
                "Production Pilot crash recovery rehearsal is incomplete"
            )
        if tuple(
            item.sequence
            for item in self.checkpoints
        ) != tuple(
            range(1, expected_count + 1)
        ):
            raise ValueError(
                "Production Pilot crash recovery sequence is invalid"
            )
        if len(
            {
                item.cut_point
                for item in self.checkpoints
            }
        ) != expected_count:
            raise ValueError(
                "Production Pilot crash recovery cut points are incomplete"
            )
        expected_digest = _report_digest(
            requested_by=self.requested_by,
            checkpoints=self.checkpoints,
        )
        if self.report_sha256 != expected_digest:
            raise ValueError(
                "Production Pilot crash recovery report digest is invalid"
            )
        return self


class ProductionPilotCrashRecoveryRehearsalService:
    """
    Evaluate every durable cut point without reading or mutating live state.

    This is a synthetic policy rehearsal, not a Go/No-Go decision. It never
    creates a Claim, resets a budget, contacts Kubernetes, or starts
    Verification. Live readiness and durable workflow queries remain required
    before any operator action.
    """

    async def run(
        self,
        *,
        operator_id: str,
    ) -> ProductionPilotCrashRecoveryRehearsalReport:
        if (
            not isinstance(operator_id, str)
            or fullmatch(
                _IDENTIFIER_PATTERN,
                operator_id,
            )
            is None
        ):
            raise ValueError(
                "Production Pilot rehearsal operator is invalid"
            )

        checkpoints = tuple(
            ProductionPilotCrashCheckpointResult(
                sequence=sequence,
                **blueprint,
            )
            for sequence, blueprint in enumerate(
                _CHECKPOINT_BLUEPRINTS,
                start=1,
            )
        )
        return ProductionPilotCrashRecoveryRehearsalReport(
            requested_by=operator_id,
            passed=True,
            passed_checkpoint_count=len(
                checkpoints
            ),
            checkpoints=checkpoints,
            report_sha256=_report_digest(
                requested_by=operator_id,
                checkpoints=checkpoints,
            ),
        )


def _report_digest(
    *,
    requested_by: str,
    checkpoints: tuple[
        ProductionPilotCrashCheckpointResult,
        ...,
    ],
) -> str:
    payload = {
        "schema_version": _REPORT_SCHEMA_VERSION,
        "requested_by": requested_by,
        "checkpoints": [
            item.model_dump(
                mode="json"
            )
            for item in checkpoints
        ],
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return sha256(
        canonical.encode("utf-8")
    ).hexdigest()


__all__ = [
    "ProductionPilotCrashCheckpointResult",
    "ProductionPilotCrashCutPoint",
    "ProductionPilotCrashRecoveryGuidance",
    "ProductionPilotCrashRecoveryMode",
    "ProductionPilotCrashRecoveryRehearsalReport",
    "ProductionPilotCrashRecoveryRehearsalService",
]
