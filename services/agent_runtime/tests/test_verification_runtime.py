from uuid import uuid4

import pytest

from services.agent_runtime.app.incident.enums import (
    IncidentStatus,
)

from services.agent_runtime.app.incident.state import (
    IncidentState,
)

from services.agent_runtime.app.incident.store import (
    IncidentStore,
)

from services.agent_runtime.app.runtime.verification_runtime import (
    VerificationIncidentSyncError,
    VerificationRuntime,
)

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


class FailOnceIncidentStore:
    """
    Delegate to a real IncidentStore but fail
    the first update operation.
    """

    def __init__(
        self,
        store: IncidentStore,
    ) -> None:
        self.store = store

        self.fail_next_update = True

    async def get(
        self,
        incident_id,
    ):
        return await self.store.get(
            incident_id
        )

    async def update(
        self,
        incident,
        expected_status=None,
    ):
        if self.fail_next_update:
            self.fail_next_update = False

            raise RuntimeError(
                "Injected IncidentStore failure"
            )

        return await self.store.update(
            incident,
            expected_status=expected_status,
        )


def create_runtime(
    tmp_path,
    incident_store,
) -> tuple[
    VerificationRuntime,
    VerificationService,
]:
    verification_service = (
        VerificationService(
            store=VerificationStore(
                db_path=(
                    tmp_path
                    / "verifications.db"
                )
            )
        )
    )

    runtime = VerificationRuntime(
        verification_service=(
            verification_service
        ),
        incident_store=incident_store,
    )

    return (
        runtime,
        verification_service,
    )


async def save_incident(
    store: IncidentStore,
    status: IncidentStatus = (
        IncidentStatus.HEALING
    ),
) -> IncidentState:
    incident = IncidentState(
        status=status,
        reason="Remediation action executed",
    )

    return await store.save(
        incident
    )


