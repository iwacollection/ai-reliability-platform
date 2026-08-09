import asyncio
from typing import Any
from uuid import UUID, uuid4

import pytest

from services.agent_runtime.app.action.models import (
    ActionPlan,
    ActionType,
)
from services.agent_runtime.app.incident.enums import (
    IncidentStatus,
)
from services.agent_runtime.app.incident.state import (
    IncidentState,
)
from services.agent_runtime.app.runtime.runtime import (
    AgentRuntime,
)
from services.agent_runtime.app.verification.coordinator import (
    VerificationCoordinator,
)
from services.agent_runtime.app.verification.models import (
    VerificationCheck,
    VerificationStatus,
)


class CountingCollector:
    """Return deterministic evidence and record every real probe run."""

    def __init__(
        self,
        *,
        delay: float = 0.0,
    ) -> None:
        self.delay = delay
        self.calls = 0

    async def collect(
        self,
        probes,
        context=None,
    ) -> list[VerificationCheck]:
        self.calls += 1

        if self.delay:
            await asyncio.sleep(
                self.delay
            )

        return [
            VerificationCheck(
                name=probe.name,
                source=probe.source,
                passed=True,
                required=probe.required,
                observed_value=(
                    "exactly-once-test-evidence"
                ),
                expected_value=(
                    "profile-rule"
                ),
                message=(
                    "Deterministic coordinator test result"
                ),
            )
            for probe in probes
        ]


def create_runtime(
    monkeypatch,
    tmp_path,
) -> AgentRuntime:
    monkeypatch.chdir(
        tmp_path
    )
    return AgentRuntime()


def create_coordinator(
    runtime: AgentRuntime,
    collector: CountingCollector,
) -> VerificationCoordinator:
    return VerificationCoordinator(
        profile_factory=(
            runtime.verification_profile_factory
        ),
        collector=collector,
        verification_runtime=(
            runtime.verification_runtime
        ),
    )


def memory_plan() -> ActionPlan:
    return ActionPlan(
        type=(
            ActionType.INCREASE_MEMORY_LIMIT
        ),
        target="payment-api",
    )


async def create_healing_incident(
    runtime: AgentRuntime,
) -> IncidentState:
    incident = IncidentState()
    incident.update(
        status=IncidentStatus.HEALING,
        reason=(
            "Remediation action executed; "
            "awaiting verification"
        ),
    )
    return await runtime.incident_store.save(
        incident
    )


def build_claim_metadata(
    runtime: AgentRuntime,
    *,
    plan: ActionPlan,
    action_execution_id: UUID,
    namespace: str,
    cluster: str,
    caller_metadata: dict[str, Any],
) -> tuple[Any, dict[str, Any]]:
    """Build the exact immutable payload produced by Coordinator.run()."""

    profile = (
        runtime.verification_profile_factory.create(
            plan,
            namespace=namespace,
            cluster=cluster,
        )
    )
    metadata = dict(
        caller_metadata
    )
    metadata.update(
        {
            "profile": profile.name,
            "namespace": profile.namespace,
            "cluster": profile.cluster,
            "required_probes": [
                probe.name
                for probe in profile.probes
                if probe.required
            ],
            "optional_probes": [
                probe.name
                for probe in profile.probes
                if not probe.required
            ],
            "action_execution_id": str(
                action_execution_id
            ),
        }
    )
    return (
        profile,
        metadata,
    )


