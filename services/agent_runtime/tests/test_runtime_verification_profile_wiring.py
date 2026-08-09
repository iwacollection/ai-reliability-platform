from unittest.mock import AsyncMock

import pytest

from services.agent_runtime.app.action.models import (
    ActionPlan,
    ActionType,
)
from services.agent_runtime.app.runtime.runtime import (
    AgentRuntime,
)
from services.agent_runtime.app.verification.collector import (
    VerificationEvidenceCollector,
)
from services.agent_runtime.app.verification.coordinator import (
    VerificationCoordinator,
)
from services.agent_runtime.app.verification.profiles import (
    VerificationProfileFactory,
)


def create_runtime(
    monkeypatch,
    tmp_path,
) -> AgentRuntime:
    """
    Keep every default SQLite database inside pytest's temporary directory.
    """

    monkeypatch.chdir(
        tmp_path
    )

    return AgentRuntime()


def test_runtime_wires_shared_verification_profile_components(
    monkeypatch,
    tmp_path,
):
    runtime = create_runtime(
        monkeypatch,
        tmp_path,
    )

    assert isinstance(
        runtime.verification_profile_factory,
        VerificationProfileFactory,
    )
    assert isinstance(
        runtime.verification_collector,
        VerificationEvidenceCollector,
    )

    assert (
        runtime.verification_collector.tools
        is runtime.tools
    )


def test_runtime_wires_shared_verification_coordinator(
    monkeypatch,
    tmp_path,
):
    runtime = create_runtime(
        monkeypatch,
        tmp_path,
    )

    assert isinstance(
        runtime.verification_coordinator,
        VerificationCoordinator,
    )
    assert (
        runtime.verification_coordinator.profile_factory
        is runtime.verification_profile_factory
    )
    assert (
        runtime.verification_coordinator.collector
        is runtime.verification_collector
    )
    assert (
        runtime.verification_coordinator.verification_runtime
        is runtime.verification_runtime
    )
    assert (
        runtime.verification_coordinator.collector.tools
        is runtime.tools
    )


def test_new_wiring_preserves_existing_shared_services(
    monkeypatch,
    tmp_path,
):
    runtime = create_runtime(
        monkeypatch,
        tmp_path,
    )

    assert (
        runtime.action_runtime.incident_store
        is runtime.incident_store
    )
    assert (
        runtime.verification_runtime.incident_store
        is runtime.incident_store
    )
    assert (
        runtime.verification_runtime.verification_service
        is runtime.verification
    )
    assert (
        runtime.verification.store
        is runtime.verification_store
    )


def test_shared_factory_builds_profile_without_calling_tools(
    monkeypatch,
    tmp_path,
):
    runtime = create_runtime(
        monkeypatch,
        tmp_path,
    )
    call_mock = AsyncMock(
        side_effect=AssertionError(
            "Profile creation must not call tools"
        )
    )
    monkeypatch.setattr(
        runtime.tools,
        "call",
        call_mock,
    )

    profile = (
        runtime.verification_profile_factory.create(
            ActionPlan(
                type=(
                    ActionType.INCREASE_MEMORY_LIMIT
                ),
                target="payment-api",
            ),
            namespace="payment",
            cluster="prod-a",
        )
    )

    assert profile.name == (
        "increase_memory_limit_v1"
    )
    assert profile.namespace == "payment"
    assert profile.cluster == "prod-a"
    assert len(profile.probes) == 3
    call_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_runtime_initialization_does_not_create_verification(
    monkeypatch,
    tmp_path,
):
    runtime = create_runtime(
        monkeypatch,
        tmp_path,
    )

    results = await runtime.verification.list_all()

    assert results == []