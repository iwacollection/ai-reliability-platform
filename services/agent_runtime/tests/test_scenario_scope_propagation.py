import pytest

from services.agent_runtime.app.action.models import (
    ActionPlan,
    ActionType,
)
from services.agent_runtime.app.evaluation.replay.engine import (
    ScenarioReplayEngine,
)
from services.agent_runtime.app.evaluation.scenario.cases.pod_oom_killed import (
    create_pod_oom_killed_scenario,
)
from services.agent_runtime.app.evaluation.scenario.models import (
    ScenarioDefinition,
)


def _healing_result() -> dict:
    return {
        "agent": "healing",
        "success": True,
        "score": 1.0,
        "message": "Increase memory limit",
        "data": {
            "action": "increase_memory_limit",
            "target": "payment-api",
            "risk": "medium",
        },
    }


class _Pipeline:
    async def execute(
        self,
        context,
    ) -> list:
        context.results[
            "healing"
        ] = _healing_result()

        return []


class _ActionRuntime:
    def __init__(self) -> None:
        self.namespace: str | None = None
        self.cluster: str | None = None

    async def execute(
        self,
        healing_result: dict,
        incident=None,
        *,
        namespace: str | None = None,
        cluster: str | None = None,
    ):
        self.namespace = namespace
        self.cluster = cluster

        plan = ActionPlan(
            type=(
                ActionType.INCREASE_MEMORY_LIMIT
            ),
            target=(
                healing_result["data"][
                    "target"
                ]
            ),
            namespace=namespace,
            cluster=cluster,
        )

        return plan, {
            "success": False,
            "status": "pending_approval",
            "incident_id": (
                str(incident.id)
                if incident is not None
                else None
            ),
        }


class _Runtime:
    def __init__(self) -> None:
        self.memory = None
        self.tools = None
        self.skills = None
        self.pipeline = _Pipeline()
        self.action_runtime = (
            _ActionRuntime()
        )

    async def execute(
        self,
        context,
    ) -> list:
        # Match the current AgentRuntime contract used by ScenarioReplayEngine
        # while keeping this test stub intentionally limited to its local
        # Pipeline implementation.
        return await self.pipeline.execute(
            context
        )


@pytest.mark.asyncio
async def test_pod_oom_scope_reaches_action_plan() -> None:
    """Scenario scope flows through StandardEvent into ActionRuntime."""

    runtime = _Runtime()
    engine = ScenarioReplayEngine(
        runtime
    )
    scenario = (
        create_pod_oom_killed_scenario()
    )

    event = engine.build_event(
        scenario
    )

    assert len(event.resources) == 1
    assert event.resources[0].namespace == (
        "payment"
    )
    assert event.resources[0].cluster == (
        "production-a"
    )

    replay = await engine.replay(
        scenario
    )

    assert runtime.action_runtime.namespace == (
        "payment"
    )
    assert runtime.action_runtime.cluster == (
        "production-a"
    )
    assert replay["action"] is not None
    assert replay["action"]["plan"][
        "namespace"
    ] == "payment"
    assert replay["action"]["plan"][
        "cluster"
    ] == "production-a"


@pytest.mark.asyncio
async def test_missing_scenario_scope_does_not_default() -> None:
    """A replay without explicit scope must preserve unknown scope."""

    runtime = _Runtime()
    engine = ScenarioReplayEngine(
        runtime
    )
    scenario = ScenarioDefinition(
        name="missing_scope",
        description=(
            "Scenario intentionally omits resource scope"
        ),
        event={
            "alertname": "PodHighCPU",
            "severity": "critical",
            "resource": "payment-api",
        },
    )

    replay = await engine.replay(
        scenario
    )

    assert runtime.action_runtime.namespace is None
    assert runtime.action_runtime.cluster is None
    assert replay["action"] is not None
    assert replay["action"]["plan"][
        "namespace"
    ] is None
    assert replay["action"]["plan"][
        "cluster"
    ] is None
