from datetime import UTC, datetime

from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)


class VerificationStatus(str, Enum):
    """
    Verification lifecycle status.
    """

    PENDING = "pending"

    RUNNING = "running"

    PASSED = "passed"

    FAILED = "failed"

    INCONCLUSIVE = "inconclusive"

    TIMED_OUT = "timed_out"


class VerificationSource(str, Enum):
    """
    Source of one verification check.
    """

    METRIC = "metric"

    LOG = "log"

    EVENT = "event"

    WORKLOAD = "workload"

    SYNTHETIC = "synthetic"

    MANUAL = "manual"


class VerificationCheck(BaseModel):
    """
    One evidence-backed verification check.

    passed:
    - True: check passed
    - False: check failed
    - None: evidence is insufficient
    """

    model_config = ConfigDict(
        use_enum_values=False,
        validate_assignment=True,
    )

    name: str

    source: VerificationSource

    passed: bool | None = None

    required: bool = True

    observed_value: Any = None

    expected_value: Any = None

    message: str = ""

    checked_at: datetime = Field(
        default_factory=lambda:
        datetime.now(UTC)
    )

    metadata: dict[str, Any] = Field(
        default_factory=dict
    )


class VerificationResult(BaseModel):
    """
    Persistent verification result for one remediation attempt.

    action_execution_id links an automatically triggered verification back to
    the durable ActionExecutionRecord that produced it. Legacy and explicitly
    manual verifications may leave the field empty. VerificationStore owns the
    uniqueness rule that at most one result may reference an Action Execution.

    A successful Action does not make this result PASSED.
    PASSED requires at least one required check and every
    required check must explicitly pass.
    """

    model_config = ConfigDict(
        use_enum_values=False,
        validate_assignment=True,
    )

    id: UUID = Field(
        default_factory=uuid4
    )

    incident_id: UUID

    action_execution_id: UUID | None = None

    action: str | None = None

    target: str | None = None

    attempt: int = Field(
        default=1,
        ge=1,
    )

    status: VerificationStatus = (
        VerificationStatus.PENDING
    )

    checks: list[VerificationCheck] = Field(
        default_factory=list
    )

    summary: str = ""

    created_at: datetime = Field(
        default_factory=lambda:
        datetime.now(UTC)
    )

    started_at: datetime | None = None

    completed_at: datetime | None = None

    updated_at: datetime = Field(
        default_factory=lambda:
        datetime.now(UTC)
    )

    metadata: dict[str, Any] = Field(
        default_factory=dict
    )

    @property
    def is_terminal(
        self,
    ) -> bool:
        return self.status in {
            VerificationStatus.PASSED,
            VerificationStatus.FAILED,
            VerificationStatus.INCONCLUSIVE,
            VerificationStatus.TIMED_OUT,
        }

    @property
    def required_checks_passed(
        self,
    ) -> bool:
        required_checks = [
            check
            for check in self.checks
            if check.required
        ]

        return (
            bool(
                required_checks
            )
            and all(
                check.passed is True
                for check in required_checks
            )
        )

    def start(
        self,
    ) -> None:
        """
        Start verification without changing the Incident status.

        Incident remains HEALING until verification reaches
        a terminal decision.
        """

        if self.is_terminal:
            raise ValueError(
                "Terminal verification cannot be restarted"
            )

        if (
            self.status
            == VerificationStatus.RUNNING
        ):
            return

        now = datetime.now(
            UTC
        )

        self.started_at = now

        self.updated_at = now

        self.status = (
            VerificationStatus.RUNNING
        )

    def complete(
        self,
        status: VerificationStatus,
        checks: list[VerificationCheck],
        summary: str = "",
    ) -> None:
        """
        Complete verification with an explicit terminal status.
        """

        if status not in {
            VerificationStatus.PASSED,
            VerificationStatus.FAILED,
            VerificationStatus.INCONCLUSIVE,
            VerificationStatus.TIMED_OUT,
        }:
            raise ValueError(
                "Verification completion requires "
                "a terminal status"
            )

        now = datetime.now(
            UTC
        )

        self.checks = list(
            checks
        )

        self.summary = summary

        self.completed_at = now

        self.updated_at = now

        self.status = status

    @model_validator(
        mode="after"
    )
    def validate_terminal_state(
        self,
    ):
        if (
            self.status
            == VerificationStatus.PASSED
            and not self.required_checks_passed
        ):
            raise ValueError(
                "PASSED verification requires at least "
                "one required check and all required "
                "checks must pass"
            )

        if (
            self.is_terminal
            and self.completed_at is None
        ):
            raise ValueError(
                "Terminal verification requires completed_at"
            )

        return self
