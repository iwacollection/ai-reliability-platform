from __future__ import annotations

import ast
import shutil
import subprocess
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


VERSION = "investigation-epistemic-guard-v2"

AFTER_NAME = (
    "investigation_epistemic_guard_v2_after.txt"
)

ERROR_NAME = (
    "investigation_epistemic_guard_v2_error.txt"
)

GUARD_SOURCE = 'from __future__ import annotations\n\nfrom dataclasses import dataclass\n\nfrom services.agent_runtime.app.investigation.models import (\n    InvestigationDecision,\n    InvestigationState,\n    InvestigationStopReason,\n)\n\n\n@dataclass(\n    frozen=True,\n    slots=True,\n)\nclass EpistemicGuardResult:\n    """\n    Result of one structural conclusion-admissibility check.\n\n    The guard does not invent, rewrite or semantically classify a root cause.\n    It only checks whether a sufficient-evidence conclusion is backed by\n    positive support declared on at least one current hypothesis.\n    """\n\n    allowed: bool\n    code: str | None = None\n\n\nclass EpistemicConclusionGuard:\n    """\n    Fail-safe evidence-discipline guard for terminal RCA decisions.\n\n    This guard intentionally does NOT:\n    - infer a root cause;\n    - inspect domain-specific keywords;\n    - decide whether an alert is a false positive;\n    - replace the Investigation reasoner.\n\n    It only enforces generic epistemic invariants for\n    stop_reason=sufficient_evidence:\n\n    1. at least one current hypothesis has positive supporting evidence;\n    2. every conclusion evidence ID is positive support for one hypothesis;\n    3. conclusion evidence is not conflicting evidence for that hypothesis;\n    4. the supporting hypothesis has a minimum confidence;\n    5. conclusion confidence may not materially exceed that hypothesis.\n\n    If these invariants are not met, the Coordinator may safely downgrade the\n    decision to insufficient_evidence instead of accepting an unsupported RCA.\n    """\n\n    def __init__(\n        self,\n        *,\n        min_supported_confidence: float = 0.5,\n        max_conclusion_confidence_delta: float = 0.05,\n    ) -> None:\n        if not (\n            0.0\n            <= min_supported_confidence\n            <= 1.0\n        ):\n            raise ValueError(\n                "min_supported_confidence must be within [0,1]"\n            )\n\n        if not (\n            0.0\n            <= max_conclusion_confidence_delta\n            <= 1.0\n        ):\n            raise ValueError(\n                "max_conclusion_confidence_delta must be within [0,1]"\n            )\n\n        self.min_supported_confidence = (\n            min_supported_confidence\n        )\n\n        self.max_conclusion_confidence_delta = (\n            max_conclusion_confidence_delta\n        )\n\n    def evaluate(\n        self,\n        *,\n        decision: InvestigationDecision,\n        state: InvestigationState,\n    ) -> EpistemicGuardResult:\n        if not isinstance(\n            decision,\n            InvestigationDecision,\n        ):\n            raise TypeError(\n                "Epistemic guard decision is invalid"\n            )\n\n        if not isinstance(\n            state,\n            InvestigationState,\n        ):\n            raise TypeError(\n                "Epistemic guard state is invalid"\n            )\n\n        if (\n            not decision.stop\n            or decision.stop_reason\n            != InvestigationStopReason.SUFFICIENT_EVIDENCE\n        ):\n            return EpistemicGuardResult(\n                allowed=True\n            )\n\n        conclusion = (\n            decision.conclusion\n        )\n\n        if conclusion is None:\n            return EpistemicGuardResult(\n                allowed=False,\n                code="MissingConclusion",\n            )\n\n        conclusion_ids = set(\n            conclusion.evidence_ids\n        )\n\n        if not conclusion_ids:\n            return EpistemicGuardResult(\n                allowed=False,\n                code="MissingConclusionEvidence",\n            )\n\n        positively_supported = [\n            hypothesis\n            for hypothesis\n            in decision.hypotheses\n            if hypothesis.supporting_evidence_ids\n        ]\n\n        if not positively_supported:\n            return EpistemicGuardResult(\n                allowed=False,\n                code="NoPositiveHypothesisSupport",\n            )\n\n        support_compatible = []\n\n        for hypothesis in positively_supported:\n            supporting_ids = set(\n                hypothesis.supporting_evidence_ids\n            )\n\n            conflicting_ids = set(\n                hypothesis.conflicting_evidence_ids\n            )\n\n            if not conclusion_ids.issubset(\n                supporting_ids\n            ):\n                continue\n\n            if conclusion_ids.intersection(\n                conflicting_ids\n            ):\n                continue\n\n            support_compatible.append(\n                hypothesis\n            )\n\n        if not support_compatible:\n            return EpistemicGuardResult(\n                allowed=False,\n                code="ConclusionEvidenceNotPositiveSupport",\n            )\n\n        confidence_compatible = [\n            hypothesis\n            for hypothesis\n            in support_compatible\n            if (\n                hypothesis.confidence\n                >= self.min_supported_confidence\n            )\n        ]\n\n        if not confidence_compatible:\n            return EpistemicGuardResult(\n                allowed=False,\n                code="SupportedHypothesisConfidenceTooLow",\n            )\n\n        for hypothesis in confidence_compatible:\n            permitted = min(\n                1.0,\n                (\n                    hypothesis.confidence\n                    + self.max_conclusion_confidence_delta\n                ),\n            )\n\n            if (\n                conclusion.confidence\n                <= permitted\n            ):\n                return EpistemicGuardResult(\n                    allowed=True\n                )\n\n        return EpistemicGuardResult(\n            allowed=False,\n            code="ConclusionConfidenceExceedsSupport",\n        )\n\n\n__all__ = [\n    "EpistemicConclusionGuard",\n    "EpistemicGuardResult",\n]\n'
ENGINE_SOURCE = 'from __future__ import annotations\n\nimport json\nfrom dataclasses import dataclass\nfrom datetime import UTC, datetime\nfrom types import SimpleNamespace\nfrom typing import Any\n\nfrom pydantic import BaseModel, ConfigDict, Field\n\nfrom services.agent_runtime.app.evaluation.real_incident.llm_run import (\n    create_historical_llm_runtime,\n)\nfrom services.agent_runtime.app.investigation.coordinator import (\n    EvidenceDrivenInvestigationCoordinator,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    EvidenceItem,\n    InvestigationDecision,\n    InvestigationLimits,\n    InvestigationProbe,\n    InvestigationState,\n    InvestigationStopReason,\n)\nfrom services.agent_runtime.app.investigation.reasoner import (\n    BaseInvestigationReasoner,\n)\n\n\nclass IntelligenceBenchmarkError(\n    RuntimeError\n):\n    pass\n\n\nclass BenchmarkScenario(BaseModel):\n    """\n    One hidden-label Investigation exam.\n\n    hidden_* fields are evaluator-only. They never enter the Agent context,\n    InvestigationScope, LLM prompt, EvidenceItem facts or decision history.\n    """\n\n    model_config = ConfigDict(\n        frozen=True,\n        extra="forbid",\n    )\n\n    key: str\n    title: str\n\n    alert_name: str\n    alert_message: str\n\n    resource: str = "payment-api"\n    namespace: str = "payment"\n    cluster: str = "benchmark-lab"\n\n    evidence_by_probe: dict[\n        InvestigationProbe,\n        dict[str, Any] | str,\n    ]\n\n    hidden_expected_stop_reason: (\n        InvestigationStopReason\n    )\n\n    hidden_required_probes: list[\n        InvestigationProbe\n    ] = Field(\n        default_factory=list\n    )\n\n    hidden_preferred_first_probes: list[\n        InvestigationProbe\n    ] = Field(\n        default_factory=list\n    )\n\n    hidden_root_cause_keyword_groups: list[\n        list[str]\n    ] = Field(\n        default_factory=list\n    )\n\n    hidden_missing_capability_keywords: list[\n        str\n    ] = Field(\n        default_factory=list\n    )\n\n    hidden_max_reasonable_tool_calls: int = Field(\n        default=4,\n        ge=0,\n        le=10,\n    )\n\n\nclass ScenarioScore(BaseModel):\n    model_config = ConfigDict(\n        frozen=True,\n        extra="forbid",\n    )\n\n    scenario_key: str\n    title: str\n\n    score: float = Field(\n        ge=0.0,\n        le=100.0,\n    )\n\n    expected_stop_reason: str\n    outcome_correct: bool\n    grounding_correct: bool\n    required_probe_coverage: float\n    first_probe_quality: bool | None\n    tool_efficiency: float\n    root_cause_or_abstention_correct: bool\n    missing_capability_awareness: bool | None\n\n    final_status: str\n    final_stop_reason: str | None\n    failure_code: str | None\n    epistemic_guard_code: str | None\n    guard_rescued: bool\n\n    attempted_probes: list[str]\n    tool_call_count: int\n    iteration_count: int\n\n    conclusion_root_cause: str | None\n    conclusion_confidence: float | None\n\n    decision_trace: list[\n        dict[str, Any]\n    ]\n\n    notes: list[str] = Field(\n        default_factory=list\n    )\n\n\nclass IntelligenceBenchmarkReport(BaseModel):\n    model_config = ConfigDict(\n        frozen=True,\n        extra="forbid",\n    )\n\n    schema_version: str = "v1"\n    generated_at: datetime\n\n    provider: str\n    mode: str\n\n    scenario_count: int\n    overall_score: float = Field(\n        ge=0.0,\n        le=100.0,\n    )\n\n    outcome_accuracy: float = Field(\n        ge=0.0,\n        le=100.0,\n    )\n\n    abstention_accuracy: float = Field(\n        ge=0.0,\n        le=100.0,\n    )\n\n    sufficient_evidence_accuracy: float = Field(\n        ge=0.0,\n        le=100.0,\n    )\n\n    average_tool_calls: float = Field(\n        ge=0.0,\n    )\n\n    guard_rescue_count: int = Field(\n        ge=0,\n    )\n\n    guard_rescue_rate: float = Field(\n        ge=0.0,\n        le=100.0,\n    )\n\n    scenarios: list[\n        ScenarioScore\n    ]\n\n    strongest_signals: list[str]\n    weakest_signals: list[str]\n\n\nclass BenchmarkProbeExecutor:\n    """\n    Synthetic evidence backend for model-intelligence evaluation.\n\n    The model sees only the evidence corresponding to probes it chose.\n    Hidden labels remain inside BenchmarkScenario and never cross this class\n    into EvidenceItem.\n    """\n\n    def __init__(\n        self,\n        scenario: BenchmarkScenario,\n        *,\n        observed_at: datetime,\n    ) -> None:\n        self.scenario = scenario\n        self.observed_at = observed_at\n        self.calls: list[\n            InvestigationProbe\n        ] = []\n\n    async def collect(\n        self,\n        context,\n        scope,\n        probe,\n    ) -> EvidenceItem:\n        self.calls.append(\n            probe\n        )\n\n        value = (\n            self.scenario\n            .evidence_by_probe\n            .get(\n                probe\n            )\n        )\n\n        if isinstance(\n            value,\n            str,\n        ):\n            raise RuntimeError(\n                "Benchmark probe unavailable"\n            )\n\n        if value is None:\n            raise RuntimeError(\n                "Benchmark probe has no observation"\n            )\n\n        source = (\n            "kubernetes"\n            if probe\n            == InvestigationProbe.KUBERNETES_POD_STATE\n            else "prometheus"\n        )\n\n        return EvidenceItem(\n            evidence_id=(\n                f"{self.scenario.key}:"\n                f"{probe.value}"\n            ),\n            probe=probe,\n            source=source,\n            success=True,\n            trusted=True,\n            production_signal=True,\n            reliability=1.0,\n            observed_at=self.observed_at,\n            facts=dict(\n                value\n            ),\n        )\n\n\nclass TracingReasoner(\n    BaseInvestigationReasoner\n):\n    """\n    Transparent delegate that records the actual Agent decisions.\n\n    It does not modify prompts, decisions, state or provider behavior.\n    """\n\n    def __init__(\n        self,\n        delegate: BaseInvestigationReasoner,\n    ) -> None:\n        if not isinstance(\n            delegate,\n            BaseInvestigationReasoner,\n        ):\n            raise TypeError(\n                "Benchmark delegate reasoner is invalid"\n            )\n\n        self.delegate = delegate\n\n        self.decisions: list[\n            InvestigationDecision\n        ] = []\n\n        self.states: list[\n            InvestigationState\n        ] = []\n\n    async def decide(\n        self,\n        scope,\n        state,\n    ) -> InvestigationDecision:\n        self.states.append(\n            state.model_copy(\n                deep=True\n            )\n        )\n\n        decision = await (\n            self.delegate.decide(\n                scope,\n                state,\n            )\n        )\n\n        self.decisions.append(\n            decision.model_copy(\n                deep=True\n            )\n        )\n\n        return decision\n\n\ndef _context(\n    scenario: BenchmarkScenario,\n):\n    return SimpleNamespace(\n        event=SimpleNamespace(\n            signal=SimpleNamespace(\n                name=scenario.alert_name,\n                message=(\n                    scenario.alert_message\n                ),\n            ),\n            resources=[\n                SimpleNamespace(\n                    name=scenario.resource,\n                    namespace=scenario.namespace,\n                    cluster=scenario.cluster,\n                )\n            ],\n        ),\n        metadata={},\n        variables={},\n    )\n\n\ndef _normalize_text(\n    value: str | None,\n) -> str:\n    if not value:\n        return ""\n\n    return (\n        value\n        .strip()\n        .lower()\n    )\n\n\ndef _all_reasoner_text(\n    decisions: list[\n        InvestigationDecision\n    ],\n) -> str:\n    fragments: list[\n        str\n    ] = []\n\n    for decision in decisions:\n        fragments.append(\n            decision.rationale_summary\n        )\n\n        for hypothesis in decision.hypotheses:\n            fragments.append(\n                hypothesis.cause\n            )\n\n            fragments.extend(\n                hypothesis.missing_evidence\n            )\n\n        if decision.conclusion is not None:\n            fragments.append(\n                decision.conclusion.root_cause\n            )\n\n            fragments.extend(\n                decision.conclusion.remaining_uncertainties\n            )\n\n    return _normalize_text(\n        "\\n".join(\n            fragments\n        )\n    )\n\n\ndef _keyword_groups_match(\n    text: str,\n    groups: list[\n        list[str]\n    ],\n) -> bool:\n    normalized = _normalize_text(\n        text\n    )\n\n    if not groups:\n        return True\n\n    for group in groups:\n        if not any(\n            _normalize_text(\n                token\n            )\n            in normalized\n            for token in group\n        ):\n            return False\n\n    return True\n\n\ndef _decision_trace(\n    decisions: list[\n        InvestigationDecision\n    ],\n) -> list[\n    dict[str, Any]\n]:\n    trace = []\n\n    for index, decision in enumerate(\n        decisions,\n        start=1,\n    ):\n        trace.append(\n            {\n                "iteration": index,\n                "hypotheses": [\n                    {\n                        "hypothesis_id": (\n                            item.hypothesis_id\n                        ),\n                        "cause": item.cause,\n                        "confidence": (\n                            item.confidence\n                        ),\n                        "supporting_evidence_ids": list(\n                            item.supporting_evidence_ids\n                        ),\n                        "conflicting_evidence_ids": list(\n                            item.conflicting_evidence_ids\n                        ),\n                        "missing_evidence": list(\n                            item.missing_evidence\n                        ),\n                    }\n                    for item in decision.hypotheses\n                ],\n                "rationale_summary": (\n                    decision.rationale_summary\n                ),\n                "stop": decision.stop,\n                "stop_reason": (\n                    decision.stop_reason.value\n                    if decision.stop_reason\n                    is not None\n                    else None\n                ),\n                "next_probe": (\n                    decision.next_probe.value\n                    if decision.next_probe\n                    is not None\n                    else None\n                ),\n                "conclusion": (\n                    decision.conclusion.model_dump(\n                        mode="json"\n                    )\n                    if decision.conclusion\n                    is not None\n                    else None\n                ),\n            }\n        )\n\n    return trace\n\n\ndef score_scenario(\n    *,\n    scenario: BenchmarkScenario,\n    state: InvestigationState,\n    decisions: list[\n        InvestigationDecision\n    ],\n) -> ScenarioScore:\n    attempted = list(state.attempted_probes)\n    expected_stop = scenario.hidden_expected_stop_reason\n\n    legitimate_terminal = (\n        state.status.value == "concluded"\n        and state.stop_reason == expected_stop\n    )\n    outcome_correct = legitimate_terminal\n\n    if expected_stop == InvestigationStopReason.SUFFICIENT_EVIDENCE:\n        if not legitimate_terminal or state.conclusion is None:\n            grounding_correct = False\n        else:\n            trusted_ids = {\n                item.evidence_id\n                for item in state.evidence\n                if (\n                    item.success\n                    and item.trusted\n                    and item.production_signal\n                )\n            }\n            conclusion_ids = set(state.conclusion.evidence_ids)\n            grounding_correct = (\n                bool(conclusion_ids)\n                and conclusion_ids.issubset(trusted_ids)\n            )\n    else:\n        grounding_correct = (\n            legitimate_terminal\n            and state.conclusion is None\n        )\n\n    required = set(scenario.hidden_required_probes)\n    attempted_set = set(attempted)\n    required_probe_coverage = (\n        len(required & attempted_set) / len(required)\n        if required\n        else 1.0\n    )\n\n    if scenario.hidden_preferred_first_probes:\n        first_probe_quality = (\n            bool(attempted)\n            and attempted[0]\n            in scenario.hidden_preferred_first_probes\n        )\n    else:\n        first_probe_quality = None\n\n    max_calls = scenario.hidden_max_reasonable_tool_calls\n    if max_calls <= 0:\n        tool_efficiency = 1.0 if state.tool_call_count == 0 else 0.0\n    elif state.tool_call_count <= max_calls:\n        tool_efficiency = 1.0\n    else:\n        tool_efficiency = max(\n            0.0,\n            1.0 - (\n                state.tool_call_count - max_calls\n            ) / max_calls,\n        )\n\n    if expected_stop == InvestigationStopReason.SUFFICIENT_EVIDENCE:\n        root_cause_or_abstention_correct = (\n            legitimate_terminal\n            and state.conclusion is not None\n            and _keyword_groups_match(\n                state.conclusion.root_cause,\n                scenario.hidden_root_cause_keyword_groups,\n            )\n        )\n    else:\n        root_cause_or_abstention_correct = (\n            legitimate_terminal\n            and state.conclusion is None\n        )\n\n    if scenario.hidden_missing_capability_keywords:\n        reasoner_text = _all_reasoner_text(decisions)\n        missing_capability_awareness = any(\n            _normalize_text(keyword) in reasoner_text\n            for keyword\n            in scenario.hidden_missing_capability_keywords\n        )\n    else:\n        missing_capability_awareness = None\n\n    score = 0.0\n    score += 30.0 if outcome_correct else 0.0\n    score += 20.0 if grounding_correct else 0.0\n\n    probe_weight = 30.0 if first_probe_quality is None else 20.0\n    score += required_probe_coverage * probe_weight\n\n    if first_probe_quality is not None:\n        score += 10.0 if first_probe_quality else 0.0\n\n    score += tool_efficiency * 10.0\n    score += 10.0 if root_cause_or_abstention_correct else 0.0\n\n    guard_rescued = (\n        state.epistemic_guard_code\n        is not None\n        and outcome_correct\n    )\n\n    if guard_rescued:\n        score = min(\n            score,\n            85.0,\n        )\n\n    notes: list[str] = []\n\n    if guard_rescued:\n        notes.append(\n            "Epistemic guard converted an unsupported sufficient-evidence "\n            "decision into safe insufficient_evidence."\n        )\n\n    if not outcome_correct:\n        notes.append(\n            "Final stop reason/status did not match the hidden evaluator label."\n        )\n\n    if state.status.value == "failed":\n        notes.append(\n            "Failed investigation is not counted as a valid abstention."\n        )\n\n    if (\n        expected_stop != InvestigationStopReason.SUFFICIENT_EVIDENCE\n        and state.conclusion is not None\n    ):\n        notes.append(\n            "Agent produced an RCA where the benchmark expected abstention."\n        )\n\n    if missing_capability_awareness is False:\n        notes.append(\n            "Agent did not explicitly recognize the expected missing capability."\n        )\n\n    return ScenarioScore(\n        scenario_key=scenario.key,\n        title=scenario.title,\n        expected_stop_reason=expected_stop.value,\n        score=round(\n            min(100.0, max(0.0, score)),\n            1,\n        ),\n        outcome_correct=outcome_correct,\n        grounding_correct=grounding_correct,\n        required_probe_coverage=round(\n            required_probe_coverage,\n            3,\n        ),\n        first_probe_quality=first_probe_quality,\n        tool_efficiency=round(\n            tool_efficiency,\n            3,\n        ),\n        root_cause_or_abstention_correct=(\n            root_cause_or_abstention_correct\n        ),\n        missing_capability_awareness=(\n            missing_capability_awareness\n        ),\n        final_status=state.status.value,\n        final_stop_reason=(\n            state.stop_reason.value\n            if state.stop_reason is not None\n            else None\n        ),\n        failure_code=state.failure_code,\n        epistemic_guard_code=(\n            state.epistemic_guard_code\n        ),\n        guard_rescued=guard_rescued,\n        attempted_probes=[\n            item.value\n            for item in attempted\n        ],\n        tool_call_count=state.tool_call_count,\n        iteration_count=state.iteration_count,\n        conclusion_root_cause=(\n            state.conclusion.root_cause\n            if state.conclusion is not None\n            else None\n        ),\n        conclusion_confidence=(\n            state.conclusion.confidence\n            if state.conclusion is not None\n            else None\n        ),\n        decision_trace=_decision_trace(decisions),\n        notes=notes,\n    )\n\n\nasync def run_scenario(\n    *,\n    reasoner: BaseInvestigationReasoner,\n    scenario: BenchmarkScenario,\n    limits: InvestigationLimits,\n    observed_at: datetime,\n) -> ScenarioScore:\n    tracing = TracingReasoner(\n        reasoner\n    )\n\n    probes = BenchmarkProbeExecutor(\n        scenario,\n        observed_at=observed_at,\n    )\n\n    coordinator = (\n        EvidenceDrivenInvestigationCoordinator(\n            reasoner=tracing,\n            probe_executor=probes,\n            limits=limits,\n            utc_clock=lambda: observed_at,\n        )\n    )\n\n    state = await coordinator.investigate(\n        _context(\n            scenario\n        )\n    )\n\n    return score_scenario(\n        scenario=scenario,\n        state=state,\n        decisions=tracing.decisions,\n    )\n\n\ndef build_bailian_reasoner(\n    *,\n    provider_name: str,\n    limits: InvestigationLimits,\n) -> BaseInvestigationReasoner:\n    runtime = (\n        create_historical_llm_runtime(\n            limits=limits,\n            provider_name=provider_name,\n        )\n    )\n\n    coordinator = getattr(\n        runtime,\n        "investigation_coordinator",\n        None,\n    )\n\n    reasoner = getattr(\n        coordinator,\n        "reasoner",\n        None,\n    )\n\n    if not isinstance(\n        reasoner,\n        BaseInvestigationReasoner,\n    ):\n        raise IntelligenceBenchmarkError(\n            "Benchmark could not obtain the canonical Investigation reasoner"\n        )\n\n    return reasoner\n\n\ndef build_report(\n    *,\n    provider: str,\n    mode: str,\n    scenarios: list[\n        ScenarioScore\n    ],\n) -> IntelligenceBenchmarkReport:\n    if not scenarios:\n        raise IntelligenceBenchmarkError(\n            "Benchmark produced no scenario results"\n        )\n\n    overall_score = (\n        sum(item.score for item in scenarios)\n        / len(scenarios)\n    )\n\n    outcome_accuracy = (\n        sum(\n            1\n            for item in scenarios\n            if item.outcome_correct\n        )\n        / len(scenarios)\n        * 100.0\n    )\n\n    expected_abstention_cases = [\n        item\n        for item in scenarios\n        if item.expected_stop_reason\n        != InvestigationStopReason.SUFFICIENT_EVIDENCE.value\n    ]\n\n    abstention_accuracy = (\n        sum(\n            1\n            for item in expected_abstention_cases\n            if (\n                item.outcome_correct\n                and item.root_cause_or_abstention_correct\n            )\n        )\n        / len(expected_abstention_cases)\n        * 100.0\n        if expected_abstention_cases\n        else 0.0\n    )\n\n    expected_sufficient_cases = [\n        item\n        for item in scenarios\n        if item.expected_stop_reason\n        == InvestigationStopReason.SUFFICIENT_EVIDENCE.value\n    ]\n\n    sufficient_evidence_accuracy = (\n        sum(\n            1\n            for item in expected_sufficient_cases\n            if (\n                item.outcome_correct\n                and item.grounding_correct\n                and item.root_cause_or_abstention_correct\n            )\n        )\n        / len(expected_sufficient_cases)\n        * 100.0\n        if expected_sufficient_cases\n        else 0.0\n    )\n\n    average_tool_calls = (\n        sum(\n            item.tool_call_count\n            for item in scenarios\n        )\n        / len(scenarios)\n    )\n\n    guard_rescue_count = sum(\n        1\n        for item in scenarios\n        if item.guard_rescued\n    )\n\n    guard_rescue_rate = (\n        guard_rescue_count\n        / len(scenarios)\n        * 100.0\n    )\n\n    ordered = sorted(\n        scenarios,\n        key=lambda item: (\n            item.score,\n            item.scenario_key,\n        ),\n    )\n\n    weakest = [\n        f"{item.scenario_key}: {item.score:.1f}/100"\n        for item in ordered[:3]\n    ]\n\n    strongest = [\n        f"{item.scenario_key}: {item.score:.1f}/100"\n        for item in reversed(ordered[-3:])\n    ]\n\n    return IntelligenceBenchmarkReport(\n        generated_at=datetime.now(UTC),\n        provider=provider,\n        mode=mode,\n        scenario_count=len(scenarios),\n        overall_score=round(\n            overall_score,\n            1,\n        ),\n        outcome_accuracy=round(\n            outcome_accuracy,\n            1,\n        ),\n        abstention_accuracy=round(\n            abstention_accuracy,\n            1,\n        ),\n        sufficient_evidence_accuracy=round(\n            sufficient_evidence_accuracy,\n            1,\n        ),\n        average_tool_calls=round(\n            average_tool_calls,\n            2,\n        ),\n        guard_rescue_count=(\n            guard_rescue_count\n        ),\n        guard_rescue_rate=round(\n            guard_rescue_rate,\n            1,\n        ),\n        scenarios=scenarios,\n        strongest_signals=strongest,\n        weakest_signals=weakest,\n    )\n\n\ndef render_report(\n    report: IntelligenceBenchmarkReport,\n) -> str:\n    lines = [\n        "=" * 96,\n        "INVESTIGATION INTELLIGENCE BENCHMARK v1",\n        "=" * 96,\n        "",\n        f"GeneratedAt: {report.generated_at.isoformat()}",\n        f"Provider: {report.provider}",\n        f"Mode: {report.mode}",\n        f"Scenarios: {report.scenario_count}",\n        "",\n        f"OverallScore: {report.overall_score:.1f}/100",\n        f"OutcomeAccuracy: {report.outcome_accuracy:.1f}%",\n        f"AbstentionAccuracy: {report.abstention_accuracy:.1f}%",\n        (\n            "SufficientEvidenceAccuracy: "\n            f"{report.sufficient_evidence_accuracy:.1f}%"\n        ),\n        f"AverageToolCalls: {report.average_tool_calls:.2f}",\n        f"GuardRescueCount: {report.guard_rescue_count}",\n        f"GuardRescueRate: {report.guard_rescue_rate:.1f}%",\n        "",\n        "Important:",\n        "- This is a controlled synthetic-evidence intelligence benchmark.",\n        "- The actual LLM Investigation reasoner is used in live mode.",\n        "- Hidden evaluator labels never enter the Agent prompt.",\n        "- This is stronger than unit testing but is not a production validation.",\n        "",\n        "SCENARIOS",\n        "-" * 96,\n    ]\n\n    for item in report.scenarios:\n        lines.extend(\n            [\n                "",\n                (\n                    f"[{item.score:5.1f}] "\n                    f"{item.scenario_key} - {item.title}"\n                ),\n                (\n                    "  outcome_correct="\n                    f"{item.outcome_correct}"\n                ),\n                (\n                    "  grounding_correct="\n                    f"{item.grounding_correct}"\n                ),\n                (\n                    "  required_probe_coverage="\n                    f"{item.required_probe_coverage:.3f}"\n                ),\n                (\n                    "  first_probe_quality="\n                    f"{item.first_probe_quality}"\n                ),\n                (\n                    "  tool_efficiency="\n                    f"{item.tool_efficiency:.3f}"\n                ),\n                (\n                    "  root_cause_or_abstention_correct="\n                    f"{item.root_cause_or_abstention_correct}"\n                ),\n                (\n                    "  missing_capability_awareness="\n                    f"{item.missing_capability_awareness}"\n                ),\n                (\n                    "  expected_stop_reason="\n                    f"{item.expected_stop_reason}"\n                ),\n                (\n                    "  final="\n                    f"{item.final_status}/"\n                    f"{item.final_stop_reason}"\n                ),\n                (\n                    "  failure_code="\n                    f"{item.failure_code}"\n                ),\n                (\n                    "  epistemic_guard_code="\n                    f"{item.epistemic_guard_code}"\n                ),\n                (\n                    "  guard_rescued="\n                    f"{item.guard_rescued}"\n                ),\n                (\n                    "  probes="\n                    + ", ".join(\n                        item.attempted_probes\n                    )\n                ),\n                (\n                    "  conclusion="\n                    + (\n                        item.conclusion_root_cause\n                        or "<NONE>"\n                    )\n                ),\n                (\n                    "  confidence="\n                    + (\n                        str(\n                            item.conclusion_confidence\n                        )\n                        if item.conclusion_confidence\n                        is not None\n                        else "<NONE>"\n                    )\n                ),\n            ]\n        )\n\n        for note in item.notes:\n            lines.append(\n                f"  note: {note}"\n            )\n\n        lines.append(\n            "  decision_trace:"\n        )\n\n        for decision in item.decision_trace:\n            lines.append(\n                "    "\n                + json.dumps(\n                    decision,\n                    ensure_ascii=False,\n                    sort_keys=True,\n                )\n            )\n\n    lines.extend(\n        [\n            "",\n            "STRONGEST",\n            "-" * 96,\n            *[\n                f"- {value}"\n                for value\n                in report.strongest_signals\n            ],\n            "",\n            "WEAKEST",\n            "-" * 96,\n            *[\n                f"- {value}"\n                for value\n                in report.weakest_signals\n            ],\n            "",\n            "=" * 96,\n        ]\n    )\n\n    return "\\n".join(\n        lines\n    ) + "\\n"\n\n\n__all__ = [\n    "BenchmarkProbeExecutor",\n    "BenchmarkScenario",\n    "IntelligenceBenchmarkError",\n    "IntelligenceBenchmarkReport",\n    "ScenarioScore",\n    "TracingReasoner",\n    "build_bailian_reasoner",\n    "build_report",\n    "render_report",\n    "run_scenario",\n    "score_scenario",\n]\n'
GUARD_TEST_SOURCE = 'from __future__ import annotations\n\nfrom datetime import UTC, datetime\nfrom types import SimpleNamespace\n\nimport pytest\n\nfrom services.agent_runtime.app.evaluation.intelligence_benchmark.engine import (\n    BenchmarkScenario,\n    build_report,\n    score_scenario,\n)\nfrom services.agent_runtime.app.investigation.coordinator import (\n    EvidenceDrivenInvestigationCoordinator,\n)\nfrom services.agent_runtime.app.investigation.epistemic_guard import (\n    EpistemicConclusionGuard,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    EvidenceItem,\n    IncidentHypothesis,\n    InvestigationConclusion,\n    InvestigationDecision,\n    InvestigationProbe,\n    InvestigationScope,\n    InvestigationState,\n    InvestigationStatus,\n    InvestigationStopReason,\n)\nfrom services.agent_runtime.app.investigation.reasoner import (\n    BaseInvestigationReasoner,\n    LLMInvestigationReasoner,\n)\n\n\nNOW = datetime(\n    2026,\n    8,\n    10,\n    10,\n    0,\n    tzinfo=UTC,\n)\n\n\ndef scope() -> InvestigationScope:\n    return InvestigationScope(\n        alert_name="PodOOMKilled",\n        alert_message="pod restarted",\n        resource="payment-api",\n        namespace="payment",\n        cluster="benchmark-lab",\n    )\n\n\ndef trusted(\n    evidence_id: str,\n    probe: InvestigationProbe,\n) -> EvidenceItem:\n    return EvidenceItem(\n        evidence_id=evidence_id,\n        probe=probe,\n        source=(\n            "kubernetes"\n            if probe\n            == InvestigationProbe.KUBERNETES_POD_STATE\n            else "prometheus"\n        ),\n        success=True,\n        trusted=True,\n        production_signal=True,\n        reliability=1.0,\n        observed_at=NOW,\n        facts={\n            "value_sum": 1.0,\n            "oom_killed": True,\n        },\n    )\n\n\ndef hypothesis(\n    *,\n    confidence: float,\n    supporting=None,\n    conflicting=None,\n    missing=None,\n) -> IncidentHypothesis:\n    return IncidentHypothesis(\n        hypothesis_id="h1",\n        cause="memory limit exhaustion",\n        confidence=confidence,\n        supporting_evidence_ids=(\n            supporting\n            or []\n        ),\n        conflicting_evidence_ids=(\n            conflicting\n            or []\n        ),\n        missing_evidence=(\n            missing\n            or []\n        ),\n    )\n\n\ndef sufficient(\n    *,\n    confidence: float,\n    hypothesis_value: IncidentHypothesis,\n    evidence_ids,\n    root_cause="memory limit exhaustion",\n) -> InvestigationDecision:\n    return InvestigationDecision(\n        hypotheses=[\n            hypothesis_value\n        ],\n        rationale_summary="evidence sufficient",\n        stop=True,\n        stop_reason=(\n            InvestigationStopReason.SUFFICIENT_EVIDENCE\n        ),\n        conclusion=(\n            InvestigationConclusion(\n                root_cause=root_cause,\n                confidence=confidence,\n                evidence_ids=list(\n                    evidence_ids\n                ),\n            )\n        ),\n    )\n\n\ndef test_guard_allows_positive_supported_conclusion():\n    state = InvestigationState(\n        scope=scope(),\n        evidence=[\n            trusted(\n                "e1",\n                InvestigationProbe.KUBERNETES_POD_STATE,\n            ),\n            trusted(\n                "e2",\n                InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,\n            ),\n        ],\n    )\n\n    decision = sufficient(\n        confidence=0.9,\n        hypothesis_value=hypothesis(\n            confidence=0.9,\n            supporting=[\n                "e1",\n                "e2",\n            ],\n        ),\n        evidence_ids=[\n            "e1",\n            "e2",\n        ],\n    )\n\n    result = (\n        EpistemicConclusionGuard()\n        .evaluate(\n            decision=decision,\n            state=state,\n        )\n    )\n\n    assert result.allowed is True\n    assert result.code is None\n\n\ndef test_guard_rejects_conclusion_built_only_from_conflicting_evidence():\n    state = InvestigationState(\n        scope=scope(),\n        evidence=[\n            trusted(\n                "e1",\n                InvestigationProbe.KUBERNETES_POD_STATE,\n            ),\n            trusted(\n                "e2",\n                InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,\n            ),\n        ],\n    )\n\n    decision = sufficient(\n        confidence=0.9,\n        hypothesis_value=hypothesis(\n            confidence=0.1,\n            conflicting=[\n                "e1",\n                "e2",\n            ],\n        ),\n        evidence_ids=[\n            "e1",\n            "e2",\n        ],\n        root_cause=(\n            "unsupported alternative causal claim"\n        ),\n    )\n\n    result = (\n        EpistemicConclusionGuard()\n        .evaluate(\n            decision=decision,\n            state=state,\n        )\n    )\n\n    assert result.allowed is False\n    assert (\n        result.code\n        == "NoPositiveHypothesisSupport"\n    )\n\n\ndef test_guard_rejects_conclusion_evidence_not_declared_as_positive_support():\n    state = InvestigationState(\n        scope=scope(),\n        evidence=[\n            trusted(\n                "e1",\n                InvestigationProbe.KUBERNETES_POD_STATE,\n            ),\n            trusted(\n                "e2",\n                InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,\n            ),\n        ],\n    )\n\n    decision = sufficient(\n        confidence=0.8,\n        hypothesis_value=hypothesis(\n            confidence=0.8,\n            supporting=[\n                "e1",\n            ],\n        ),\n        evidence_ids=[\n            "e1",\n            "e2",\n        ],\n    )\n\n    result = (\n        EpistemicConclusionGuard()\n        .evaluate(\n            decision=decision,\n            state=state,\n        )\n    )\n\n    assert result.allowed is False\n    assert (\n        result.code\n        == "ConclusionEvidenceNotPositiveSupport"\n    )\n\n\ndef test_guard_rejects_overstated_conclusion_confidence():\n    state = InvestigationState(\n        scope=scope(),\n        evidence=[\n            trusted(\n                "e1",\n                InvestigationProbe.KUBERNETES_POD_STATE,\n            ),\n        ],\n    )\n\n    decision = sufficient(\n        confidence=0.9,\n        hypothesis_value=hypothesis(\n            confidence=0.6,\n            supporting=[\n                "e1",\n            ],\n        ),\n        evidence_ids=[\n            "e1",\n        ],\n    )\n\n    result = (\n        EpistemicConclusionGuard()\n        .evaluate(\n            decision=decision,\n            state=state,\n        )\n    )\n\n    assert result.allowed is False\n    assert (\n        result.code\n        == "ConclusionConfidenceExceedsSupport"\n    )\n\n\ndef test_non_sufficient_terminal_decision_is_untouched():\n    state = InvestigationState(\n        scope=scope(),\n    )\n\n    decision = InvestigationDecision(\n        hypotheses=[\n            hypothesis(\n                confidence=0.2,\n                missing=[\n                    "application logs"\n                ],\n            )\n        ],\n        rationale_summary="insufficient evidence",\n        stop=True,\n        stop_reason=(\n            InvestigationStopReason.INSUFFICIENT_EVIDENCE\n        ),\n    )\n\n    result = (\n        EpistemicConclusionGuard()\n        .evaluate(\n            decision=decision,\n            state=state,\n        )\n    )\n\n    assert result.allowed is True\n\n\nclass ScriptedReasoner(\n    BaseInvestigationReasoner\n):\n    def __init__(\n        self,\n        decisions,\n    ):\n        self.decisions = list(\n            decisions\n        )\n\n    async def decide(\n        self,\n        scope_value,\n        state,\n    ):\n        return self.decisions.pop(\n            0\n        )\n\n\nclass ProbeExecutor:\n    async def collect(\n        self,\n        context,\n        scope_value,\n        probe,\n    ):\n        return trusted(\n            "e1",\n            probe,\n        )\n\n\ndef context():\n    return SimpleNamespace(\n        event=SimpleNamespace(\n            signal=SimpleNamespace(\n                name="PodOOMKilled",\n                message="pod restarted",\n            ),\n            resources=[\n                SimpleNamespace(\n                    name="payment-api",\n                    namespace="payment",\n                    cluster="benchmark-lab",\n                )\n            ],\n        ),\n        metadata={},\n        variables={},\n    )\n\n\n@pytest.mark.asyncio\nasync def test_coordinator_downgrades_unsupported_rca_to_safe_abstention():\n    reasoner = ScriptedReasoner(\n        [\n            InvestigationDecision(\n                hypotheses=[\n                    hypothesis(\n                        confidence=0.5,\n                        missing=[\n                            "pod state"\n                        ],\n                    )\n                ],\n                rationale_summary="collect pod state",\n                next_probe=(\n                    InvestigationProbe.KUBERNETES_POD_STATE\n                ),\n            ),\n            sufficient(\n                confidence=0.9,\n                hypothesis_value=hypothesis(\n                    confidence=0.1,\n                    conflicting=[\n                        "e1",\n                    ],\n                ),\n                evidence_ids=[\n                    "e1",\n                ],\n                root_cause=(\n                    "unsupported alternative causal claim"\n                ),\n            ),\n        ]\n    )\n\n    coordinator = (\n        EvidenceDrivenInvestigationCoordinator(\n            reasoner=reasoner,\n            probe_executor=ProbeExecutor(),\n            utc_clock=lambda: NOW,\n        )\n    )\n\n    state = await coordinator.investigate(\n        context()\n    )\n\n    assert state.status == (\n        InvestigationStatus.CONCLUDED\n    )\n\n    assert state.stop_reason == (\n        InvestigationStopReason.INSUFFICIENT_EVIDENCE\n    )\n\n    assert state.conclusion is None\n\n    assert state.epistemic_guard_code == (\n        "NoPositiveHypothesisSupport"\n    )\n\n\n@pytest.mark.asyncio\nasync def test_coordinator_preserves_valid_supported_rca():\n    reasoner = ScriptedReasoner(\n        [\n            InvestigationDecision(\n                hypotheses=[\n                    hypothesis(\n                        confidence=0.5,\n                        missing=[\n                            "pod state"\n                        ],\n                    )\n                ],\n                rationale_summary="collect pod state",\n                next_probe=(\n                    InvestigationProbe.KUBERNETES_POD_STATE\n                ),\n            ),\n            sufficient(\n                confidence=0.9,\n                hypothesis_value=hypothesis(\n                    confidence=0.9,\n                    supporting=[\n                        "e1",\n                    ],\n                ),\n                evidence_ids=[\n                    "e1",\n                ],\n            ),\n        ]\n    )\n\n    coordinator = (\n        EvidenceDrivenInvestigationCoordinator(\n            reasoner=reasoner,\n            probe_executor=ProbeExecutor(),\n            utc_clock=lambda: NOW,\n        )\n    )\n\n    state = await coordinator.investigate(\n        context()\n    )\n\n    assert state.status == (\n        InvestigationStatus.CONCLUDED\n    )\n\n    assert state.stop_reason == (\n        InvestigationStopReason.SUFFICIENT_EVIDENCE\n    )\n\n    assert state.conclusion is not None\n    assert state.epistemic_guard_code is None\n\n\ndef test_prompt_teaches_positive_support_and_negative_evidence_discipline():\n    state = InvestigationState(\n        scope=scope(),\n    )\n\n    prompt = (\n        LLMInvestigationReasoner\n        ._build_prompt(\n            scope=scope(),\n            state=state,\n        )\n    )\n\n    assert (\n        "Conflicting evidence can weaken a hypothesis"\n        in prompt\n    )\n\n    assert (\n        "Ruling out one hypothesis is not sufficient evidence"\n        in prompt\n    )\n\n    assert (\n        "Current-state evidence does not by itself prove"\n        in prompt\n    )\n\n    assert (\n        "positively establish a root cause"\n        in prompt\n    )\n\n\ndef test_benchmark_guard_rescue_is_visible_and_capped():\n    scenario = BenchmarkScenario(\n        key="guard-rescue",\n        title="guard rescue",\n        alert_name="PodOOMKilled",\n        alert_message="alert",\n        evidence_by_probe={},\n        hidden_expected_stop_reason=(\n            InvestigationStopReason.INSUFFICIENT_EVIDENCE\n        ),\n    )\n\n    state = InvestigationState(\n        status=(\n            InvestigationStatus.CONCLUDED\n        ),\n        scope=scope(),\n        stop_reason=(\n            InvestigationStopReason.INSUFFICIENT_EVIDENCE\n        ),\n        epistemic_guard_code=(\n            "NoPositiveHypothesisSupport"\n        ),\n    )\n\n    score = score_scenario(\n        scenario=scenario,\n        state=state,\n        decisions=[],\n    )\n\n    assert score.outcome_correct is True\n    assert score.guard_rescued is True\n    assert score.score == 85.0\n\n    report = build_report(\n        provider="unit",\n        mode="unit",\n        scenarios=[\n            score\n        ],\n    )\n\n    assert report.guard_rescue_count == 1\n    assert report.guard_rescue_rate == 100.0\n'
REASONER_OLD_BLOCK = '        trusted_evidence_ids = [\n            item.evidence_id\n            for item in state.evidence\n            if (\n                item.success\n                and item.trusted\n                and item.production_signal\n            )\n        ]\n\n        state_payload = {\n            "scope": scope.model_dump(\n                mode="json"\n            ),\n            "iteration_count": state.iteration_count,\n            "tool_call_count": state.tool_call_count,\n            "attempted_probes": [\n                probe.value\n                for probe in state.attempted_probes\n            ],\n            "hypotheses": [\n                item.model_dump(mode="json")\n                for item in state.hypotheses\n            ],\n            "evidence": [\n                item.model_dump(mode="json")\n                for item in state.evidence\n            ],\n            "trusted_evidence_ids": trusted_evidence_ids,\n            "allowed_probes": [\n                probe.value\n                for probe in InvestigationProbe\n            ],\n        }\n\n        return (\n            "Investigate the incident using the bounded state below.\\n"\n            "Return exactly one JSON object only. Do not return markdown.\\n"\n            "Maintain competing hypotheses and update confidence from evidence.\\n"\n            "Never repeat a probe already listed in attempted_probes.\\n"\n            "Never cite an evidence ID that is absent from State.evidence.\\n"\n            "For a conclusion, evidence_ids must be non-empty and copied "\n            "exactly from trusted_evidence_ids.\\n"\n            "Allowed model terminal stop_reason values are exactly: "\n            "sufficient_evidence, insufficient_evidence, no_safe_probe.\\n"\n            "Do not emit internal coordinator reasons such as timeout, "\n            "max_iterations, max_tool_calls, duplicate_probe or reasoner_error.\\n"\n            "\\n"\n            "Continuing shape:\\n"\n            "{\\n"\n            \'  "hypotheses": [{"hypothesis_id": "h1", "cause": "candidate", \'\n            \'"confidence": 0.5, "supporting_evidence_ids": [], \'\n            \'"conflicting_evidence_ids": [], "missing_evidence": ["needed evidence"]}],\\n\'\n            \'  "rationale_summary": "why this probe is most discriminative",\\n\'\n            \'  "stop": false,\\n\'\n            \'  "stop_reason": null,\\n\'\n            \'  "next_probe": "one unattempted value from allowed_probes",\\n\'\n            \'  "conclusion": null\\n\'\n            "}\\n"\n            "\\n"\n            "Terminal sufficient-evidence shape:\\n"\n            "{\\n"\n            \'  "hypotheses": [{"hypothesis_id": "h1", "cause": "supported cause", \'\n            \'"confidence": 0.9, "supporting_evidence_ids": ["copy exact trusted id"], \'\n            \'"conflicting_evidence_ids": [], "missing_evidence": []}],\\n\'\n            \'  "rationale_summary": "why trusted evidence is sufficient",\\n\'\n            \'  "stop": true,\\n\'\n            \'  "stop_reason": "sufficient_evidence",\\n\'\n            \'  "next_probe": null,\\n\'\n            \'  "conclusion": {"root_cause": "bounded root cause", \'\n            \'"confidence": 0.9, "evidence_ids": ["copy exact trusted id"], \'\n            \'"remaining_uncertainties": []}\\n\'\n            "}\\n"\n            "\\n"\n            "Terminal insufficient/no-safe-probe shape:\\n"\n            "{\\n"\n            \'  "hypotheses": [{"hypothesis_id": "h1", "cause": "candidate", \'\n            \'"confidence": 0.3, "supporting_evidence_ids": [], \'\n            \'"conflicting_evidence_ids": [], "missing_evidence": ["what is still missing"]}],\\n\'\n            \'  "rationale_summary": "why current bounded evidence cannot support an RCA",\\n\'\n            \'  "stop": true,\\n\'\n            \'  "stop_reason": "insufficient_evidence",\\n\'\n            \'  "next_probe": null,\\n\'\n            \'  "conclusion": null\\n\'\n            "}\\n"\n            "\\n"\n            "If no useful unattempted allowed probe can resolve the remaining "\n            "uncertainty, stop with insufficient_evidence or no_safe_probe.\\n"\n            "State:\\n"\n            f"{json.dumps(state_payload, ensure_ascii=True, sort_keys=True)}"\n        )\n'
REASONER_NEW_BLOCK = '        trusted_evidence_ids = [\n            item.evidence_id\n            for item in state.evidence\n            if (\n                item.success\n                and item.trusted\n                and item.production_signal\n            )\n        ]\n\n        state_payload = {\n            "scope": scope.model_dump(\n                mode="json"\n            ),\n            "iteration_count": state.iteration_count,\n            "tool_call_count": state.tool_call_count,\n            "attempted_probes": [\n                probe.value\n                for probe in state.attempted_probes\n            ],\n            "hypotheses": [\n                item.model_dump(mode="json")\n                for item in state.hypotheses\n            ],\n            "evidence": [\n                item.model_dump(mode="json")\n                for item in state.evidence\n            ],\n            "trusted_evidence_ids": trusted_evidence_ids,\n            "allowed_probes": [\n                probe.value\n                for probe in InvestigationProbe\n            ],\n        }\n\n        return (\n            "Investigate the incident using the bounded state below.\\n"\n            "Return exactly one JSON object only. Do not return markdown.\\n"\n            "Maintain competing hypotheses and update confidence from evidence.\\n"\n            "Never repeat a probe already listed in attempted_probes.\\n"\n            "Never cite an evidence ID that is absent from State.evidence.\\n"\n            "Conflicting evidence can weaken a hypothesis but cannot by itself "\n            "positively establish an unrelated root cause.\\n"\n            "Ruling out one hypothesis is not sufficient evidence for a different "\n            "causal claim.\\n"\n            "Current-state evidence does not by itself prove that a historical "\n            "event never occurred.\\n"\n            "For sufficient_evidence, the conclusion must affirm a positively "\n            "supported current hypothesis. Every conclusion.evidence_id must also "\n            "appear in that hypothesis supporting_evidence_ids and must not be "\n            "conflicting evidence for that same hypothesis.\\n"\n            "Conclusion confidence must not materially exceed the confidence of "\n            "the positively supported hypothesis.\\n"\n            "If the available evidence only rejects hypotheses and does not "\n            "positively establish a root cause, stop with insufficient_evidence.\\n"\n            "For a conclusion, evidence_ids must be non-empty and copied "\n            "exactly from trusted_evidence_ids.\\n"\n            "Allowed model terminal stop_reason values are exactly: "\n            "sufficient_evidence, insufficient_evidence, no_safe_probe.\\n"\n            "Do not emit internal coordinator reasons such as timeout, "\n            "max_iterations, max_tool_calls, duplicate_probe or reasoner_error.\\n"\n            "\\n"\n            "Continuing shape:\\n"\n            "{\\n"\n            \'  "hypotheses": [{"hypothesis_id": "h1", "cause": "candidate", \'\n            \'"confidence": 0.5, "supporting_evidence_ids": [], \'\n            \'"conflicting_evidence_ids": [], "missing_evidence": ["needed evidence"]}],\\n\'\n            \'  "rationale_summary": "why this probe is most discriminative",\\n\'\n            \'  "stop": false,\\n\'\n            \'  "stop_reason": null,\\n\'\n            \'  "next_probe": "one unattempted value from allowed_probes",\\n\'\n            \'  "conclusion": null\\n\'\n            "}\\n"\n            "\\n"\n            "Terminal sufficient-evidence shape:\\n"\n            "{\\n"\n            \'  "hypotheses": [{"hypothesis_id": "h1", "cause": "supported cause", \'\n            \'"confidence": 0.9, "supporting_evidence_ids": ["copy exact trusted id"], \'\n            \'"conflicting_evidence_ids": [], "missing_evidence": []}],\\n\'\n            \'  "rationale_summary": "why trusted evidence is sufficient",\\n\'\n            \'  "stop": true,\\n\'\n            \'  "stop_reason": "sufficient_evidence",\\n\'\n            \'  "next_probe": null,\\n\'\n            \'  "conclusion": {"root_cause": "bounded root cause", \'\n            \'"confidence": 0.9, "evidence_ids": ["copy exact trusted id"], \'\n            \'"remaining_uncertainties": []}\\n\'\n            "}\\n"\n            "\\n"\n            "Terminal insufficient/no-safe-probe shape:\\n"\n            "{\\n"\n            \'  "hypotheses": [{"hypothesis_id": "h1", "cause": "candidate", \'\n            \'"confidence": 0.3, "supporting_evidence_ids": [], \'\n            \'"conflicting_evidence_ids": [], "missing_evidence": ["what is still missing"]}],\\n\'\n            \'  "rationale_summary": "why current bounded evidence cannot support an RCA",\\n\'\n            \'  "stop": true,\\n\'\n            \'  "stop_reason": "insufficient_evidence",\\n\'\n            \'  "next_probe": null,\\n\'\n            \'  "conclusion": null\\n\'\n            "}\\n"\n            "\\n"\n            "If no useful unattempted allowed probe can resolve the remaining "\n            "uncertainty, stop with insufficient_evidence or no_safe_probe.\\n"\n            "State:\\n"\n            f"{json.dumps(state_payload, ensure_ascii=True, sort_keys=True)}"\n        )\n'


