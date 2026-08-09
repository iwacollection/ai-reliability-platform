from types import SimpleNamespace

import pytest

from datetime import UTC, datetime

from common.domain.event import (
    Header,
    Resource,
    Signal,
    StandardEvent,
)
from common.domain.event.enums import (
    EventSource,
    ResourceKind,
    Severity,
    SignalType,
)

from services.agent_runtime.app.incident.enums import (
    IncidentStatus,
)
from services.agent_runtime.app.incident.store import (
    IncidentStore,
)
from services.agent_runtime.app.model.context import (
    AgentContext,
)
from services.agent_runtime.app.model.result import (
    AgentResult,
)
from services.agent_runtime.app.pipeline.planner_pipeline import (
    PlannerPipeline,
)


class FakeAgent:
    """
    Controllable Agent used by lifecycle tests.
    """

    def __init__(
        self,
        name: str,
        result: AgentResult,
    ) -> None:
        self.name = name
        self.result = result
        self.observed_statuses: list[
            IncidentStatus
        ] = []

    async def run(
        self,
        context: AgentContext,
    ) -> AgentResult:
        self.observed_statuses.append(
            context.incident.status
        )

        return self.result


class FakeRegistry:
    """
    Minimal registry implementing the interface used by PlannerPipeline.
    """

    def __init__(
        self,
        agents: list[FakeAgent],
    ) -> None:
        self.agents = {
            agent.name: agent
            for agent in agents
        }

    def get(
        self,
        name: str,
    ) -> FakeAgent:
        return self.agents[name]


class FakePlanner:
    """
    Return a deterministic execution order.
    """

    def __init__(
        self,
        order: list[str],
    ) -> None:
        self.order = order

    def build_execution_order(
        self,
        registry,
    ) -> list[str]:
        return list(self.order)


class FakeTracer:
    """
    Minimal tracer used to isolate lifecycle behavior.
    """

    def start(
        self,
        **kwargs,
    ):
        return SimpleNamespace(
            trace_id=kwargs["trace_id"],
            agent=kwargs["agent"],
        )

    def finish(
        self,
        **kwargs,
    ) -> None:
        return None


class FakeEvaluationRegistry:
    """
    Disable evaluation during lifecycle unit tests.
    """

    def list(self) -> list:
        return []


def build_context() -> AgentContext:
    event = StandardEvent(
        header=Header(
            source=EventSource.ALERTMANAGER,
            occurred_at=datetime.now(UTC),
        ),
        signal=Signal(
            type=SignalType.ALERT,
            name="PodHighCPU",
            severity=Severity.CRITICAL,
            message="CPU > 90%",
        ),
        resources=[
            Resource(
                kind=ResourceKind.POD,
                name="payment-api",
            )
        ],
    )

    return AgentContext(
        event=event,
    )


def build_pipeline(
    agents: list[FakeAgent],
    incident_store: IncidentStore | None = None,
) -> PlannerPipeline:
    return PlannerPipeline(
        registry=FakeRegistry(agents),
        planner=FakePlanner(
            [
                agent.name
                for agent in agents
            ]
        ),
        tracer=FakeTracer(),
        evaluators=FakeEvaluationRegistry(),
        incident_store=incident_store,
    )


@pytest.mark.asyncio
async def test_incident_waits_for_approval():
    rca_agent = FakeAgent(
        name="rca",
        result=AgentResult(
            agent="rca",
            success=True,
            score=0.95,
            message="Root cause confirmed",
            data={
                "root_cause": "Memory limit exceeded",
            },
        ),
    )

    healing_agent = FakeAgent(
        name="healing",
        result=AgentResult(
            agent="healing",
            success=True,
            score=1.0,
            message="Healing plan generated",
            data={
                "action": {
                    "type": "increase_memory_limit",
                    "target": "payment-api",
                },
                "approval_required": True,
            },
        ),
    )

    context = build_context()

    pipeline = build_pipeline(
        [
            rca_agent,
            healing_agent,
        ]
    )

    assert context.incident.status == (
        IncidentStatus.NEW
    )

    results = await pipeline.execute(
        context
    )

    assert rca_agent.observed_statuses == [
        IncidentStatus.ANALYZING
    ]

    assert healing_agent.observed_statuses == [
        IncidentStatus.HEALING
    ]

    assert context.incident.status == (
        IncidentStatus.CONFIRMED
    )

    assert (
        results[-1].data["incident_status"]
        == IncidentStatus.CONFIRMED.value
    )

    assert context.incident.reason is not None

    assert "approval" in (
        context.incident.reason.lower()
    )


