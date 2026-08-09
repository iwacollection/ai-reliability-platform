from __future__ import annotations

import asyncio

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.agent_runtime.app.investigation.models import (
    EvidenceItem,
    IncidentHypothesis,
    InvestigationDecision,
    InvestigationLimits,
    InvestigationProbe,
    InvestigationScope,
    InvestigationState,
    InvestigationStatus,
)
from services.agent_runtime.app.investigation.reasoner import (
    BaseInvestigationReasoner,
    InvestigationReasonerValidationError,
)
from services.agent_runtime.app.investigation.session_driver import (
    DurableInvestigationSessionDriver,
    InvestigationSessionDriverBlockedError,
)
from services.agent_runtime.app.investigation.session_models import (
    InvestigationSessionStatus,
    InvestigationStepKind,
    InvestigationStepStatus,
    canonical_digest,
)
from services.agent_runtime.app.investigation.session_service import (
    InvestigationSessionService,
)
from services.agent_runtime.app.investigation.session_store import (
    InvestigationSessionStore,
)


class CountingReasoner(BaseInvestigationReasoner):
    def __init__(self, decision=None, error=None, delay=0.0):
        self.decision = decision or _continuing_decision()
        self.error = error
        self.delay = delay
        self.calls = 0

    async def decide(self, scope, state):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.decision


class CountingProbeExecutor:
    def __init__(self, evidence=None, error=None):
        self.evidence = evidence
        self.error = error
        self.calls = 0
        self.probes = []

    async def collect(self, context, scope, probe):
        self.calls += 1
        self.probes.append(probe)
        if self.error is not None:
            raise self.error
        return self.evidence or _evidence(probe=probe)


def _state(
    *,
    limits: InvestigationLimits | None = None,
) -> InvestigationState:
    return InvestigationState(
        scope=InvestigationScope(
            alert_name="PodOOMKilled",
            resource="payment-api-abc",
            namespace="payment",
            cluster="prod-a",
        ),
        limits=limits or InvestigationLimits(),
    )


def _continuing_decision() -> InvestigationDecision:
    return InvestigationDecision(
        hypotheses=[
            IncidentHypothesis(
                hypothesis_id="h1",
                cause="container memory pressure",
                confidence=0.5,
                missing_evidence=["pod state"],
            )
        ],
        rationale_summary="collect pod state",
        stop=False,
        next_probe=InvestigationProbe.KUBERNETES_POD_STATE,
    )


def _evidence(
    *,
    probe=InvestigationProbe.KUBERNETES_POD_STATE,
    cluster="prod-a",
    cluster_verified=True,
) -> EvidenceItem:
    return EvidenceItem(
        evidence_id="evidence-1",
        probe=probe,
        source="kubernetes",
        success=True,
        trusted=True,
        production_signal=True,
        reliability=0.95,
        observed_at=datetime.now(UTC),
        cluster=cluster,
        cluster_verified=cluster_verified,
        facts={"reason": "OOMKilled"},
    )


async def _driver(
    tmp_path,
    *,
    reasoner=None,
    probe_executor=None,
    require_cluster_verified_evidence=False,
    limits=None,
):
    now = datetime.now(UTC)
    service = InvestigationSessionService(
        InvestigationSessionStore(tmp_path / "sessions.db")
    )
    created = await service.create_or_get(
        incident_id=uuid4(),
        run_key="automatic-shadow-v1",
        initial_state=_state(limits=limits),
        now=now,
    )
    driver = DurableInvestigationSessionDriver(
        session_service=service,
        reasoner=reasoner or CountingReasoner(),
        probe_executor=probe_executor or CountingProbeExecutor(),
        require_cluster_verified_evidence=(
            require_cluster_verified_evidence
        ),
        utc_clock=lambda: now + timedelta(seconds=1),
    )
    return driver, service, created.session


@pytest.mark.asyncio
async def test_reasoner_step_calls_once_and_exact_replay_is_zero_call(tmp_path):
    reasoner = CountingReasoner()
    driver, _, session = await _driver(
        tmp_path,
        reasoner=reasoner,
    )

    first = await driver.execute_reasoner_step(
        session.session_id,
        claimant="runtime-worker-1",
    )
    replay = await driver.execute_reasoner_step(
        session.session_id,
        claimant="runtime-worker-1",
    )

    assert reasoner.calls == 1
    assert first.external_call_made is True
    assert first.step.status == InvestigationStepStatus.SUCCEEDED
    assert first.session.status == InvestigationSessionStatus.PAUSED
    assert replay.external_call_made is False
    assert replay.replayed is True
    assert replay.step == first.step


