import pytest

from services.agent_runtime.app.investigation.coordinator import (
    EvidenceDrivenInvestigationCoordinator,
)
from services.agent_runtime.app.investigation.factory import (
    InvestigationFactoryError,
    create_investigation_coordinator,
)
from services.agent_runtime.app.investigation.models import (
    InvestigationDecision,
    InvestigationLimits,
)
from services.agent_runtime.app.investigation.reasoner import (
    BaseInvestigationReasoner,
)
from services.agent_runtime.app.investigation.settings import (
    INVESTIGATION_ENABLE_ACKNOWLEDGEMENT,
    InvestigationSettings,
)


class FakeReasoner(BaseInvestigationReasoner):
    async def decide(
        self,
        scope,
        state,
    ) -> InvestigationDecision:
        raise AssertionError(
            "Factory wiring must not run the reasoner"
        )


class FakeProbeExecutor:
    async def collect(self, context, scope, probe):
        raise AssertionError(
            "Factory wiring must not run a probe"
        )


def enabled_settings() -> InvestigationSettings:
    return InvestigationSettings(
        enabled=True,
        acknowledgement=(
            INVESTIGATION_ENABLE_ACKNOWLEDGEMENT
        ),
        limits=InvestigationLimits(
            max_iterations=4,
            max_tool_calls=7,
            timeout_seconds=12.5,
        ),
    )


def test_disabled_factory_returns_before_component_validation():
    coordinator = create_investigation_coordinator(
        reasoner=object(),
        probe_executor=object(),
        settings=InvestigationSettings(),
    )

    assert coordinator is None


def test_enabled_factory_requires_reasoner():
    with pytest.raises(
        InvestigationFactoryError,
        match="requires a reasoner",
    ):
        create_investigation_coordinator(
            settings=enabled_settings(),
        )


def test_enabled_factory_rejects_invalid_probe_executor():
    with pytest.raises(
        InvestigationFactoryError,
        match="read-only probe executor",
    ):
        create_investigation_coordinator(
            reasoner=FakeReasoner(),
            probe_executor=object(),
            settings=enabled_settings(),
        )


def test_enabled_factory_builds_bounded_coordinator_without_calls():
    reasoner = FakeReasoner()
    probes = FakeProbeExecutor()

    coordinator = create_investigation_coordinator(
        reasoner=reasoner,
        probe_executor=probes,
        settings=enabled_settings(),
    )

    assert isinstance(
        coordinator,
        EvidenceDrivenInvestigationCoordinator,
    )
    assert coordinator.reasoner is reasoner
    assert coordinator.probe_executor is probes
    assert coordinator.limits.max_iterations == 4
    assert coordinator.limits.max_tool_calls == 7
    assert coordinator.limits.timeout_seconds == 12.5