@pytest.mark.asyncio
async def test_healing_boolean_does_not_resolve_persistent_incident(
    tmp_path,
):
    """
    Raw HealingAgent boolean fields are not
    persisted Verification evidence.

    A persistent Pipeline must remain HEALING
    until VerificationRuntime stores a terminal
    PASSED VerificationResult.
    """

    rca_agent = FakeAgent(
        name="rca",
        result=AgentResult(
            agent="rca",
            success=True,
            score=0.95,
            message="Root cause confirmed",
            data={
                "root_cause": (
                    "Memory limit exceeded"
                ),
            },
        ),
    )

    healing_agent = FakeAgent(
        name="healing",
        result=AgentResult(
            agent="healing",
            success=True,
            score=1.0,
            message="Healing claimed verified",
            data={
                "action": {
                    "type": (
                        "increase_memory_limit"
                    ),
                    "target": "payment-api",
                },
                "approval_required": False,

                #
                # Untrusted legacy field.
                # This must not resolve a
                # persistent Incident.
                #
                "verified": True,
            },
        ),
    )

    context = build_context()

    pipeline = build_pipeline(
        [
            rca_agent,
            healing_agent,
        ],
        incident_store=IncidentStore(
            db_path=(
                tmp_path
                / "incidents.db"
            )
        ),
    )

    results = await pipeline.execute(
        context
    )

    assert context.incident.status == (
        IncidentStatus.HEALING
    )

    assert (
        results[-1].data[
            "incident_status"
        ]
        == IncidentStatus.HEALING.value
    )

    assert context.incident.reason is not None

    assert (
        "awaiting execution and verification"
        in context.incident.reason.lower()
    )

    persisted = await (
        pipeline.incident_store.get(
            context.incident.id
        )
    )

    assert persisted is not None

    assert persisted.status == (
        IncidentStatus.HEALING
    )

@pytest.mark.asyncio
async def test_failed_incident_is_sticky():
    diagnosis_agent = FakeAgent(
        name="diagnosis",
        result=AgentResult(
            agent="diagnosis",
            success=False,
            score=0.0,
            message="Evidence collection failed",
            data={
                "error": "Prometheus unavailable",
            },
        ),
    )

    rca_agent = FakeAgent(
        name="rca",
        result=AgentResult(
            agent="rca",
            success=True,
            score=0.95,
            message="RCA completed",
            data={
                "root_cause": "Unknown",
            },
        ),
    )

    healing_agent = FakeAgent(
        name="healing",
        result=AgentResult(
            agent="healing",
            success=True,
            score=1.0,
            message="Healing verified",
            data={
                "approval_required": False,
                "verified": True,
            },
        ),
    )

    context = build_context()

    pipeline = build_pipeline(
        [
            diagnosis_agent,
            rca_agent,
            healing_agent,
        ]
    )

    results = await pipeline.execute(
        context
    )

    assert context.incident.status == (
        IncidentStatus.FAILED
    )

    assert context.incident.reason is not None

    assert "diagnosis" in (
        context.incident.reason.lower()
    )

    assert all(
        result.data["incident_status"]
        == IncidentStatus.FAILED.value
        for result in results
    )

    assert rca_agent.observed_statuses == [
        IncidentStatus.FAILED
    ]

    assert healing_agent.observed_statuses == [
        IncidentStatus.FAILED
    ]

