from __future__ import annotations

from datetime import UTC, datetime

from services.agent_runtime.app.investigation.models import (
    EvidenceItem,
    InvestigationProbe,
    InvestigationScope,
    InvestigationState,
)
from services.agent_runtime.app.investigation.reasoner import (
    LLMInvestigationReasoner,
)


def test_reasoner_prompt_contains_terminal_contracts():
    scope = InvestigationScope(
        alert_name="PodOOMKilled",
        alert_message="Pod restarted",
        resource="payment-api",
        namespace="payment",
        cluster="benchmark-lab",
    )
    state = InvestigationState(scope=scope)

    prompt = LLMInvestigationReasoner._build_prompt(
        scope=scope,
        state=state,
    )

    assert "sufficient_evidence" in prompt
    assert "insufficient_evidence" in prompt
    assert "no_safe_probe" in prompt
    assert "Terminal sufficient-evidence shape" in prompt
    assert "Terminal insufficient/no-safe-probe shape" in prompt
    assert "Never repeat a probe already listed in attempted_probes" in prompt
    assert "Never cite an evidence ID that is absent from State.evidence" in prompt


def test_reasoner_prompt_lists_trusted_evidence_ids():
    scope = InvestigationScope(
        alert_name="PodOOMKilled",
        alert_message="Pod restarted",
        resource="payment-api",
        namespace="payment",
        cluster="benchmark-lab",
    )

    state = InvestigationState(
        scope=scope,
        evidence=[
            EvidenceItem(
                evidence_id="known-evidence-001",
                probe=InvestigationProbe.KUBERNETES_POD_STATE,
                source="kubernetes",
                success=True,
                trusted=True,
                production_signal=True,
                reliability=1.0,
                observed_at=datetime(
                    2026, 8, 10, 9, 0, tzinfo=UTC
                ),
                facts={"oom_killed": True},
            )
        ],
    )

    prompt = LLMInvestigationReasoner._build_prompt(
        scope=scope,
        state=state,
    )

    assert "trusted_evidence_ids" in prompt
    assert "known-evidence-001" in prompt
