from uuid import uuid4

import pytest

from services.agent_runtime.app.verification.models import (
    VerificationCheck,
    VerificationResult,
    VerificationSource,
    VerificationStatus,
)

from services.agent_runtime.app.verification.store import (
    VerificationConflictError,
    VerificationStore,
)


@pytest.mark.asyncio
async def test_persists_across_store_instances(
    tmp_path,
):
    """
    A new Store instance can restore data
    written by an earlier instance.
    """

    db_path = (
        tmp_path
        / "verifications.db"
    )

    incident_id = uuid4()

    first_store = VerificationStore(
        db_path=db_path
    )

    result = VerificationResult(
        incident_id=incident_id,
        action="increase_memory_limit",
        target="payment-api",
    )

    saved = await first_store.save(
        result
    )

    second_store = VerificationStore(
        db_path=db_path
    )

    restored = await second_store.get(
        saved.id
    )

    assert restored is not None

    assert restored.id == saved.id

    assert (
        restored.incident_id
        == incident_id
    )

    assert (
        restored.status
        == VerificationStatus.PENDING
    )

    assert restored.action == (
        "increase_memory_limit"
    )

    assert restored.target == (
        "payment-api"
    )


@pytest.mark.asyncio
async def test_verification_lifecycle_is_persisted(
    tmp_path,
):
    """
    Persist:
    PENDING -> RUNNING -> PASSED.
    """

    store = VerificationStore(
        db_path=(
            tmp_path
            / "verifications.db"
        )
    )

    result = VerificationResult(
        incident_id=uuid4(),
        action="increase_memory_limit",
        target="payment-api",
    )

    result = await store.save(
        result
    )

    result.start()

    running = await store.update(
        result,
        expected_status=(
            VerificationStatus.PENDING
        ),
    )

    assert (
        running.status
        == VerificationStatus.RUNNING
    )

    assert running.started_at is not None

    check = VerificationCheck(
        name="pod_is_healthy",
        source=(
            VerificationSource.WORKLOAD
        ),
        passed=True,
        observed_value="Running",
        expected_value="Running",
        message="Pod returned to Running state",
    )

    running.complete(
        status=VerificationStatus.PASSED,
        checks=[
            check
        ],
        summary=(
            "Required workload check passed"
        ),
    )

    passed = await store.update(
        running,
        expected_status=(
            VerificationStatus.RUNNING
        ),
    )

    assert (
        passed.status
        == VerificationStatus.PASSED
    )

    assert passed.is_terminal is True

    assert (
        passed.required_checks_passed
        is True
    )

    assert passed.completed_at is not None

    restored = await store.get(
        passed.id
    )

    assert restored is not None

    assert (
        restored.status
        == VerificationStatus.PASSED
    )

    assert len(restored.checks) == 1

    assert (
        restored.checks[0].passed
        is True
    )


@pytest.mark.asyncio
async def test_duplicate_id_is_rejected(
    tmp_path,
):
    """
    The same Verification ID cannot
    be inserted twice.
    """

    store = VerificationStore(
        db_path=(
            tmp_path
            / "verifications.db"
        )
    )

    result = VerificationResult(
        incident_id=uuid4()
    )

    await store.save(
        result
    )

    with pytest.raises(
        VerificationConflictError
    ):
        await store.save(
            result
        )


@pytest.mark.asyncio
async def test_cross_instance_cas_conflict(
    tmp_path,
):
    """
    Only one Store instance can change
    a PENDING result with the same CAS condition.
    """

    db_path = (
        tmp_path
        / "verifications.db"
    )

    first_store = VerificationStore(
        db_path=db_path
    )

    second_store = VerificationStore(
        db_path=db_path
    )

    original = await first_store.save(
        VerificationResult(
            incident_id=uuid4()
        )
    )

    first_copy = await first_store.get(
        original.id
    )

    second_copy = await second_store.get(
        original.id
    )

    assert first_copy is not None

    assert second_copy is not None

    first_copy.start()

    await first_store.update(
        first_copy,
        expected_status=(
            VerificationStatus.PENDING
        ),
    )

    second_copy.start()

    with pytest.raises(
        VerificationConflictError
    ):
        await second_store.update(
            second_copy,
            expected_status=(
                VerificationStatus.PENDING
            ),
        )

    restored = await second_store.get(
        original.id
    )

    assert restored is not None

    assert (
        restored.status
        == VerificationStatus.RUNNING
    )


@pytest.mark.asyncio
async def test_list_by_incident_is_isolated_and_ordered(
    tmp_path,
):
    """
    Query only one Incident and order
    verification attempts correctly.
    """

    db_path = (
        tmp_path
        / "verifications.db"
    )

    store = VerificationStore(
        db_path=db_path
    )

    incident_id = uuid4()

    other_incident_id = uuid4()

    second_attempt = VerificationResult(
        incident_id=incident_id,
        attempt=2,
        action="restart_workload",
    )

    first_attempt = VerificationResult(
        incident_id=incident_id,
        attempt=1,
        action="increase_memory_limit",
    )

    unrelated = VerificationResult(
        incident_id=other_incident_id,
        attempt=1,
        action="scale_out",
    )

    await store.save(
        second_attempt
    )

    await store.save(
        unrelated
    )

    await store.save(
        first_attempt
    )

    new_store = VerificationStore(
        db_path=db_path
    )

    incident_results = (
        await new_store.list_by_incident(
            incident_id
        )
    )

    assert len(incident_results) == 2

    assert [
        result.attempt
        for result in incident_results
    ] == [
        1,
        2,
    ]

    assert {
        result.incident_id
        for result in incident_results
    } == {
        incident_id
    }

    all_results = (
        await new_store.list_all()
    )

    assert len(all_results) == 3
