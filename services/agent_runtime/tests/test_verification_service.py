from uuid import uuid4

import pytest

from services.agent_runtime.app.verification.models import (
    VerificationCheck,
    VerificationSource,
    VerificationStatus,
)

from services.agent_runtime.app.verification.service import (
    VerificationService,
)

from services.agent_runtime.app.verification.store import (
    VerificationStore,
)


def create_service(
    db_path,
) -> VerificationService:
    return VerificationService(
        store=VerificationStore(
            db_path=db_path
        )
    )


@pytest.mark.asyncio
async def test_create_verification_is_persisted(
    tmp_path,
):
    """
    Creation starts from PENDING and can
    be restored by another Service instance.
    """

    db_path = (
        tmp_path
        / "verifications.db"
    )

    incident_id = uuid4()

    first_service = create_service(
        db_path
    )

    created = (
        await first_service.create_verification(
            incident_id=incident_id,
            action="increase_memory_limit",
            target="payment-api",
            attempt=1,
            metadata={
                "source": "action_runtime"
            },
        )
    )

    second_service = create_service(
        db_path
    )

    restored = await second_service.get(
        created.id
    )

    assert restored is not None

    assert (
        restored.status
        == VerificationStatus.PENDING
    )

    assert (
        restored.incident_id
        == incident_id
    )

    assert restored.action == (
        "increase_memory_limit"
    )

    assert restored.target == (
        "payment-api"
    )

    assert restored.attempt == 1

    assert restored.metadata == {
        "source": "action_runtime"
    }


@pytest.mark.asyncio
async def test_start_is_persisted_and_idempotent(
    tmp_path,
):
    """
    Repeated start does not change
    the original start time.
    """

    db_path = (
        tmp_path
        / "verifications.db"
    )

    first_service = create_service(
        db_path
    )

    created = (
        await first_service.create_verification(
            incident_id=uuid4()
        )
    )

    started = await first_service.start(
        created.id
    )

    assert (
        started.status
        == VerificationStatus.RUNNING
    )

    assert started.started_at is not None

    second_service = create_service(
        db_path
    )

    repeated = await second_service.start(
        created.id
    )

    assert (
        repeated.status
        == VerificationStatus.RUNNING
    )

    assert (
        repeated.started_at
        == started.started_at
    )

    assert (
        repeated.updated_at
        == started.updated_at
    )


@pytest.mark.asyncio
async def test_complete_passed_across_service_instances(
    tmp_path,
):
    """
    Different Service instances can create,
    start and complete the same verification.
    """

    db_path = (
        tmp_path
        / "verifications.db"
    )

    create_service_instance = (
        create_service(
            db_path
        )
    )

    created = await (
        create_service_instance
        .create_verification(
            incident_id=uuid4(),
            action="increase_memory_limit",
            target="payment-api",
        )
    )

    start_service_instance = (
        create_service(
            db_path
        )
    )

    await start_service_instance.start(
        created.id
    )

    check = VerificationCheck(
        name="pod_is_healthy",
        source=(
            VerificationSource.WORKLOAD
        ),
        passed=True,
        observed_value="Running",
        expected_value="Running",
        message=(
            "Pod returned to Running state"
        ),
    )

    complete_service_instance = (
        create_service(
            db_path
        )
    )

    completed = await (
        complete_service_instance.complete(
            verification_id=created.id,
            status=(
                VerificationStatus.PASSED
            ),
            checks=[
                check
            ],
            summary=(
                "Required workload check passed"
            ),
        )
    )

    assert (
        completed.status
        == VerificationStatus.PASSED
    )

    assert completed.is_terminal is True

    assert (
        completed.required_checks_passed
        is True
    )

    assert completed.completed_at is not None

    assert len(completed.checks) == 1

    restored = await (
        create_service_instance.get(
            created.id
        )
    )

    assert restored is not None

    assert (
        restored.status
        == VerificationStatus.PASSED
    )

    assert restored.summary == (
        "Required workload check passed"
    )


