import json

import pytest

from services.agent_runtime.app.investigation.llm_gateway_adapter import (
    BaseInvestigationLLM,
)
from services.agent_runtime.app.investigation.models import (
    InvestigationScope,
    InvestigationState,
)
from services.agent_runtime.app.investigation.reasoner import (
    InvestigationReasonerError,
    LLMInvestigationReasoner,
)


class FakeInvestigationLLM(
    BaseInvestigationLLM
):
    def __init__(
        self,
        content,
    ):
        self.content = content
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

        return self.content


class RawGateway:
    async def chat(
        self,
        request,
    ):
        raise AssertionError(
            "Reasoner must never depend directly on raw LLMGateway"
        )


def state() -> InvestigationState:
    return InvestigationState(
        scope=InvestigationScope(
            alert_name="PodOOMKilled",
            alert_message="Pod restarted",
            resource="payment-api",
            namespace="payment",
            cluster="production-a",
        )
    )


@pytest.mark.asyncio
async def test_reasoner_returns_structured_probe_decision():
    investigation_llm = FakeInvestigationLLM(
        json.dumps(
            {
                "hypotheses": [
                    {
                        "hypothesis_id": (
                            "memory_limit_too_low"
                        ),
                        "cause": (
                            "Container memory limit is too low"
                        ),
                        "confidence": 0.5,
                        "supporting_evidence_ids": [],
                        "conflicting_evidence_ids": [],
                        "missing_evidence": [
                            "pod termination state"
                        ],
                    }
                ],
                "rationale_summary": (
                    "Collect Pod termination evidence"
                ),
                "stop": False,
                "stop_reason": None,
                "next_probe": (
                    "kubernetes_pod_state"
                ),
                "conclusion": None,
            }
        )
    )

    reasoner = LLMInvestigationReasoner(
        investigation_llm
    )

    current_state = state()

    decision = await reasoner.decide(
        current_state.scope,
        current_state,
    )

    assert decision.next_probe.value == (
        "kubernetes_pod_state"
    )

    assert len(
        investigation_llm.calls
    ) == 1

    call = investigation_llm.calls[0]

    assert (
        "bounded SRE investigation reasoner"
        in call["system_prompt"]
    )

    assert (
        "Never propose or execute a write"
        in call["system_prompt"]
    )

    assert (
        "allowed_probes"
        in call["prompt"]
    )

    assert (
        "kubernetes_pod_state"
        in call["prompt"]
    )

    assert (
        "kubernetes_patch"
        not in call["prompt"]
    )


def test_reasoner_rejects_direct_raw_gateway_dependency():
    with pytest.raises(
        TypeError,
        match="LLM adapter is invalid",
    ):
        LLMInvestigationReasoner(
            RawGateway()
        )


@pytest.mark.asyncio
async def test_reasoner_rejects_unknown_or_mutating_probe():
    investigation_llm = FakeInvestigationLLM(
        json.dumps(
            {
                "hypotheses": [
                    {
                        "hypothesis_id": "bad",
                        "cause": "Attempt a write",
                        "confidence": 0.9,
                    }
                ],
                "rationale_summary": "Patch the pod",
                "stop": False,
                "next_probe": "kubernetes_patch",
            }
        )
    )

    reasoner = LLMInvestigationReasoner(
        investigation_llm
    )

    current_state = state()

    with pytest.raises(
        InvestigationReasonerError,
        match="invalid decision",
    ):
        await reasoner.decide(
            current_state.scope,
            current_state,
        )


@pytest.mark.asyncio
async def test_reasoner_error_does_not_echo_model_content():
    secret = (
        "super-secret-model-output"
    )

    reasoner = LLMInvestigationReasoner(
        FakeInvestigationLLM(
            secret
        )
    )

    current_state = state()

    with pytest.raises(
        InvestigationReasonerError,
    ) as captured:
        await reasoner.decide(
            current_state.scope,
            current_state,
        )

    assert secret not in str(
        captured.value
    )


@pytest.mark.asyncio
async def test_reasoner_rejects_non_string_llm_response():
    reasoner = LLMInvestigationReasoner(
        FakeInvestigationLLM(
            None
        )
    )

    current_state = state()

    with pytest.raises(
        InvestigationReasonerError,
        match="returned no JSON",
    ):
        await reasoner.decide(
            current_state.scope,
            current_state,
        )
