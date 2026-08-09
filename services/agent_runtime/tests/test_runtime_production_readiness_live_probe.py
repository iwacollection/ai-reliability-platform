from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.agent_runtime.app.investigation.live_readiness import (
    ProductionReadinessLiveProbeError,
)
from services.agent_runtime.app.model.context import AgentContext


class FakeLiveProbe:
    def __init__(self, snapshot):
        self.snapshot_value = snapshot
        self.calls = []

    async def probe_event(
        self,
        event,
        *,
        acknowledgement,
        reason,
    ):
        self.calls.append(
            {
                "event": event,
                "acknowledgement": acknowledgement,
                "reason": reason,
            }
        )
        return SimpleNamespace(
            snapshot=lambda: dict(self.snapshot_value)
        )


@pytest.mark.asyncio
async def test_runtime_live_readiness_is_explicit_and_records_sanitized_snapshot():
    from services.agent_runtime.app.runtime.runtime import AgentRuntime

    runtime = object.__new__(AgentRuntime)
    runtime.tools = SimpleNamespace()

    live = FakeLiveProbe(
        {
            "schema_version": "v1",
            "read_only": True,
            "decision_influence": False,
            "ready": True,
            "cluster": "prod-us-03",
            "kubernetes_probe_ready": True,
            "prometheus_probe_ready": True,
            "issues": [],
        }
    )
    runtime.production_multi_cluster_live_readiness = live

    context = AgentContext.model_construct(
        event=SimpleNamespace(resources=[]),
        tools=runtime.tools,
        metadata={},
    )

    snapshot = await runtime.run_production_multi_cluster_live_readiness(
        context,
        acknowledgement=(
            "I_ACKNOWLEDGE_PRODUCTION_READINESS_LIVE_READS"
        ),
        reason="operator preflight",
    )

    assert snapshot["ready"] is True
    assert (
        context.metadata[
            "production_multi_cluster_live_readiness"
        ]["ready"]
        is True
    )
    assert len(live.calls) == 1


@pytest.mark.asyncio
async def test_runtime_live_readiness_unavailable_fails_before_network():
    from services.agent_runtime.app.runtime.runtime import AgentRuntime

    runtime = object.__new__(AgentRuntime)
    runtime.tools = SimpleNamespace()
    runtime.production_multi_cluster_live_readiness = None

    context = AgentContext.model_construct(
        event=SimpleNamespace(resources=[]),
        tools=runtime.tools,
        metadata={},
    )

    with pytest.raises(
        ProductionReadinessLiveProbeError,
        match="unavailable",
    ):
        await runtime.run_production_multi_cluster_live_readiness(
            context,
            acknowledgement=(
                "I_ACKNOWLEDGE_PRODUCTION_READINESS_LIVE_READS"
            ),
            reason="operator preflight",
        )