@pytest.mark.asyncio
async def test_complete_requires_running_status(
    tmp_path,
):
    """
    PENDING verification cannot be
    completed directly by the Service.
    """

    service = create_service(
        tmp_path
        / "verifications.db"
    )

    created = (
        await service.create_verification(
            incident_id=uuid4()
        )
    )

    with pytest.raises(
        ValueError,
        match="Only RUNNING",
    ):
        await service.complete(
            verification_id=created.id,
            status=(
                VerificationStatus.FAILED
            ),
            checks=[],
        )

    restored = await service.get(
        created.id
    )

    assert restored is not None

    assert (
        restored.status
        == VerificationStatus.PENDING
    )


@pytest.mark.asyncio
async def test_passed_requires_required_evidence(
    tmp_path,
):
    """
    Optional evidence alone cannot
    produce a PASSED result.
    """

    service = create_service(
        tmp_path
        / "verifications.db"
    )

    created = (
        await service.create_verification(
            incident_id=uuid4()
        )
    )

    await service.start(
        created.id
    )

    optional_check = VerificationCheck(
        name="optional_log_check",
        source=VerificationSource.LOG,
        passed=True,
        required=False,
    )

    with pytest.raises(
        ValueError
    ):
        await service.complete(
            verification_id=created.id,
            status=(
                VerificationStatus.PASSED
            ),
            checks=[
                optional_check
            ],
        )

    restored = await service.get(
        created.id
    )

    assert restored is not None

    assert (
        restored.status
        == VerificationStatus.RUNNING
    )


@pytest.mark.parametrize(
    "terminal_status",
    [
        VerificationStatus.FAILED,
        VerificationStatus.INCONCLUSIVE,
        VerificationStatus.TIMED_OUT,
    ],
)
@pytest.mark.asyncio
async def test_non_passed_terminal_statuses(
    tmp_path,
    terminal_status,
):
    """
    Non-PASSED terminal statuses do not
    require successful checks.
    """

    service = create_service(
        tmp_path
        / (
            f"{terminal_status.value}.db"
        )
    )

    created = (
        await service.create_verification(
            incident_id=uuid4()
        )
    )

    await service.start(
        created.id
    )

    completed = await service.complete(
        verification_id=created.id,
        status=terminal_status,
        checks=[],
        summary=(
            f"Verification ended as "
            f"{terminal_status.value}"
        ),
    )

    assert (
        completed.status
        == terminal_status
    )

    assert completed.is_terminal is True

    assert completed.completed_at is not None


@pytest.mark.asyncio
async def test_terminal_verification_cannot_restart(
    tmp_path,
):
    """
    A terminal result cannot return
    to RUNNING.
    """

    service = create_service(
        tmp_path
        / "verifications.db"
    )

    created = (
        await service.create_verification(
            incident_id=uuid4()
        )
    )

    await service.start(
        created.id
    )

    await service.complete(
        verification_id=created.id,
        status=VerificationStatus.FAILED,
        checks=[],
        summary="Required check failed",
    )

    with pytest.raises(
        ValueError,
        match="Only PENDING",
    ):
        await service.start(
            created.id
        )


@pytest.mark.asyncio
async def test_missing_verification_is_rejected(
    tmp_path,
):
    """
    Lifecycle operations require an
    existing persisted result.
    """

    service = create_service(
        tmp_path
        / "verifications.db"
    )

    with pytest.raises(
        ValueError,
        match="not found",
    ):
        await service.start(
            uuid4()
        )


@pytest.mark.asyncio
async def test_list_by_incident(
    tmp_path,
):
    """
    Service delegates isolated Incident
    queries to the persistent Store.
    """

    service = create_service(
        tmp_path
        / "verifications.db"
    )

    incident_id = uuid4()

    await service.create_verification(
        incident_id=incident_id,
        attempt=2,
        action="restart_workload",
    )

    await service.create_verification(
        incident_id=uuid4(),
        attempt=1,
        action="scale_out",
    )

    await service.create_verification(
        incident_id=incident_id,
        attempt=1,
        action="increase_memory_limit",
    )

    results = (
        await service.list_by_incident(
            incident_id
        )
    )

    assert len(results) == 2

    assert [
        result.attempt
        for result in results
    ] == [
        1,
        2,
    ]

    assert {
        result.incident_id
        for result in results
    } == {
        incident_id
    }
