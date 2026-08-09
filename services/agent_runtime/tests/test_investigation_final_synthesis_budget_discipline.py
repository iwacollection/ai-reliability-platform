from __future__ import annotations

import json

import pytest

from services.agent_runtime.app.investigation.llm_gateway_adapter import (
    BaseInvestigationLLM,
)
from services.agent_runtime.app.investigation.models import (
    InvestigationLimits,
    InvestigationProbe,
    InvestigationScope,
    InvestigationState,
    InvestigationStopReason,
)
from services.agent_runtime.app.investigation.reasoner import (
    LLMInvestigationReasoner,
)


class SequenceLLM(
    BaseInvestigationLLM
):
    def __init__(
        self,
        values,
    ) -> None:
        self.values = list(
            values
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

        return self.values.pop(
            0
        )


def scope() -> InvestigationScope:
    return InvestigationScope(
        alert_name="PodRestartHigh",
        alert_message="payment-api is restarting",
        resource="payment-api",
        namespace="payment",
        cluster="benchmark-lab",
    )


def continue_decision(
    probe: str,
) -> str:
    return json.dumps(
        {
            "hypotheses": [
                {
                    "hypothesis_id": "h1",
                    "cause": "unresolved restart mechanism",
                    "confidence": 0.4,
                    "supporting_evidence_ids": [],
                    "conflicting_evidence_ids": [],
                    "missing_evidence": [
                        "direct root-cause mechanism evidence"
                    ],
                    "optional_evidence": [
                        "restart frequency"
                    ],
                }
            ],
            "rationale_summary": (
                "collect one more corroborative probe"
            ),
            "stop": False,
            "stop_reason": None,
            "next_probe": probe,
            "conclusion": None,
        }
    )


def abstain_decision() -> str:
    return json.dumps(
        {
            "hypotheses": [
                {
                    "hypothesis_id": "h1",
                    "cause": "unresolved restart mechanism",
                    "confidence": 0.3,
                    "supporting_evidence_ids": [],
                    "conflicting_evidence_ids": [],
                    "missing_evidence": [
                        "direct root-cause mechanism evidence"
                    ],
                    "optional_evidence": [
                        "restart frequency"
                    ],
                }
            ],
            "rationale_summary": (
                "bounded evidence cannot establish the mechanism"
            ),
            "stop": True,
            "stop_reason": (
                InvestigationStopReason
                .INSUFFICIENT_EVIDENCE
                .value
            ),
            "next_probe": None,
            "conclusion": None,
        }
    )


def budget_exhausted_state() -> InvestigationState:
    available = [
        InvestigationProbe.KUBERNETES_POD_STATE,
        (
            InvestigationProbe
            .KUBERNETES_PREVIOUS_CONTAINER_LOGS
        ),
        (
            InvestigationProbe
            .KUBERNETES_WORKLOAD_CHANGE
        ),
        (
            InvestigationProbe
            .PROMETHEUS_MEMORY_WORKING_SET
        ),
        (
            InvestigationProbe
            .PROMETHEUS_MEMORY_LIMIT
        ),
        (
            InvestigationProbe
            .PROMETHEUS_RESTART_COUNT
        ),
    ]

    return InvestigationState(
        scope=scope(),
        limits=InvestigationLimits(
            max_iterations=6,
            max_tool_calls=5,
        ),
        iteration_count=5,
        tool_call_count=5,
        available_probes=available,
        attempted_probes=[
            InvestigationProbe.KUBERNETES_POD_STATE,
            (
                InvestigationProbe
                .KUBERNETES_PREVIOUS_CONTAINER_LOGS
            ),
            (
                InvestigationProbe
                .KUBERNETES_WORKLOAD_CHANGE
            ),
            (
                InvestigationProbe
                .PROMETHEUS_MEMORY_WORKING_SET
            ),
            (
                InvestigationProbe
                .PROMETHEUS_MEMORY_LIMIT
            ),
        ],
    )


def test_prompt_exposes_explicit_terminal_budget_state():
    state = budget_exhausted_state()

    prompt = (
        LLMInvestigationReasoner
        ._build_prompt(
            scope=state.scope,
            state=state,
        )
    )

    assert (
        '"remaining_tool_calls": 0'
        in prompt
    )

    assert (
        '"remaining_reasoning_iterations": 1'
        in prompt
    )

    assert (
        '"continuation_allowed": false'
        in prompt
    )

    assert (
        "you MUST return a terminal decision"
        in prompt
    )

    assert (
        "restart count is corroborative"
        in prompt
    )


@pytest.mark.asyncio
async def test_no_budget_continue_is_repaired_into_terminal_abstention():
    state = budget_exhausted_state()

    llm = SequenceLLM(
        [
            continue_decision(
                (
                    InvestigationProbe
                    .PROMETHEUS_RESTART_COUNT
                    .value
                )
            ),
            abstain_decision(),
        ]
    )

    reasoner = LLMInvestigationReasoner(
        llm
    )

    decision = await reasoner.decide(
        state.scope,
        state,
    )

    assert len(
        llm.calls
    ) == 2

    assert decision.stop is True

    assert (
        decision.stop_reason
        == InvestigationStopReason.INSUFFICIENT_EVIDENCE
    )

    assert decision.next_probe is None


@pytest.mark.asyncio
async def test_last_reasoning_iteration_is_reserved_for_synthesis():
    state = InvestigationState(
        scope=scope(),
        limits=InvestigationLimits(
            max_iterations=6,
            max_tool_calls=10,
        ),
        iteration_count=5,
        tool_call_count=2,
        available_probes=[
            InvestigationProbe.KUBERNETES_POD_STATE,
            (
                InvestigationProbe
                .PROMETHEUS_RESTART_COUNT
            ),
        ],
        attempted_probes=[
            InvestigationProbe.KUBERNETES_POD_STATE,
        ],
    )

    llm = SequenceLLM(
        [
            continue_decision(
                (
                    InvestigationProbe
                    .PROMETHEUS_RESTART_COUNT
                    .value
                )
            ),
            abstain_decision(),
        ]
    )

    reasoner = LLMInvestigationReasoner(
        llm
    )

    decision = await reasoner.decide(
        state.scope,
        state,
    )

    assert decision.stop is True
    assert decision.next_probe is None


@pytest.mark.asyncio
async def test_continue_remains_valid_when_probe_and_synthesis_budget_exist():
    state = InvestigationState(
        scope=scope(),
        limits=InvestigationLimits(
            max_iterations=6,
            max_tool_calls=5,
        ),
        iteration_count=2,
        tool_call_count=2,
        available_probes=[
            InvestigationProbe.KUBERNETES_POD_STATE,
            (
                InvestigationProbe
                .PROMETHEUS_RESTART_COUNT
            ),
        ],
        attempted_probes=[
            InvestigationProbe.KUBERNETES_POD_STATE,
        ],
    )

    llm = SequenceLLM(
        [
            continue_decision(
                (
                    InvestigationProbe
                    .PROMETHEUS_RESTART_COUNT
                    .value
                )
            ),
        ]
    )

    reasoner = LLMInvestigationReasoner(
        llm
    )

    decision = await reasoner.decide(
        state.scope,
        state,
    )

    assert len(
        llm.calls
    ) == 1

    assert decision.stop is False

    assert (
        decision.next_probe
        == InvestigationProbe.PROMETHEUS_RESTART_COUNT
    )