@pytest.mark.asyncio
async def test_terminal_replay_reuses_result_without_new_probe(
    monkeypatch,
    tmp_path,
):
    runtime = create_runtime(
        monkeypatch,
        tmp_path,
    )
    incident = await create_healing_incident(
        runtime
    )
    execution_id = uuid4()
    first_collector = CountingCollector()
    first_coordinator = create_coordinator(
        runtime,
        first_collector,
    )

    first_verification, first_incident = (
        await first_coordinator.run(
            incident_id=incident.id,
            plan=memory_plan(),
            namespace="payment",
            cluster="prod-a",
            metadata={
                "trigger": "action_execution"
            },
            action_execution_id=execution_id,
        )
    )

    assert first_collector.calls == 1
    assert first_verification.status == (
        VerificationStatus.PASSED
    )
    assert first_incident.status == (
        IncidentStatus.RESOLVED
    )

    replay_runtime = AgentRuntime()
    replay_collector = CountingCollector()
    replay_coordinator = create_coordinator(
        replay_runtime,
        replay_collector,
    )

    replayed_verification, replayed_incident = (
        await replay_coordinator.run(
            incident_id=incident.id,
            plan=memory_plan(),
            namespace="payment",
            cluster="prod-a",
            metadata={
                "trigger": "action_execution"
            },
            action_execution_id=execution_id,
        )
    )

    assert replay_collector.calls == 0
    assert replayed_verification.id == (
        first_verification.id
    )
    assert replayed_verification.action_execution_id == (
        execution_id
    )
    assert replayed_incident.status == (
        IncidentStatus.RESOLVED
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "existing_status",
    [
        VerificationStatus.PENDING,
        VerificationStatus.RUNNING,
    ],
)
async def test_inflight_replay_never_takes_probe_ownership(
    monkeypatch,
    tmp_path,
    existing_status,
):
    runtime = create_runtime(
        monkeypatch,
        tmp_path,
    )
    incident = await create_healing_incident(
        runtime
    )
    plan = memory_plan()
    execution_id = uuid4()
    caller_metadata = {
        "trigger": "action_execution"
    }
    profile, claim_metadata = (
        build_claim_metadata(
            runtime,
            plan=plan,
            action_execution_id=execution_id,
            namespace="payment",
            cluster="prod-a",
            caller_metadata=caller_metadata,
        )
    )

    claim = await runtime.verification_runtime.claim(
        action_execution_id=execution_id,
        incident_id=incident.id,
        action=profile.action.value,
        target=profile.target,
        attempt=1,
        metadata=claim_metadata,
    )
    assert claim.created is True

    if existing_status == VerificationStatus.RUNNING:
        await runtime.verification_runtime.start(
            claim.verification.id
        )

    collector = CountingCollector()
    coordinator = create_coordinator(
        runtime,
        collector,
    )

    replayed, persisted_incident = (
        await coordinator.run(
            incident_id=incident.id,
            plan=plan,
            namespace="payment",
            cluster="prod-a",
            metadata=caller_metadata,
            action_execution_id=execution_id,
        )
    )

    assert collector.calls == 0
    assert replayed.id == claim.verification.id
    assert replayed.status == existing_status
    assert persisted_incident.status == (
        IncidentStatus.HEALING
    )


@pytest.mark.asyncio
async def test_cross_instance_race_runs_only_one_collector(
    monkeypatch,
    tmp_path,
):
    first_runtime = create_runtime(
        monkeypatch,
        tmp_path,
    )
    incident = await create_healing_incident(
        first_runtime
    )
    second_runtime = AgentRuntime()
    execution_id = uuid4()
    first_collector = CountingCollector(
        delay=0.05
    )
    second_collector = CountingCollector(
        delay=0.05
    )
    first_coordinator = create_coordinator(
        first_runtime,
        first_collector,
    )
    second_coordinator = create_coordinator(
        second_runtime,
        second_collector,
    )

    first_result, second_result = await asyncio.gather(
        first_coordinator.run(
            incident_id=incident.id,
            plan=memory_plan(),
            namespace="payment",
            cluster="prod-a",
            metadata={
                "trigger": "concurrent_test"
            },
            action_execution_id=execution_id,
        ),
        second_coordinator.run(
            incident_id=incident.id,
            plan=memory_plan(),
            namespace="payment",
            cluster="prod-a",
            metadata={
                "trigger": "concurrent_test"
            },
            action_execution_id=execution_id,
        ),
    )

    assert (
        first_collector.calls
        + second_collector.calls
    ) == 1
    assert first_result[0].id == second_result[0].id

    stored = await (
        first_runtime.verification
        .get_by_action_execution(
            execution_id
        )
    )
    assert stored is not None
    assert stored.id == first_result[0].id
    assert stored.status == (
        VerificationStatus.PASSED
    )


@pytest.mark.asyncio
async def test_conflicting_execution_ids_fail_before_probe(
    monkeypatch,
    tmp_path,
):
    runtime = create_runtime(
        monkeypatch,
        tmp_path,
    )
    incident = await create_healing_incident(
        runtime
    )
    collector = CountingCollector()
    coordinator = create_coordinator(
        runtime,
        collector,
    )

    with pytest.raises(
        ValueError,
        match="conflicts",
    ):
        await coordinator.run(
            incident_id=incident.id,
            plan=memory_plan(),
            namespace="payment",
            cluster="prod-a",
            metadata={
                "action_execution_id": str(
                    uuid4()
                )
            },
            action_execution_id=uuid4(),
        )

    assert collector.calls == 0
    assert await runtime.verification.list_all() == []

