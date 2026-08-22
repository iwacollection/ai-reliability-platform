from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.agent_runtime.app.investigation.engine import (
    BaseInvestigationEngine,
    CustomInvestigationEngine,
)
from services.agent_runtime.app.investigation.models import (
    IncidentHypothesis,
    InvestigationDecision,
    InvestigationProbe,
    InvestigationScope,
    InvestigationState,
)
from services.agent_runtime.app.investigation.reasoner import (
    BaseInvestigationReasoner,
)
from services.agent_runtime.app.investigation.session_driver import (
    DurableInvestigationSessionDriver,
)
from services.agent_runtime.app.investigation.session_loop import (
    DurableInvestigationSessionLoop,
    InvestigationSessionLoopOutcome,
)
from services.agent_runtime.app.investigation.session_service import (
    InvestigationSessionService,
)
from services.agent_runtime.app.investigation.session_store import (
    InvestigationSessionStore,
)


class OneDecisionReasoner(BaseInvestigationReasoner):
    def __init__(self):
        self.calls = 0

    async def decide(self, scope, state):
        self.calls += 1
        return InvestigationDecision(
            hypotheses=[
                IncidentHypothesis(
                    hypothesis_id="memory-pressure",
                    cause="container memory pressure",
                    confidence=0.5,
                    missing_evidence=["pod state"],
                )
            ],
            rationale_summary="collect pod state",
            stop=False,
            next_probe=InvestigationProbe.KUBERNETES_POD_STATE,
        )


class ForbiddenProbeExecutor:
    async def collect(self, context, scope, probe):
        raise AssertionError("Custom Engine first step called a Probe")


def state() -> InvestigationState:
    return InvestigationState(
        scope=InvestigationScope(
            alert_name="PodOOMKilled",
            resource="payment-api-abc",
            namespace="payment",
            cluster="prod-a",
        )
    )


@pytest.mark.asyncio
async def test_custom_engine_preserves_framework_neutral_contract(
    tmp_path,
):
    service = InvestigationSessionService(
        InvestigationSessionStore(
            tmp_path / "engine.db"
        )
    )
    reasoner = OneDecisionReasoner()
    driver = DurableInvestigationSessionDriver(
        session_service=service,
        reasoner=reasoner,
        probe_executor=ForbiddenProbeExecutor(),
    )
    loop = DurableInvestigationSessionLoop(
        session_service=service,
        session_driver=driver,
    )
    engine = CustomInvestigationEngine(
        session_service=service,
        session_loop=loop,
    )

    assert isinstance(engine, BaseInvestigationEngine)
    assert engine.name == "custom"
    created = await engine.create_or_get(
        incident_id=uuid4(),
        run_key="custom-engine-v1",
        initial_state=state(),
        now=datetime.now(UTC),
    )
    result = await engine.advance(
        created.session.session_id,
        context=SimpleNamespace(),
        claimant="custom-worker",
        expected_version=0,
    )

    assert result.outcome == InvestigationSessionLoopOutcome.PAUSED
    assert result.external_calls_made == 1
    assert reasoner.calls == 1
    assert (
        await engine.get(
            created.session.session_id
        )
    ) == result.session


def test_custom_engine_rejects_split_service_graph(tmp_path):
    first = InvestigationSessionService(
        InvestigationSessionStore(
            tmp_path / "first.db"
        )
    )
    second = InvestigationSessionService(
        InvestigationSessionStore(
            tmp_path / "second.db"
        )
    )
    loop = DurableInvestigationSessionLoop(
        session_service=first,
        session_driver=DurableInvestigationSessionDriver(
            session_service=first,
            reasoner=OneDecisionReasoner(),
            probe_executor=ForbiddenProbeExecutor(),
        ),
    )

    with pytest.raises(
        ValueError,
        match="share one Service",
    ):
        CustomInvestigationEngine(
            session_service=second,
            session_loop=loop,
        )
