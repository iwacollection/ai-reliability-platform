from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from services.agent_runtime.app.investigation.epistemic_guard import (
    EpistemicConclusionGuard,
)
from services.agent_runtime.app.investigation.llm_gateway_adapter import (
    BaseInvestigationLLM,
)
from services.agent_runtime.app.investigation.models import (
    EvidenceItem,
    IncidentHypothesis,
    InvestigationConclusion,
    InvestigationDecision,
    InvestigationProbe,
    InvestigationScope,
    InvestigationState,
    InvestigationStopReason,
)
from services.agent_runtime.app.investigation.reasoner import (
    InvestigationReasonerRepairValidationError,
    LLMInvestigationReasoner,
)


NOW = datetime(
    2026,
    8,
    10,
    14,
    15,
    tzinfo=UTC,
)


class SequenceInvestigationLLM(
    BaseInvestigationLLM
):
    def __init__(
        self,
        responses,
    ) -> None:
        self.responses = list(
            responses
        )
        self.calls = []

    async def complete(
        self,
        *,
        system_prompt: str,
        prompt: str,
    ) -> str:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "prompt": prompt,
            }
        )

        return self.responses.pop(
            0
        )


def scope() -> InvestigationScope:
    return InvestigationScope(
        alert_name="PodRestartHigh",
        alert_message="payment-api restarts are increasing",
        resource="payment-api",
        namespace="payment",
        cluster="benchmark-lab",
    )


def state() -> InvestigationState:
    return InvestigationState(
        scope=scope()
    )


def valid_probe_decision() -> str:
    return json.dumps(
        {
            "hypotheses": [
                {
                    "hypothesis_id": "h1",
                    "cause": "unresolved startup failure",
                    "confidence": 0.4,
                    "supporting_evidence_ids": [],
                    "conflicting_evidence_ids": [],
                    "missing_evidence": [
                        "previous container logs"
                    ],
                    "optional_evidence": [
                        "restart count"
                    ],
                }
            ],
            "rationale_summary": (
                "previous container logs are the most discriminative next probe"
            ),
            "stop": False,
            "stop_reason": None,
            "next_probe": (
                "kubernetes_previous_container_logs"
            ),
            "conclusion": None,
        }
    )


@pytest.mark.asyncio
async def test_reasoner_repairs_one_invalid_decision_without_new_evidence():
    llm = SequenceInvestigationLLM(
        [
            json.dumps(
                {
                    "hypotheses": [
                        {
                            "hypothesis_id": "bad",
                            "cause": "attempt a write",
                            "confidence": 0.9,
                        }
                    ],
                    "rationale_summary": "bad probe",
                    "stop": False,
                    "next_probe": "kubernetes_patch",
                }
            ),
            valid_probe_decision(),
        ]
    )

    reasoner = LLMInvestigationReasoner(
        llm
    )

    current = state()

    decision = await reasoner.decide(
        current.scope,
        current,
    )

    assert (
        decision.next_probe
        == InvestigationProbe.KUBERNETES_PREVIOUS_CONTAINER_LOGS
    )

    assert len(
        llm.calls
    ) == 2

    repair_call = llm.calls[
        1
    ]

    assert (
        "Repair the decision contract only"
        in repair_call[
            "system_prompt"
        ]
    )

    assert (
        "Re-evaluate the SAME supplied state"
        in repair_call[
            "prompt"
        ]
    )

    assert (
        "kubernetes_patch"
        not in repair_call[
            "prompt"
        ]
    )


@pytest.mark.asyncio
async def test_reasoner_repair_still_fails_closed_after_one_retry():
    invalid = json.dumps(
        {
            "hypotheses": [
                {
                    "hypothesis_id": "bad",
                    "cause": "attempt a write",
                    "confidence": 0.9,
                }
            ],
            "rationale_summary": "bad probe",
            "stop": False,
            "next_probe": "kubernetes_patch",
        }
    )

    llm = SequenceInvestigationLLM(
        [
            invalid,
            invalid,
        ]
    )

    reasoner = LLMInvestigationReasoner(
        llm
    )

    current = state()

    with pytest.raises(
        InvestigationReasonerRepairValidationError,
    ):
        await reasoner.decide(
            current.scope,
            current,
        )

    assert len(
        llm.calls
    ) == 2


