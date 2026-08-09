from typing import Any

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
    VerificationSource,
    VerificationStatus,
)
from services.agent_runtime.app.verification.profiles import (
    VerificationProfileError,
)


class FakeCollector:
    """Build deterministic checks without calling any production tool."""

    def __init__(
        self,
        *,
        states: dict[str, bool | None] | None = None,
        omitted: set[str] | None = None,
        duplicates: set[str] | None = None,
        source_overrides: (
            dict[str, VerificationSource]
            | None
        ) = None,
        required_overrides: (
            dict[str, bool]
            | None
        ) = None,
        error: Exception | None = None,
    ) -> None:
        self.states = dict(
            states or {}
        )
        self.omitted = set(
            omitted or set()
        )
        self.duplicates = set(
            duplicates or set()
        )
        self.source_overrides = dict(
            source_overrides or {}
        )
        self.required_overrides = dict(
            required_overrides or {}
        )
        self.error = error
        self.context = None
        self.probes = []

    async def collect(
        self,
        probes,
        context=None,
    ) -> list[VerificationCheck]:
        self.context = context
        self.probes = list(
            probes
        )

        if self.error is not None:
            raise self.error

        checks: list[
            VerificationCheck
        ] = []

        for probe in self.probes:
            if probe.name in self.omitted:
                continue

            check = VerificationCheck(
                name=probe.name,
                source=(
                    self.source_overrides.get(
                        probe.name,
                        probe.source,
                    )
                ),
                passed=self.states.get(
                    probe.name,
                    True,
                ),
                required=(
                    self.required_overrides.get(
                        probe.name,
                        probe.required,
                    )
                ),
                observed_value="fake-evidence",
                expected_value="profile-rule",
                message="Fake collector result",
            )
            checks.append(
                check
            )

            if probe.name in self.duplicates:
                checks.append(
                    check.model_copy(
                        deep=True
                    )
                )

        return checks


def create_runtime(
    monkeypatch,
    tmp_path,
) -> AgentRuntime:
    monkeypatch.chdir(
        tmp_path
    )
    return AgentRuntime()


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


