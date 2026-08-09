from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from services.agent_runtime.app.verification.models import (
    VerificationCheck,
    VerificationResult,
    VerificationSource,
    VerificationStatus,
)


def build_result() -> VerificationResult:
    return VerificationResult(
        incident_id=uuid4(),
        action="increase_memory_limit",
        target="payment-api",
    )


def build_required_check(
    passed: bool | None,
) -> VerificationCheck:
    return VerificationCheck(
        name="pod_restart_count",
        source=VerificationSource.METRIC,
        passed=passed,
        required=True,
        observed_value=0,
        expected_value=(
            "restart_count decreases"
        ),
        message=(
            "Pod restart count verification"
        ),
    )


def test_verification_start():
    result = build_result()

    assert result.status == (
        VerificationStatus.PENDING
    )

    assert result.started_at is None

    result.start()

    assert result.status == (
        VerificationStatus.RUNNING
    )

    assert result.started_at is not None

    assert result.completed_at is None

    assert result.is_terminal is False


def test_passed_requires_evidence():
    result = build_result()

    result.start()

    with pytest.raises(
        ValueError,
        match=(
            "PASSED verification requires"
        ),
    ):
        result.complete(
            status=(
                VerificationStatus.PASSED
            ),
            checks=[],
            summary=(
                "No evidence was collected"
            ),
        )


def test_required_check_must_pass():
    result = build_result()

    result.start()

    with pytest.raises(
        ValueError,
        match=(
            "PASSED verification requires"
        ),
    ):
        result.complete(
            status=(
                VerificationStatus.PASSED
            ),
            checks=[
                build_required_check(
                    passed=False
                )
            ],
            summary=(
                "Required verification failed"
            ),
        )


def test_optional_failure_does_not_block_pass():
    result = build_result()

    result.start()

    required_check = (
        build_required_check(
            passed=True
        )
    )

    optional_check = VerificationCheck(
        name="application_log_noise",
        source=VerificationSource.LOG,
        passed=False,
        required=False,
        observed_value=(
            "one non-critical warning"
        ),
        expected_value=(
            "no critical errors"
        ),
        message=(
            "Optional warning check failed"
        ),
    )

    result.complete(
        status=VerificationStatus.PASSED,
        checks=[
            required_check,
            optional_check,
        ],
        summary=(
            "All required checks passed"
        ),
    )

    assert result.status == (
        VerificationStatus.PASSED
    )

    assert result.required_checks_passed is True

    assert result.is_terminal is True

    assert result.completed_at is not None


def test_inconclusive_is_not_passed():
    result = build_result()

    result.start()

    result.complete(
        status=(
            VerificationStatus.INCONCLUSIVE
        ),
        checks=[
            build_required_check(
                passed=None
            )
        ],
        summary=(
            "Metrics are temporarily unavailable"
        ),
    )

    assert result.status == (
        VerificationStatus.INCONCLUSIVE
    )

    assert result.required_checks_passed is False

    assert result.is_terminal is True


def test_terminal_verification_cannot_restart():
    result = build_result()

    result.start()

    result.complete(
        status=(
            VerificationStatus.FAILED
        ),
        checks=[
            build_required_check(
                passed=False
            )
        ],
        summary=(
            "Pod restart count is still increasing"
        ),
    )

    assert result.completed_at is not None

    with pytest.raises(
        ValueError,
        match=(
            "Terminal verification "
            "cannot be restarted"
        ),
    ):
        result.start()


def test_terminal_model_requires_completed_at():
    with pytest.raises(
        ValueError,
        match=(
            "Terminal verification "
            "requires completed_at"
        ),
    ):
        VerificationResult(
            incident_id=uuid4(),
            status=(
                VerificationStatus.FAILED
            ),
            checks=[
                build_required_check(
                    passed=False
                )
            ],
            created_at=datetime.now(
                UTC
            ),
        )