@dataclass(frozen=True)
class CommandResult:
    name: str
    command: list[str]
    returncode: int
    stdout: str
    stderr: str


def find_repo_root(
    start: Path,
) -> Path:
    for candidate in (
        start,
        *start.parents,
    ):
        if (
            (candidate / "pyproject.toml").exists()
            and (candidate / "services").exists()
            and (candidate / "packages").exists()
        ):
            return candidate

    raise RuntimeError(
        "Repository root not found. "
        "Run from inside ai-reliability-platform."
    )


def read_text(
    path: Path,
) -> str:
    return (
        path.read_text(
            encoding="utf-8-sig",
            errors="strict",
        )
        .replace(
            "\r\n",
            "\n",
        )
        .replace(
            "\r",
            "\n",
        )
    )


def write_text(
    path: Path,
    text: str,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    path.write_text(
        text.replace(
            "\r\n",
            "\n",
        ).replace(
            "\r",
            "\n",
        ),
        encoding="utf-8",
        newline="\n",
    )


def backup_file(
    path: Path,
) -> Path:
    stamp = (
        datetime.now()
        .strftime(
            "%Y%m%d_%H%M%S"
        )
    )

    backup = path.with_name(
        f"{path.name}.before_{VERSION}_{stamp}.bak"
    )

    shutil.copy2(
        path,
        backup,
    )

    return backup


def run_command(
    *,
    root: Path,
    name: str,
    command: list[str],
) -> CommandResult:
    process = subprocess.run(
        command,
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    return CommandResult(
        name=name,
        command=command,
        returncode=process.returncode,
        stdout=process.stdout,
        stderr=process.stderr,
    )


def section(
    lines: list[str],
    title: str,
) -> None:
    lines.extend(
        [
            "",
            "=" * 120,
            title,
            "=" * 120,
            "",
        ]
    )


def add_command(
    lines: list[str],
    result: CommandResult,
) -> None:
    section(
        lines,
        f"COMMAND: {result.name}",
    )

    lines.extend(
        [
            " ".join(
                result.command
            ),
            "",
            f"ExitCode: {result.returncode}",
            "",
            "STDOUT",
            "-" * 120,
            result.stdout.rstrip()
            or "<EMPTY>",
            "",
            "STDERR",
            "-" * 120,
            result.stderr.rstrip()
            or "<EMPTY>",
        ]
    )


def find_top_level_class(
    tree: ast.Module,
    name: str,
) -> ast.ClassDef:
    matches = [
        node
        for node in tree.body
        if (
            isinstance(
                node,
                ast.ClassDef,
            )
            and node.name == name
        )
    ]

    if len(
        matches
    ) != 1:
        raise RuntimeError(
            f"Expected exactly one top-level class {name}, "
            f"found {len(matches)}"
        )

    return matches[0]


def class_field_names(
    class_node: ast.ClassDef,
) -> set[str]:
    fields: set[str] = set()

    for node in class_node.body:
        if isinstance(
            node,
            ast.AnnAssign,
        ):
            target = node.target

            if isinstance(
                target,
                ast.Name,
            ):
                fields.add(
                    target.id
                )

    return fields


def method_names(
    class_node: ast.ClassDef,
) -> set[str]:
    return {
        node.name
        for node in class_node.body
        if isinstance(
            node,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
            ),
        )
    }


def validate_models_structure(
    text: str,
) -> dict[str, str]:
    tree = ast.parse(
        text
    )

    state = find_top_level_class(
        tree,
        "InvestigationState",
    )

    fields = class_field_names(
        state
    )

    required = {
        "status",
        "scope",
        "limits",
        "hypotheses",
        "evidence",
        "attempted_probes",
        "decision_summaries",
        "stop_reason",
        "failure_code",
        "conclusion",
    }

    missing = sorted(
        required - fields
    )

    if missing:
        raise RuntimeError(
            "models.py InvestigationState structure changed; "
            "missing fields: "
            + ", ".join(
                missing
            )
        )

    if (
        "epistemic_guard_code"
        in fields
    ):
        raise RuntimeError(
            "models.py already contains epistemic_guard_code; "
            "refusing duplicate installation"
        )

    return {
        "class": "InvestigationState",
        "field_count": str(
            len(
                fields
            )
        ),
        "target_anchor": (
            "failure_code -> conclusion"
        ),
    }


def patch_models(
    path: Path,
) -> dict[str, str]:
    text = read_text(
        path
    )

    structure = (
        validate_models_structure(
            text
        )
    )

    anchor = (
        "    failure_code: ShortText | None = None\n"
        "    conclusion: InvestigationConclusion | None = None\n"
    )

    count = text.count(
        anchor
    )

    if count != 1:
        raise RuntimeError(
            "models.py target field anchor is not unique; "
            f"found {count}"
        )

    replacement = (
        "    failure_code: ShortText | None = None\n"
        "    epistemic_guard_code: ShortText | None = None\n"
        "    conclusion: InvestigationConclusion | None = None\n"
    )

    updated = text.replace(
        anchor,
        replacement,
        1,
    )

    ast.parse(
        updated
    )

    write_text(
        path,
        updated,
    )

    return structure


def validate_coordinator_structure(
    text: str,
) -> dict[str, str]:
    tree = ast.parse(
        text
    )

    coordinator = (
        find_top_level_class(
            tree,
            "EvidenceDrivenInvestigationCoordinator",
        )
    )

    methods = method_names(
        coordinator
    )

    required_methods = {
        "__init__",
        "investigate",
        "_stop",
        "_publish_shadow_snapshot",
        "_evidence_references_are_valid",
    }

    missing = sorted(
        required_methods
        - methods
    )

    if missing:
        raise RuntimeError(
            "coordinator.py structure changed; "
            "missing methods: "
            + ", ".join(
                missing
            )
        )

    if (
        "EpistemicConclusionGuard"
        in text
        or "epistemic_guard_code"
        in text
    ):
        raise RuntimeError(
            "coordinator.py already appears to contain Epistemic Guard wiring"
        )

    return {
        "class": (
            "EvidenceDrivenInvestigationCoordinator"
        ),
        "method_count": str(
            len(
                methods
            )
        ),
        "terminal_anchor": (
            "decision.stop -> _stop -> conclusion"
        ),
    }


def patch_coordinator(
    path: Path,
) -> dict[str, str]:
    text = read_text(
        path
    )

    structure = (
        validate_coordinator_structure(
            text
        )
    )

    import_anchor = (
        "from services.agent_runtime.app.investigation.models import (\n"
    )

    import_count = text.count(
        import_anchor
    )

    if import_count != 1:
        raise RuntimeError(
            "coordinator.py models import anchor is not unique; "
            f"found {import_count}"
        )

    import_block = (
        "from services.agent_runtime.app.investigation.epistemic_guard "
        "import (\n"
        "    EpistemicConclusionGuard,\n"
        ")\n"
    )

    text = text.replace(
        import_anchor,
        (
            import_block
            + import_anchor
        ),
        1,
    )

    terminal_anchor = (
        "            if decision.stop:\n"
        "                self._stop(\n"
        "                    state,\n"
        "                    status=InvestigationStatus.CONCLUDED,\n"
        "                    reason=decision.stop_reason,\n"
        "                )\n"
        "                state.conclusion = decision.conclusion\n"
        "                break\n"
    )

    terminal_count = text.count(
        terminal_anchor
    )

    if terminal_count != 1:
        raise RuntimeError(
            "coordinator.py terminal decision anchor is not unique; "
            f"found {terminal_count}"
        )

    terminal_replacement = (
        "            if decision.stop:\n"
        "                guard_result = (\n"
        "                    EpistemicConclusionGuard()\n"
        "                    .evaluate(\n"
        "                        decision=decision,\n"
        "                        state=state,\n"
        "                    )\n"
        "                )\n"
        "\n"
        "                if not guard_result.allowed:\n"
        "                    state.epistemic_guard_code = (\n"
        "                        guard_result.code\n"
        "                    )\n"
        "\n"
        "                    self._stop(\n"
        "                        state,\n"
        "                        status=InvestigationStatus.CONCLUDED,\n"
        "                        reason=(\n"
        "                            InvestigationStopReason\n"
        "                            .INSUFFICIENT_EVIDENCE\n"
        "                        ),\n"
        "                    )\n"
        "\n"
        "                    state.conclusion = None\n"
        "                    break\n"
        "\n"
        "                self._stop(\n"
        "                    state,\n"
        "                    status=InvestigationStatus.CONCLUDED,\n"
        "                    reason=decision.stop_reason,\n"
        "                )\n"
        "                state.conclusion = decision.conclusion\n"
        "                break\n"
    )

    updated = text.replace(
        terminal_anchor,
        terminal_replacement,
        1,
    )

    ast.parse(
        updated
    )

    write_text(
        path,
        updated,
    )

    return structure


def patch_reasoner(
    path: Path,
) -> dict[str, str]:
    text = read_text(
        path
    )

    ast.parse(
        text
    )

    if (
        "Conflicting evidence can weaken a hypothesis"
        in text
    ):
        raise RuntimeError(
            "reasoner.py already appears to contain Epistemic prompt rules"
        )

    count = text.count(
        REASONER_OLD_BLOCK
    )

    if count != 1:
        raise RuntimeError(
            "reasoner.py terminal prompt anchor is not unique/current; "
            f"found {count}"
        )

    updated = text.replace(
        REASONER_OLD_BLOCK,
        REASONER_NEW_BLOCK,
        1,
    )

    ast.parse(
        updated
    )

    write_text(
        path,
        updated,
    )

    return {
        "terminal_prompt_anchor_count": str(
            count
        ),
        "terminal_contract_present": str(
            "Terminal sufficient-evidence shape"
            in text
        ),
    }


def main() -> int:
    root = find_repo_root(
        Path.cwd().resolve()
    )

    after = (
        root
        / AFTER_NAME
    )

    error = (
        root
        / ERROR_NAME
    )

    for output in (
        after,
        error,
    ):
        try:
            output.unlink()
        except FileNotFoundError:
            pass

    investigation_dir = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "investigation"
    )

    reasoner_file = (
        investigation_dir
        / "reasoner.py"
    )

    models_file = (
        investigation_dir
        / "models.py"
    )

    coordinator_file = (
        investigation_dir
        / "coordinator.py"
    )

    guard_file = (
        investigation_dir
        / "epistemic_guard.py"
    )

    engine_file = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "evaluation"
        / "intelligence_benchmark"
        / "engine.py"
    )

    guard_test_file = (
        root
        / "services"
        / "agent_runtime"
        / "tests"
        / "test_investigation_epistemic_guard.py"
    )

    required = (
        reasoner_file,
        models_file,
        coordinator_file,
        engine_file,
    )

    for path in required:
        if not path.exists():
            raise RuntimeError(
                f"Required file is missing: {path}"
            )

    targets = (
        reasoner_file,
        models_file,
        coordinator_file,
        guard_file,
        engine_file,
        guard_test_file,
    )

    preexisting = {
        path: path.exists()
        for path in targets
    }

    backups = []

    report = [
        "Investigation Epistemic Guard v2",
        f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
        "",
        "Why v2:",
        "- v1 used byte-for-byte full-file equality for models.py/coordinator.py",
        "- harmless formatting/unrelated-file differences could be treated as stale",
        "- v2 reads and AST-parses the entire current file first",
        "- v2 validates required class/field/method structure",
        "- v2 patches only unique target anchors",
        "- real structural drift still fails closed",
        "",
        "Guard semantics are unchanged from v1:",
        "- sufficient_evidence needs positive hypothesis support",
        "- conclusion evidence must be positive support for one hypothesis",
        "- conflicting-only evidence cannot establish the conclusion",
        "- conclusion confidence cannot materially exceed hypothesis confidence",
        "- inadmissible RCA is safely downgraded to insufficient_evidence",
        "",
        "No network request is sent by this installer.",
    ]

    try:
        section(
            report,
            "BACKUP",
        )

        for path in targets:
            if path.exists():
                backup = backup_file(
                    path
                )

                backups.append(
                    (
                        path,
                        backup,
                    )
                )

                report.append(
                    "backup="
                    + str(
                        backup.relative_to(
                            root
                        )
                    )
                )

        section(
            report,
            "FULL-FILE STRUCTURAL PREFLIGHT",
        )

        # Read and parse the entire current files before any mutation.
        models_preflight = (
            validate_models_structure(
                read_text(
                    models_file
                )
            )
        )

        coordinator_preflight = (
            validate_coordinator_structure(
                read_text(
                    coordinator_file
                )
            )
        )

        reasoner_text = read_text(
            reasoner_file
        )

        ast.parse(
            reasoner_text
        )

        report.extend(
            [
                "models.py=" + str(
                    models_preflight
                ),
                "coordinator.py=" + str(
                    coordinator_preflight
                ),
                (
                    "reasoner.py_terminal_anchor_count="
                    + str(
                        reasoner_text.count(
                            REASONER_OLD_BLOCK
                        )
                    )
                ),
            ]
        )

        if (
            reasoner_text.count(
                REASONER_OLD_BLOCK
            )
            != 1
        ):
            raise RuntimeError(
                "reasoner.py does not match the verified terminal-contract structure"
            )

        # Only mutate after every current full-file preflight passes.
        write_text(
            guard_file,
            GUARD_SOURCE,
        )

        patch_models(
            models_file
        )

        patch_coordinator(
            coordinator_file
        )

        patch_reasoner(
            reasoner_file
        )

        write_text(
            engine_file,
            ENGINE_SOURCE,
        )

        write_text(
            guard_test_file,
            GUARD_TEST_SOURCE,
        )

        syntax = run_command(
            root=root,
            name="Python syntax",
            command=[
                "uv",
                "run",
                "python",
                "-m",
                "py_compile",
                str(
                    guard_file.relative_to(
                        root
                    )
                ),
                str(
                    models_file.relative_to(
                        root
                    )
                ),
                str(
                    coordinator_file.relative_to(
                        root
                    )
                ),
                str(
                    reasoner_file.relative_to(
                        root
                    )
                ),
                str(
                    engine_file.relative_to(
                        root
                    )
                ),
                str(
                    guard_test_file.relative_to(
                        root
                    )
                ),
            ],
        )

        add_command(
            report,
            syntax,
        )

        if syntax.returncode != 0:
            raise RuntimeError(
                "Python syntax verification failed"
            )

        tests = run_command(
            root=root,
            name="Epistemic Guard focused regression tests",
            command=[
                "uv",
                "run",
                "pytest",
                str(
                    guard_test_file.relative_to(
                        root
                    )
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_terminal_contract.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_intelligence_benchmark.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_coordinator.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_reasoner.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_models.py"
                ),
                "-q",
            ],
        )

        add_command(
            report,
            tests,
        )

        if tests.returncode != 0:
            raise RuntimeError(
                "Epistemic Guard regression tests failed"
            )

        prompt = run_command(
            root=root,
            name="Epistemic prompt preflight",
            command=[
                "uv",
                "run",
                "python",
                "-c",
                (
                    "from services.agent_runtime.app.investigation.reasoner "
                    "import LLMInvestigationReasoner; "
                    "from services.agent_runtime.app.investigation.models "
                    "import InvestigationScope,InvestigationState; "
                    "s=InvestigationScope(alert_name='A',resource='r'); "
                    "p=LLMInvestigationReasoner._build_prompt("
                    "scope=s,state=InvestigationState(scope=s)); "
                    "print('positive_support=' + "
                    "str('positively supported current hypothesis' in p)); "
                    "print('negative_evidence_rule=' + "
                    "str('Ruling out one hypothesis is not sufficient evidence' in p)); "
                    "print('historical_nonoccurrence_rule=' + "
                    "str('Current-state evidence does not by itself prove' in p))"
                ),
            ],
        )

        add_command(
            report,
            prompt,
        )

        if prompt.returncode != 0:
            raise RuntimeError(
                "Epistemic prompt preflight failed"
            )

        authority = run_command(
            root=root,
            name="Guard authority boundary",
            command=[
                "uv",
                "run",
                "python",
                "-c",
                (
                    "from pathlib import Path; "
                    "s=Path(r'services/agent_runtime/app/investigation/"
                    "epistemic_guard.py').read_text(encoding='utf-8'); "
                    "bad=[x for x in ['ActionRuntime','ApprovalService',"
                    "'VerificationRuntime','create_llm_gateway'] "
                    "if x in s]; "
                    "print('forbidden_matches='+str(bad)); "
                    "raise SystemExit(1 if bad else 0)"
                ),
            ],
        )

        add_command(
            report,
            authority,
        )

        if authority.returncode != 0:
            raise RuntimeError(
                "Epistemic Guard authority boundary failed"
            )

        status = run_command(
            root=root,
            name="Git status",
            command=[
                "git",
                "status",
                "--short",
                "--",
                str(
                    reasoner_file.relative_to(
                        root
                    )
                ),
                str(
                    models_file.relative_to(
                        root
                    )
                ),
                str(
                    coordinator_file.relative_to(
                        root
                    )
                ),
                str(
                    guard_file.relative_to(
                        root
                    )
                ),
                str(
                    engine_file.relative_to(
                        root
                    )
                ),
                str(
                    guard_test_file.relative_to(
                        root
                    )
                ),
            ],
        )

        add_command(
            report,
            status,
        )

        section(
            report,
            "RESULT",
        )

        report.extend(
            [
                "PASSED",
                "",
                "Agent autonomy preserved:",
                "- hypotheses remain model-owned",
                "- confidence remains model-owned",
                "- Probe choice remains model-owned",
                "- stop/RCA remain model proposals",
                "",
                "Guard authority remains narrow:",
                "- validates positive-support discipline only",
                "- may safely downgrade unsupported RCA to insufficient_evidence",
                "- cannot create a different RCA",
                "- cannot call LLM/Action/Approval/Verification",
                "",
                "Next:",
                "rerun the same three Bailian smoke benchmark scenarios.",
            ]
        )

        write_text(
            after,
            "\n".join(
                report
            )
            + "\n",
        )

        print("=" * 72)
        print(
            "INVESTIGATION EPISTEMIC GUARD V2 PASSED"
        )
        print("=" * 72)
        print("")
        print(
            "No LLM/Kubernetes/Prometheus request was sent."
        )
        print("")
        print("Upload:")
        print(after)

        return 0

    except Exception as exc:
        rollback = []

        for original, backup in reversed(
            backups
        ):
            try:
                shutil.copy2(
                    backup,
                    original,
                )

                rollback.append(
                    "RESTORED "
                    + str(
                        original.relative_to(
                            root
                        )
                    )
                )

            except Exception as rollback_exc:
                rollback.append(
                    "ROLLBACK FAILED "
                    + str(
                        original.relative_to(
                            root
                        )
                    )
                    + ": "
                    + f"{type(rollback_exc).__name__}: {rollback_exc}"
                )

        for path in targets:
            if (
                not preexisting[
                    path
                ]
                and path.exists()
            ):
                try:
                    path.unlink()

                    rollback.append(
                        "REMOVED newly-created "
                        + str(
                            path.relative_to(
                                root
                            )
                        )
                    )

                except Exception as rollback_exc:
                    rollback.append(
                        "ROLLBACK FAILED removing "
                        + str(
                            path.relative_to(
                                root
                            )
                        )
                        + ": "
                        + f"{type(rollback_exc).__name__}: {rollback_exc}"
                    )

        write_text(
            error,
            "\n".join(
                [
                    "Investigation Epistemic Guard v2 FAILED",
                    f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
                    "",
                    "Exception:",
                    f"{type(exc).__name__}: {exc}",
                    "",
                    "Traceback:",
                    traceback.format_exc(),
                    "",
                    "ROLLBACK",
                    "=" * 120,
                    *rollback,
                    "",
                    "PARTIAL REPORT",
                    "=" * 120,
                    *report,
                ]
            )
            + "\n",
        )

        print("=" * 72)
        print(
            "INVESTIGATION EPISTEMIC GUARD V2 FAILED"
        )
        print("=" * 72)
        print("")
        print(
            "Modified files were rolled back where possible."
        )
        print("")
        print("Upload:")
        print(error)

        return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
