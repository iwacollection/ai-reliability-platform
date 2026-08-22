from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from services.agent_runtime.app.investigation.dsh_investigation_reasoner import (
    DshInvestigationReasoner,
    DshInvestigationReasonerConfig,
    DshInvestigationReasonerError,
)
from services.agent_runtime.app.investigation.dsh_runtime_adapter import (
    DshRunResult,
)
from services.agent_runtime.app.investigation.models import (
    EvidenceItem,
    InvestigationProbe,
    InvestigationScope,
    InvestigationState,
)


NOW = datetime(
    2026,
    8,
    20,
    tzinfo=UTC,
)


class FakeDshRuntime:
    def __init__(
        self,
        response: str,
        *,
        finish_reason: str = "completed",
        failure: Exception | None = None,
    ) -> None:
        self.response = response
        self.finish_reason = finish_reason
        self.failure = failure
        self.initialize_calls = 0
        self.turn_calls = 0
        self.closed = False
        self.last_prompt: str | None = None
        self.last_session_id: str | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.closed = True

    async def initialize(
        self,
        *,
        cwd: str,
        provider: str,
        model: str,
        max_tokens: int | None = None,
    ) -> dict:
        self.initialize_calls += 1
        return {
            "serverInfo": {
                "name": "deepseek-harness-sdk-runtime",
            }
        }

    async def run_turn(
        self,
        input_text: str,
        *,
        session_id: str,
    ) -> DshRunResult:
        self.turn_calls += 1
        self.last_prompt = input_text
        self.last_session_id = session_id
        if self.failure is not None:
            raise self.failure
        return DshRunResult(
            session_id=session_id,
            final_response=self.response,
            finish_reason=self.finish_reason,
            events=(),
            notifications=(),
        )


def scope() -> InvestigationScope:
    return InvestigationScope(
        alert_name="PodOOMKilled",
        resource="payment-api-abc",
        namespace="payment",
        cluster="prod-a",
    )


def state(
    *,
    evidence: bool = False,
) -> InvestigationState:
    current = InvestigationState(
        scope=scope(),
    )
    if evidence:
        current.evidence.append(
            EvidenceItem(
                evidence_id="trusted-evidence-1",
                probe=InvestigationProbe.KUBERNETES_POD_STATE,
                source="kubernetes",
                success=True,
                trusted=True,
                production_signal=True,
                reliability=0.95,
                observed_at=NOW,
                cluster="prod-a",
                cluster_verified=True,
                facts={"reason": "OOMKilled"},
            )
        )
    return current


def decision_json(
    *,
    evidence_id: str | None = None,
) -> str:
    return json.dumps(
        {
            "hypotheses": [
                {
                    "hypothesis_id": "memory-pressure",
                    "cause": "container memory pressure",
                    "confidence": 0.55,
                    "supporting_evidence_ids": (
                        [evidence_id]
                        if evidence_id is not None
                        else []
                    ),
                    "conflicting_evidence_ids": [],
                    "missing_evidence": ["pod state"],
                    "optional_evidence": [],
                }
            ],
            "rationale_summary": "collect bounded pod state",
            "stop": False,
            "stop_reason": None,
            "next_probe": "kubernetes_pod_state",
            "conclusion": None,
        }
    )


@pytest.mark.asyncio
async def test_dsh_reasoner_returns_valid_symbolic_read_only_decision(
    tmp_path: Path,
):
    runtime = FakeDshRuntime(
        decision_json()
    )
    reasoner = DshInvestigationReasoner(
        runtime_factory=lambda: runtime,
        config=DshInvestigationReasonerConfig(
            cwd=str(tmp_path),
            provider="deepseek-official",
            model="deepseek-v4-flash",
            max_tokens=2048,
        ),
    )

    decision = await reasoner.decide(
        scope(),
        state(),
    )

    assert decision.stop is False
    assert (
        decision.next_probe
        == InvestigationProbe.KUBERNETES_POD_STATE
    )
    assert runtime.initialize_calls == 1
    assert runtime.turn_calls == 1
    assert runtime.closed is True
    assert runtime.last_session_id.startswith(
        "investigation-reasoner-"
    )
    assert "Never invent evidence" in runtime.last_prompt
    assert "INPUT_JSON" in runtime.last_prompt


@pytest.mark.asyncio
async def test_dsh_reasoner_rejects_untrusted_evidence_reference(
    tmp_path: Path,
):
    runtime = FakeDshRuntime(
        decision_json(
            evidence_id="invented-evidence"
        )
    )
    reasoner = DshInvestigationReasoner(
        runtime_factory=lambda: runtime,
        config=DshInvestigationReasonerConfig(
            cwd=str(tmp_path),
        ),
    )

    with pytest.raises(
        DshInvestigationReasonerError,
        match="untrusted evidence",
    ):
        await reasoner.decide(
            scope(),
            state(evidence=True),
        )


@pytest.mark.asyncio
async def test_dsh_reasoner_rejects_markdown_wrapped_json(
    tmp_path: Path,
):
    runtime = FakeDshRuntime(
        "```json\n"
        + decision_json()
        + "\n```"
    )
    reasoner = DshInvestigationReasoner(
        runtime_factory=lambda: runtime,
        config=DshInvestigationReasonerConfig(
            cwd=str(tmp_path),
        ),
    )

    with pytest.raises(
        DshInvestigationReasonerError,
        match="non-raw JSON",
    ):
        await reasoner.decide(
            scope(),
            state(),
        )


@pytest.mark.asyncio
async def test_dsh_reasoner_sanitizes_runtime_failure(
    tmp_path: Path,
):
    secret = "secret-provider-token"
    runtime = FakeDshRuntime(
        decision_json(),
        failure=RuntimeError(secret),
    )
    reasoner = DshInvestigationReasoner(
        runtime_factory=lambda: runtime,
        config=DshInvestigationReasonerConfig(
            cwd=str(tmp_path),
        ),
    )

    with pytest.raises(
        DshInvestigationReasonerError,
        match="reasoning failed",
    ) as captured:
        await reasoner.decide(
            scope(),
            state(),
        )

    assert secret not in str(captured.value)


@pytest.mark.asyncio
async def test_dsh_reasoner_rejects_non_completed_turn(
    tmp_path: Path,
):
    runtime = FakeDshRuntime(
        decision_json(),
        finish_reason="max_tokens",
    )
    reasoner = DshInvestigationReasoner(
        runtime_factory=lambda: runtime,
        config=DshInvestigationReasonerConfig(
            cwd=str(tmp_path),
        ),
    )

    with pytest.raises(
        DshInvestigationReasonerError,
        match="did not complete safely",
    ):
        await reasoner.decide(
            scope(),
            state(),
        )


def test_dsh_reasoner_config_is_fail_closed():
    with pytest.raises(
        ValueError,
        match="cwd",
    ):
        DshInvestigationReasonerConfig(
            cwd="",
        )

    with pytest.raises(
        ValueError,
        match="max_tokens",
    ):
        DshInvestigationReasonerConfig(
            cwd=".",
            max_tokens=64,
        )