@pytest.mark.asyncio
async def test_cross_instance_reasoner_claim_grants_one_call(tmp_path):
    db_path = tmp_path / "sessions.db"
    now = datetime.now(UTC)
    service = InvestigationSessionService(
        InvestigationSessionStore(db_path)
    )
    created = await service.create_or_get(
        incident_id=uuid4(),
        run_key="automatic-shadow-v1",
        initial_state=_state(),
        now=now,
    )
    reasoner = CountingReasoner(delay=0.02)
    drivers = [
        DurableInvestigationSessionDriver(
            session_service=InvestigationSessionService(
                InvestigationSessionStore(db_path)
            ),
            reasoner=reasoner,
            probe_executor=CountingProbeExecutor(),
            utc_clock=lambda: now + timedelta(seconds=1),
        )
        for _ in range(6)
    ]

    results = await asyncio.gather(
        *[
            driver.execute_reasoner_step(
                created.session.session_id,
                claimant="runtime-worker-1",
            )
            for driver in drivers
        ]
    )

    assert reasoner.calls == 1
    assert sum(item.external_call_made for item in results) == 1
    assert len({item.step.step_id for item in results}) == 1


@pytest.mark.asyncio
async def test_claim_only_replay_requires_recovery_and_does_not_call(tmp_path):
    reasoner = CountingReasoner()
    driver, service, session = await _driver(
        tmp_path,
        reasoner=reasoner,
    )
    digest = canonical_digest(
        {
            "operation": driver._REASONER_OPERATION,
            "session_id": session.session_id,
            "input_digest": session.input_digest,
            "state": session.state,
        }
    )
    await service.claim_step(
        session.session_id,
        kind=InvestigationStepKind.REASONER,
        request_digest=digest,
        claimant="runtime-worker-1",
    )

    replay = await driver.execute_reasoner_step(
        session.session_id,
        claimant="runtime-worker-1",
    )

    assert reasoner.calls == 0
    assert replay.external_call_made is False
    assert replay.recovery_required is True
    assert replay.step.status == InvestigationStepStatus.CLAIMED


@pytest.mark.asyncio
async def test_definite_reasoner_contract_failure_is_durable_failed(tmp_path):
    reasoner = CountingReasoner(
        error=InvestigationReasonerValidationError(
            "invalid decision"
        )
    )
    driver, _, session = await _driver(
        tmp_path,
        reasoner=reasoner,
    )

    result = await driver.execute_reasoner_step(
        session.session_id,
        claimant="runtime-worker-1",
    )

    assert result.session.status == InvestigationSessionStatus.FAILED
    assert result.session.state.status == InvestigationStatus.FAILED
    assert result.step.status == InvestigationStepStatus.FAILED
    assert result.recovery_required is False


@pytest.mark.asyncio
async def test_unknown_reasoner_transport_failure_is_indeterminate(tmp_path):
    reasoner = CountingReasoner(
        error=ConnectionError("credential must not leak")
    )
    driver, _, session = await _driver(
        tmp_path,
        reasoner=reasoner,
    )

    result = await driver.execute_reasoner_step(
        session.session_id,
        claimant="runtime-worker-1",
    )
    replay = await driver.execute_reasoner_step(
        session.session_id,
        claimant="runtime-worker-1",
    )

    assert reasoner.calls == 1
    assert result.session.status == InvestigationSessionStatus.INDETERMINATE
    assert result.step.failure_code == "ConnectionError"
    assert "credential" not in result.session.model_dump_json()
    assert replay.external_call_made is False
    assert replay.recovery_required is True


@pytest.mark.asyncio
async def test_probe_is_derived_from_reasoner_and_replay_is_zero_call(tmp_path):
    probe_executor = CountingProbeExecutor()
    driver, _, session = await _driver(
        tmp_path,
        probe_executor=probe_executor,
    )
    reasoned = await driver.execute_reasoner_step(
        session.session_id,
        claimant="runtime-worker-1",
    )

    first = await driver.execute_probe_step(
        reasoned.session.session_id,
        context=SimpleNamespace(),
        claimant="runtime-worker-1",
    )
    replay = await driver.execute_probe_step(
        reasoned.session.session_id,
        context=SimpleNamespace(),
        claimant="runtime-worker-1",
    )

    assert probe_executor.calls == 1
    assert probe_executor.probes == [
        InvestigationProbe.KUBERNETES_POD_STATE
    ]
    assert first.session.state.tool_call_count == 1
    assert first.session.state.attempted_probes == [
        InvestigationProbe.KUBERNETES_POD_STATE
    ]
    assert len(first.session.state.evidence) == 1
    assert replay.external_call_made is False
    assert replay.step == first.step