def create_coordinator(
    runtime: AgentRuntime,
    collector: FakeCollector,
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


@pytest.mark.asyncio
async def test_all_required_checks_pass_and_resolve_incident(
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
    collector = FakeCollector()
    coordinator = create_coordinator(
        runtime,
        collector,
    )
    context = object()

    verification, persisted_incident = (
        await coordinator.run(
            incident_id=incident.id,
            plan=memory_plan(),
            namespace="payment",
            cluster="prod-a",
            attempt=2,
            context=context,
            metadata={
                "profile": "untrusted-override",
                "request_id": "request-1",
            },
        )
    )

    assert verification.status == (
        VerificationStatus.PASSED
    )
    assert verification.required_checks_passed
    assert verification.attempt == 2
    assert verification.metadata[
        "profile"
    ] == "increase_memory_limit_v1"
    assert verification.metadata[
        "namespace"
    ] == "payment"
    assert verification.metadata[
        "cluster"
    ] == "prod-a"
    assert verification.metadata[
        "request_id"
    ] == "request-1"
    assert collector.context is context

    assert persisted_incident.status == (
        IncidentStatus.RESOLVED
    )
    assert "Verification succeeded" in (
        persisted_incident.reason
    )

    stored = await runtime.verification.get(
        verification.id
    )
    assert stored is not None
    assert stored.status == (
        VerificationStatus.PASSED
    )


@pytest.mark.asyncio
async def test_explicit_required_failure_marks_incident_failed(
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
    collector = FakeCollector(
        states={
            "memory_headroom_after_memory_increase": False
        }
    )
    coordinator = create_coordinator(
        runtime,
        collector,
    )

    verification, persisted_incident = (
        await coordinator.run(
            incident_id=incident.id,
            plan=memory_plan(),
            namespace="payment",
        )
    )

    assert verification.status == (
        VerificationStatus.FAILED
    )
    assert persisted_incident.status == (
        IncidentStatus.FAILED
    )
    assert (
        "memory_headroom_after_memory_increase"
        in verification.summary
    )


@pytest.mark.asyncio
async def test_unknown_required_check_keeps_incident_healing(
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
    collector = FakeCollector(
        states={
            "pod_ready_after_memory_increase": None
        }
    )
    coordinator = create_coordinator(
        runtime,
        collector,
    )

    verification, persisted_incident = (
        await coordinator.run(
            incident_id=incident.id,
            plan=memory_plan(),
        )
    )

    assert verification.status == (
        VerificationStatus.INCONCLUSIVE
    )
    assert persisted_incident.status == (
        IncidentStatus.HEALING
    )
    assert (
        "awaiting more evidence"
        in persisted_incident.reason
    )


@pytest.mark.asyncio
async def test_optional_failure_does_not_block_resolution(
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
    collector = FakeCollector(
        states={
            "pod_restart_stability_after_memory_increase": False
        }
    )
    coordinator = create_coordinator(
        runtime,
        collector,
    )

    verification, persisted_incident = (
        await coordinator.run(
            incident_id=incident.id,
            plan=memory_plan(),
        )
    )

    assert verification.status == (
        VerificationStatus.PASSED
    )
    assert persisted_incident.status == (
        IncidentStatus.RESOLVED
    )
    assert "optional checks not passed" in (
        verification.summary
    )


@pytest.mark.asyncio
async def test_unexpected_collection_error_is_persisted_as_inconclusive(
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
    collector = FakeCollector(
        error=RuntimeError(
            "collector crashed"
        )
    )
    coordinator = create_coordinator(
        runtime,
        collector,
    )

    verification, persisted_incident = (
        await coordinator.run(
            incident_id=incident.id,
            plan=memory_plan(),
        )
    )

    assert verification.status == (
        VerificationStatus.INCONCLUSIVE
    )
    assert persisted_incident.status == (
        IncidentStatus.HEALING
    )
    assert len(verification.checks) == 1
    assert verification.checks[0].name == (
        "verification_collection_error"
    )
    assert verification.checks[0].passed is None
    assert verification.checks[0].metadata[
        "error_type"
    ] == "RuntimeError"


@pytest.mark.asyncio
async def test_missing_required_probe_is_inconclusive(
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
    collector = FakeCollector(
        omitted={
            "memory_headroom_after_memory_increase"
        }
    )
    coordinator = create_coordinator(
        runtime,
        collector,
    )

    verification, persisted_incident = (
        await coordinator.run(
            incident_id=incident.id,
            plan=memory_plan(),
        )
    )

    integrity = next(
        check
        for check in verification.checks
        if check.name
        == "verification_probe_integrity"
    )

    assert verification.status == (
        VerificationStatus.INCONCLUSIVE
    )
    assert persisted_incident.status == (
        IncidentStatus.HEALING
    )
    assert integrity.required is True
    assert integrity.passed is None


@pytest.mark.asyncio
async def test_missing_optional_probe_is_audited_but_non_blocking(
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
    collector = FakeCollector(
        omitted={
            "pod_restart_stability_after_memory_increase"
        }
    )
    coordinator = create_coordinator(
        runtime,
        collector,
    )

    verification, persisted_incident = (
        await coordinator.run(
            incident_id=incident.id,
            plan=memory_plan(),
        )
    )

    integrity = next(
        check
        for check in verification.checks
        if check.name
        == "verification_probe_integrity"
    )

    assert verification.status == (
        VerificationStatus.PASSED
    )
    assert persisted_incident.status == (
        IncidentStatus.RESOLVED
    )
    assert integrity.required is False
    assert integrity.passed is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate",
        "source",
        "required",
    ],
)
async def test_required_probe_contract_mutation_is_inconclusive(
    monkeypatch,
    tmp_path,
    mutation,
):
    runtime = create_runtime(
        monkeypatch,
        tmp_path,
    )
    incident = await create_healing_incident(
        runtime
    )
    probe_name = (
        "memory_headroom_after_memory_increase"
    )
    collector_kwargs: dict[
        str,
        Any,
    ] = {}

    if mutation == "duplicate":
        collector_kwargs[
            "duplicates"
        ] = {
            probe_name
        }
    elif mutation == "source":
        collector_kwargs[
            "source_overrides"
        ] = {
            probe_name: (
                VerificationSource.WORKLOAD
            )
        }
    else:
        collector_kwargs[
            "required_overrides"
        ] = {
            probe_name: False
        }

    coordinator = create_coordinator(
        runtime,
        FakeCollector(
            **collector_kwargs
        ),
    )

    verification, persisted_incident = (
        await coordinator.run(
            incident_id=incident.id,
            plan=memory_plan(),
        )
    )

    assert verification.status == (
        VerificationStatus.INCONCLUSIVE
    )
    assert persisted_incident.status == (
        IncidentStatus.HEALING
    )
    assert any(
        check.name
        == "verification_probe_integrity"
        and check.required
        and check.passed is None
        for check in verification.checks
    )


@pytest.mark.asyncio
async def test_unsupported_action_does_not_create_verification(
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
    coordinator = create_coordinator(
        runtime,
        FakeCollector(),
    )

    with pytest.raises(
        VerificationProfileError,
        match=(
            "No verification profile is registered"
        ),
    ):
        await coordinator.run(
            incident_id=incident.id,
            plan=ActionPlan(
                type=ActionType.RESTART_POD,
                target="payment-api",
            ),
        )

    assert await runtime.verification.list_all() == []

    persisted_incident = (
        await runtime.incident_store.get(
            incident.id
        )
    )
    assert persisted_incident is not None
    assert persisted_incident.status == (
        IncidentStatus.HEALING
    )


def test_decision_without_required_checks_is_inconclusive():
    status, summary = (
        VerificationCoordinator.decide(
            [
                VerificationCheck(
                    name="optional_only",
                    source=(
                        VerificationSource.METRIC
                    ),
                    passed=True,
                    required=False,
                )
            ]
        )
    )

    assert status == (
        VerificationStatus.INCONCLUSIVE
    )
    assert summary == (
        "Verification has no required checks"
    )