def test_reasoner_prompt_contains_probe_affordances_and_replan_rule():
    current = state()

    prompt = (
        LLMInvestigationReasoner
        ._build_prompt(
            scope=current.scope,
            state=current,
        )
    )

    assert (
        "Probe affordances:"
        in prompt
    )

    assert (
        "kubernetes_previous_container_logs"
        in prompt
    )

    assert (
        "If trusted evidence falsifies the current leading hypothesis"
        in prompt
    )

    assert (
        "Replan with at least one evidence-plausible alternative hypothesis"
        in prompt
    )


def trusted(
    evidence_id: str,
    probe: InvestigationProbe,
    value: float | None = None,
    *,
    oom_killed: bool | None = None,
) -> EvidenceItem:
    facts = {}

    if value is not None:
        facts[
            "value_sum"
        ] = value

    if oom_killed is not None:
        facts[
            "oom_killed"
        ] = oom_killed

    return EvidenceItem(
        evidence_id=evidence_id,
        probe=probe,
        source=(
            "kubernetes"
            if probe
            == InvestigationProbe.KUBERNETES_POD_STATE
            else "prometheus"
        ),
        success=True,
        trusted=True,
        production_signal=True,
        reliability=1.0,
        observed_at=NOW,
        facts=facts,
    )


def memory_limit_decision(
    *,
    working: str,
    limit: str,
) -> InvestigationDecision:
    return InvestigationDecision(
        hypotheses=[
            IncidentHypothesis(
                hypothesis_id="h1",
                cause=(
                    "container exceeded memory limit causing OOMKilled"
                ),
                confidence=0.9,
                supporting_evidence_ids=[
                    "pod",
                    working,
                    limit,
                ],
                conflicting_evidence_ids=[],
                missing_evidence=[],
                optional_evidence=[],
            )
        ],
        rationale_summary=(
            "memory evidence supports the proposed threshold mechanism"
        ),
        stop=True,
        stop_reason=(
            InvestigationStopReason.SUFFICIENT_EVIDENCE
        ),
        conclusion=InvestigationConclusion(
            root_cause=(
                "container exceeded memory limit causing OOMKilled"
            ),
            confidence=0.9,
            evidence_ids=[
                "pod",
                working,
                limit,
            ],
        ),
    )


def test_guard_allows_near_limit_oom_support():
    current = InvestigationState(
        scope=scope(),
        evidence=[
            trusted(
                "pod",
                InvestigationProbe.KUBERNETES_POD_STATE,
                oom_killed=True,
            ),
            trusted(
                "working",
                InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,
                530_000_000.0,
            ),
            trusted(
                "limit",
                InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,
                536_870_912.0,
            ),
        ],
    )

    result = (
        EpistemicConclusionGuard()
        .evaluate(
            decision=memory_limit_decision(
                working="working",
                limit="limit",
            ),
            state=current,
        )
    )

    assert result.allowed is True
    assert result.code is None


def test_guard_rejects_far_below_limit_sample_as_positive_limit_support():
    current = InvestigationState(
        scope=scope(),
        evidence=[
            trusted(
                "pod",
                InvestigationProbe.KUBERNETES_POD_STATE,
                oom_killed=True,
            ),
            trusted(
                "working",
                InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,
                300_000_000.0,
            ),
            trusted(
                "limit",
                InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,
                1_073_741_824.0,
            ),
        ],
    )

    result = (
        EpistemicConclusionGuard()
        .evaluate(
            decision=memory_limit_decision(
                working="working",
                limit="limit",
            ),
            state=current,
        )
    )

    assert result.allowed is False
    assert (
        result.code
        == "MemoryLimitEvidenceNotNearThreshold"
    )
