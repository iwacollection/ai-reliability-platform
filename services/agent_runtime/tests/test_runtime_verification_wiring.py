import pytest

from services.agent_runtime.app.incident.enums import (
    IncidentStatus,
)

from services.agent_runtime.app.incident.state import (
    IncidentState,
)

from services.agent_runtime.app.runtime.runtime import (
    AgentRuntime,
)

from services.agent_runtime.app.verification.models import (
    VerificationCheck,
    VerificationSource,
    VerificationStatus,
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


def test_agent_runtime_shares_verification_dependencies(
    tmp_path,
    monkeypatch,
):
    """
    Action, Verification and Pipeline use
    the same IncidentStore instance.
    """

    monkeypatch.chdir(
        tmp_path
    )

    runtime = AgentRuntime()

    assert (
        runtime.verification.store
        is runtime.verification_store
    )

    assert (
        runtime.verification_runtime
        .verification_service
        is runtime.verification
    )

    assert (
        runtime.action_runtime.incident_store
        is runtime.incident_store
    )

    assert (
        runtime.verification_runtime
        .incident_store
        is runtime.incident_store
    )

    assert (
        runtime.pipeline.incident_store
        is runtime.incident_store
    )

    assert (
        tmp_path
        / "data"
        / "incidents.db"
    ).exists()

    assert (
        tmp_path
        / "data"
        / "verifications.db"
    ).exists()


@pytest.mark.asyncio
async def test_agent_runtime_restart_recovers_verification(
    tmp_path,
    monkeypatch,
):
    """
    Separate AgentRuntime instances can create,
    start and complete the same Verification.
    """

    monkeypatch.chdir(
        tmp_path
    )

    #
    # Runtime process 1:
    # persist HEALING Incident and create
    # PENDING Verification.
    #
    first_runtime = AgentRuntime()

    incident = IncidentState(
        status=IncidentStatus.HEALING,
        reason=(
            "Remediation action executed; "
            "awaiting verification"
        ),
    )

    incident = (
        await first_runtime
        .incident_store
        .save(
            incident
        )
    )

    created = await (
        first_runtime
        .verification_runtime
        .create(
            incident_id=incident.id,
            action="increase_memory_limit",
            target="payment-api",
            metadata={
                "source": "action_runtime"
            },
        )
    )

    assert (
        created.status
        == VerificationStatus.PENDING
    )

    #
    # Runtime process 2:
    # restore and start Verification.
    #
    second_runtime = AgentRuntime()

    started = await (
        second_runtime
        .verification_runtime
        .start(
            created.id
        )
    )

    assert (
        started.status
        == VerificationStatus.RUNNING
    )

    assert started.started_at is not None

    restored_incident = (
        await second_runtime
        .incident_store
        .get(
            incident.id
        )
    )

    assert restored_incident is not None

    assert (
        restored_incident.status
        == IncidentStatus.HEALING
    )

    #
    # Runtime process 3:
    # restore, persist PASSED evidence and
    # resolve the linked Incident.
    #
    third_runtime = AgentRuntime()

    verification, resolved_incident = (
        await third_runtime
        .verification_runtime
        .complete(
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
        verification.required_checks_passed
        is True
    )

    assert (
        resolved_incident.status
        == IncidentStatus.RESOLVED
    )

    #
    # Runtime process 4:
    # verify durable state after another restart.
    #
    fourth_runtime = AgentRuntime()

    persisted_verification = (
        await fourth_runtime
        .verification
        .get(
            created.id
        )
    )

    persisted_incident = (
        await fourth_runtime
        .incident_store
        .get(
            incident.id
        )
    )

    assert persisted_verification is not None

    assert (
        persisted_verification.status
        == VerificationStatus.PASSED
    )

    assert persisted_incident is not None

    assert (
        persisted_incident.status
        == IncidentStatus.RESOLVED
    )

    assert (
        persisted_incident.reason
        is not None
    )

    assert (
        "verification succeeded"
        in persisted_incident.reason.lower()
    )

    #
    # Reconcile remains idempotent after restart.
    #
    before_updated_at = (
        persisted_incident.updated_at
    )

    _, reconciled_incident = (
        await fourth_runtime
        .verification_runtime
        .reconcile(
            created.id
        )
    )

    assert (
        reconciled_incident.updated_at
        == before_updated_at
    )