@pytest.mark.asyncio
async def test_probe_failure_becomes_bounded_fact_free_evidence(tmp_path):
    probe_executor = CountingProbeExecutor(
        error=RuntimeError("secret-value")
    )
    driver, _, session = await _driver(
        tmp_path,
        probe_executor=probe_executor,
    )
    reasoned = await driver.execute_reasoner_step(
        session.session_id,
        claimant="runtime-worker-1",
    )

    result = await driver.execute_probe_step(
        reasoned.session.session_id,
        context=SimpleNamespace(),
        claimant="runtime-worker-1",
    )

    evidence = result.step.evidence
    assert evidence is not None
    assert evidence.success is False
    assert evidence.trusted is False
    assert evidence.facts == {}
    assert evidence.error_code == "RuntimeError"
    assert "secret-value" not in result.session.model_dump_json()


@pytest.mark.asyncio
async def test_cluster_mismatch_is_replaced_before_persistence(tmp_path):
    probe_executor = CountingProbeExecutor(
        evidence=_evidence(cluster="prod-b")
    )
    driver, _, session = await _driver(
        tmp_path,
        probe_executor=probe_executor,
    )
    reasoned = await driver.execute_reasoner_step(
        session.session_id,
        claimant="runtime-worker-1",
    )

    result = await driver.execute_probe_step(
        reasoned.session.session_id,
        context=SimpleNamespace(),
        claimant="runtime-worker-1",
    )

    evidence = result.step.evidence
    assert evidence is not None
    assert evidence.error_code == "ClusterEvidenceMismatch"
    assert evidence.facts == {}
    assert evidence.cluster is None


@pytest.mark.asyncio
async def test_probe_identity_mismatch_is_replaced_before_persistence(tmp_path):
    probe_executor = CountingProbeExecutor(
        evidence=_evidence(
            probe=InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,
        )
    )
    driver, _, session = await _driver(
        tmp_path,
        probe_executor=probe_executor,
    )
    reasoned = await driver.execute_reasoner_step(
        session.session_id,
        claimant="runtime-worker-1",
    )

    result = await driver.execute_probe_step(
        reasoned.session.session_id,
        context=SimpleNamespace(),
        claimant="runtime-worker-1",
    )

    assert result.step.evidence is not None
    assert result.step.evidence.probe == (
        InvestigationProbe.KUBERNETES_POD_STATE
    )
    assert result.step.evidence.error_code == "ProbeEvidenceMismatch"
    assert result.step.evidence.facts == {}


@pytest.mark.asyncio
async def test_strict_mode_requires_cluster_verified_evidence(tmp_path):
    probe_executor = CountingProbeExecutor(
        evidence=_evidence(
            cluster="prod-a",
            cluster_verified=False,
        )
    )
    driver, _, session = await _driver(
        tmp_path,
        probe_executor=probe_executor,
        require_cluster_verified_evidence=True,
    )
    reasoned = await driver.execute_reasoner_step(
        session.session_id,
        claimant="runtime-worker-1",
    )

    result = await driver.execute_probe_step(
        reasoned.session.session_id,
        context=SimpleNamespace(),
        claimant="runtime-worker-1",
    )

    assert result.step.evidence is not None
    assert (
        result.step.evidence.error_code
        == "ClusterVerificationRequired"
    )


@pytest.mark.asyncio
async def test_probe_cannot_run_without_reasoner_selection(tmp_path):
    driver, _, session = await _driver(tmp_path)

    with pytest.raises(
        InvestigationSessionDriverBlockedError,
        match="not safely resumable",
    ):
        await driver.execute_probe_step(
            session.session_id,
            context=SimpleNamespace(),
            claimant="runtime-worker-1",
        )


@pytest.mark.asyncio
async def test_replay_claimant_cannot_be_spoofed(tmp_path):
    driver, _, session = await _driver(tmp_path)
    await driver.execute_reasoner_step(
        session.session_id,
        claimant="runtime-worker-1",
    )

    with pytest.raises(
        InvestigationSessionDriverBlockedError,
        match="claimant does not match",
    ):
        await driver.execute_reasoner_step(
            session.session_id,
            claimant="runtime-worker-2",
        )


@pytest.mark.asyncio
async def test_driver_does_not_publish_context_or_invoke_write_workflows(tmp_path):
    context = SimpleNamespace(metadata={"existing": "value"})
    driver, _, session = await _driver(tmp_path)
    reasoned = await driver.execute_reasoner_step(
        session.session_id,
        claimant="runtime-worker-1",
    )
    await driver.execute_probe_step(
        reasoned.session.session_id,
        context=context,
        claimant="runtime-worker-1",
    )

    assert context.metadata == {"existing": "value"}