def passing_check(
) -> VerificationCheck:
    return VerificationCheck(
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


@pytest.mark.asyncio
async def test_create_and_start_for_healing_incident(
    tmp_path,
):
    """
    Verification can be created and started
    only while the Incident is HEALING.
    """

    incident_store = IncidentStore(
        db_path=(
            tmp_path
            / "incidents.db"
        )
    )

    incident = await save_incident(
        incident_store
    )

    runtime, service = create_runtime(
        tmp_path,
        incident_store,
    )

    created = await runtime.create(
        incident_id=incident.id,
        action="increase_memory_limit",
        target="payment-api",
        metadata={
            "source": "action_runtime"
        },
    )

    assert (
        created.status
        == VerificationStatus.PENDING
    )

    assert (
        created.incident_id
        == incident.id
    )

    started = await runtime.start(
        created.id
    )

    assert (
        started.status
        == VerificationStatus.RUNNING
    )

    restored = await service.get(
        created.id
    )

    assert restored is not None

    assert (
        restored.status
        == VerificationStatus.RUNNING
    )


@pytest.mark.asyncio
async def test_create_rejects_non_healing_incident(
    tmp_path,
):
    """
    A CONFIRMED Incident has not reached the
    post-action verification phase.
    """

    incident_store = IncidentStore(
        db_path=(
            tmp_path
            / "incidents.db"
        )
    )

    incident = await save_incident(
        incident_store,
        status=IncidentStatus.CONFIRMED,
    )

    runtime, _ = create_runtime(
        tmp_path,
        incident_store,
    )

    with pytest.raises(
        ValueError,
        match="HEALING",
    ):
        await runtime.create(
            incident_id=incident.id
        )


@pytest.mark.asyncio
async def test_passed_verification_resolves_incident(
    tmp_path,
):
    """
    PASSED evidence moves Incident from
    HEALING to RESOLVED.
    """

    incident_store = IncidentStore(
        db_path=(
            tmp_path
            / "incidents.db"
        )
    )

    incident = await save_incident(
        incident_store
    )

    runtime, _ = create_runtime(
        tmp_path,
        incident_store,
    )

    created = await runtime.create(
        incident_id=incident.id,
        action="increase_memory_limit",
        target="payment-api",
    )

    await runtime.start(
        created.id
    )

    verification, updated_incident = (
        await runtime.complete(
            verification_id=created.id,
            status=(
                VerificationStatus.PASSED
            ),
            checks=[
                passing_check()
            ],
            summary=(
                "Pod health and memory "
                "checks passed"
            ),
        )
    )

    assert (
        verification.status
        == VerificationStatus.PASSED
    )

    assert (
        updated_incident.status
        == IncidentStatus.RESOLVED
    )

    assert (
        updated_incident.reason
        is not None
    )

    assert (
        "verification succeeded"
        in updated_incident.reason.lower()
    )

    restored_incident = (
        await incident_store.get(
            incident.id
        )
    )

    assert restored_incident is not None

    assert (
        restored_incident.status
        == IncidentStatus.RESOLVED
    )


@pytest.mark.parametrize(
    "verification_status, reason_text",
    [
        (
            VerificationStatus.FAILED,
            "verification failed",
        ),
        (
            VerificationStatus.TIMED_OUT,
            "verification timed out",
        ),
    ],
)
@pytest.mark.asyncio
async def test_failure_statuses_fail_incident(
    tmp_path,
    verification_status,
    reason_text,
):
    """
    FAILED and TIMED_OUT both move the
    Incident to FAILED.
    """

    incident_store = IncidentStore(
        db_path=(
            tmp_path
            / (
                f"incidents_"
                f"{verification_status.value}.db"
            )
        )
    )

    incident = await save_incident(
        incident_store
    )

    verification_service = (
        VerificationService(
            store=VerificationStore(
                db_path=(
                    tmp_path
                    / (
                        f"verifications_"
                        f"{verification_status.value}.db"
                    )
                )
            )
        )
    )

    runtime = VerificationRuntime(
        verification_service=(
            verification_service
        ),
        incident_store=incident_store,
    )

    created = await runtime.create(
        incident_id=incident.id
    )

    await runtime.start(
        created.id
    )

    _, updated_incident = (
        await runtime.complete(
            verification_id=created.id,
            status=verification_status,
            checks=[],
            summary="Recovery was not confirmed",
        )
    )

    assert (
        updated_incident.status
        == IncidentStatus.FAILED
    )

    assert (
        updated_incident.reason
        is not None
    )

    assert (
        reason_text
        in updated_incident.reason.lower()
    )


@pytest.mark.asyncio
async def test_inconclusive_keeps_incident_healing(
    tmp_path,
):
    """
    Missing evidence is not the same as
    confirmed remediation failure.
    """

    incident_store = IncidentStore(
        db_path=(
            tmp_path
            / "incidents.db"
        )
    )

    incident = await save_incident(
        incident_store
    )

    runtime, _ = create_runtime(
        tmp_path,
        incident_store,
    )

    created = await runtime.create(
        incident_id=incident.id
    )

    await runtime.start(
        created.id
    )

    _, updated_incident = (
        await runtime.complete(
            verification_id=created.id,
            status=(
                VerificationStatus.INCONCLUSIVE
            ),
            checks=[],
            summary="Metrics are unavailable",
        )
    )

    assert (
        updated_incident.status
        == IncidentStatus.HEALING
    )

    assert (
        updated_incident.reason
        is not None
    )

    assert (
        "inconclusive"
        in updated_incident.reason.lower()
    )


@pytest.mark.asyncio
async def test_start_rejects_incident_that_left_healing(
    tmp_path,
):
    """
    Verification cannot start after another
    workflow has already failed the Incident.
    """

    incident_store = IncidentStore(
        db_path=(
            tmp_path
            / "incidents.db"
        )
    )

    incident = await save_incident(
        incident_store
    )

    runtime, _ = create_runtime(
        tmp_path,
        incident_store,
    )

    created = await runtime.create(
        incident_id=incident.id
    )

    current_incident = (
        await incident_store.get(
            incident.id
        )
    )

    assert current_incident is not None

    current_incident.update(
        status=IncidentStatus.FAILED,
        reason="External remediation failure",
    )

    await incident_store.update(
        current_incident,
        expected_status=(
            IncidentStatus.HEALING
        ),
    )

    with pytest.raises(
        ValueError,
        match="HEALING",
    ):
        await runtime.start(
            created.id
        )


@pytest.mark.asyncio
async def test_reconcile_recovers_after_sync_failure(
    tmp_path,
):
    """
    Verification remains PASSED when the first
    Incident update fails, and reconcile repairs
    the Incident later.
    """

    real_incident_store = IncidentStore(
        db_path=(
            tmp_path
            / "incidents.db"
        )
    )

    incident = await save_incident(
        real_incident_store
    )

    failing_store = FailOnceIncidentStore(
        real_incident_store
    )

    runtime, service = create_runtime(
        tmp_path,
        failing_store,
    )

    created = await runtime.create(
        incident_id=incident.id
    )

    await runtime.start(
        created.id
    )

    with pytest.raises(
        VerificationIncidentSyncError
    ) as captured:
        await runtime.complete(
            verification_id=created.id,
            status=(
                VerificationStatus.PASSED
            ),
            checks=[
                passing_check()
            ],
            summary="Required checks passed",
        )

    assert (
        captured.value.verification_id
        == created.id
    )

    assert (
        captured.value.incident_id
        == incident.id
    )

    persisted_verification = (
        await service.get(
            created.id
        )
    )

    assert (
        persisted_verification
        is not None
    )

    assert (
        persisted_verification.status
        == VerificationStatus.PASSED
    )

    unchanged_incident = (
        await real_incident_store.get(
            incident.id
        )
    )

    assert unchanged_incident is not None

    assert (
        unchanged_incident.status
        == IncidentStatus.HEALING
    )

    _, reconciled_incident = (
        await runtime.reconcile(
            created.id
        )
    )

    assert (
        reconciled_incident.status
        == IncidentStatus.RESOLVED
    )

    restored_incident = (
        await real_incident_store.get(
            incident.id
        )
    )

    assert restored_incident is not None

    assert (
        restored_incident.status
        == IncidentStatus.RESOLVED
    )


@pytest.mark.asyncio
async def test_reconcile_is_idempotent(
    tmp_path,
):
    """
    Reconciliation does not rewrite an Incident
    that already matches the Verification.
    """

    incident_store = IncidentStore(
        db_path=(
            tmp_path
            / "incidents.db"
        )
    )

    incident = await save_incident(
        incident_store
    )

    runtime, _ = create_runtime(
        tmp_path,
        incident_store,
    )

    created = await runtime.create(
        incident_id=incident.id
    )

    await runtime.start(
        created.id
    )

    await runtime.complete(
        verification_id=created.id,
        status=VerificationStatus.PASSED,
        checks=[
            passing_check()
        ],
        summary="Required checks passed",
    )

    before = await incident_store.get(
        incident.id
    )

    assert before is not None

    _, reconciled = await runtime.reconcile(
        created.id
    )

    assert (
        reconciled.status
        == IncidentStatus.RESOLVED
    )

    assert (
        reconciled.updated_at
        == before.updated_at
    )


@pytest.mark.asyncio
async def test_reconcile_rejects_nonterminal_result(
    tmp_path,
):
    """
    PENDING and RUNNING results cannot drive
    Incident terminal state.
    """

    incident_store = IncidentStore(
        db_path=(
            tmp_path
            / "incidents.db"
        )
    )

    incident = await save_incident(
        incident_store
    )

    runtime, _ = create_runtime(
        tmp_path,
        incident_store,
    )

    created = await runtime.create(
        incident_id=incident.id
    )

    with pytest.raises(
        ValueError,
        match="terminal",
    ):
        await runtime.reconcile(
            created.id
        )


@pytest.mark.asyncio
async def test_missing_incident_is_rejected(
    tmp_path,
):
    """
    Verification cannot exist without a
    persisted linked Incident.
    """

    incident_store = IncidentStore(
        db_path=(
            tmp_path
            / "incidents.db"
        )
    )

    runtime, _ = create_runtime(
        tmp_path,
        incident_store,
    )

    with pytest.raises(
        ValueError,
        match="Incident not found",
    ):
        await runtime.create(
            incident_id=uuid4()
        )
