from __future__ import annotations

import hashlib
import shutil
import subprocess
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


VERSION = "investigation-benchmark-abstention-semantics-v1"

AFTER_NAME = (
    "investigation_benchmark_abstention_semantics_v1_after.txt"
)

ERROR_NAME = (
    "investigation_benchmark_abstention_semantics_v1_error.txt"
)

EXPECTED_HASHES = {'services/agent_runtime/app/evaluation/intelligence_benchmark/engine.py': 'b5e90cf47318ba6e2c002c50d27a86ea73c7d00143729ad26341d8e9ff83c26b', 'services/agent_runtime/app/evaluation/intelligence_benchmark/scenarios.py': 'a6d66c38216fb7ebca0756a9abb5ba59d617b92413033e55f48c2b5b310319d3'}

ENGINE_SOURCE = 'from __future__ import annotations\n\nimport json\nfrom dataclasses import dataclass\nfrom datetime import UTC, datetime\nfrom types import SimpleNamespace\nfrom typing import Any\n\nfrom pydantic import BaseModel, ConfigDict, Field\n\nfrom services.agent_runtime.app.evaluation.real_incident.llm_run import (\n    create_historical_llm_runtime,\n)\nfrom services.agent_runtime.app.investigation.coordinator import (\n    EvidenceDrivenInvestigationCoordinator,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    EvidenceItem,\n    InvestigationDecision,\n    InvestigationLimits,\n    InvestigationProbe,\n    InvestigationState,\n    InvestigationStopReason,\n)\nfrom services.agent_runtime.app.investigation.reasoner import (\n    BaseInvestigationReasoner,\n)\n\n\nclass IntelligenceBenchmarkError(\n    RuntimeError\n):\n    pass\n\n\nclass _BenchmarkMonotonicClock:\n    """\n    Deterministic logical clock for Intelligence Benchmark control limits.\n\n    Real provider/network latency must not decide whether an intelligence\n    scenario reaches its terminal reasoning step. asyncio.wait_for still keeps\n    the coordinator\'s per-call timeout protection, while cumulative benchmark\n    elapsed time advances only by a tiny logical step per control check.\n    """\n\n    def __init__(\n        self,\n        *,\n        step_seconds: float = 0.001,\n    ) -> None:\n        self._value = 0.0\n        self._step_seconds = (\n            step_seconds\n        )\n\n    def __call__(\n        self,\n    ) -> float:\n        current = self._value\n        self._value += (\n            self._step_seconds\n        )\n        return current\n\n\nclass BenchmarkScenario(BaseModel):\n    """\n    One hidden-label Investigation exam.\n\n    hidden_* fields are evaluator-only. They never enter the Agent context,\n    InvestigationScope, LLM prompt, EvidenceItem facts or decision history.\n    """\n\n    model_config = ConfigDict(\n        frozen=True,\n        extra="forbid",\n    )\n\n    key: str\n    title: str\n\n    alert_name: str\n    alert_message: str\n\n    resource: str = "payment-api"\n    namespace: str = "payment"\n    cluster: str = "benchmark-lab"\n\n    evidence_by_probe: dict[\n        InvestigationProbe,\n        dict[str, Any] | str,\n    ]\n\n    hidden_expected_stop_reason: (\n        InvestigationStopReason\n    )\n\n    hidden_acceptable_stop_reasons: list[\n        InvestigationStopReason\n    ] = Field(\n        default_factory=list\n    )\n\n    hidden_required_probes: list[\n        InvestigationProbe\n    ] = Field(\n        default_factory=list\n    )\n\n    hidden_preferred_first_probes: list[\n        InvestigationProbe\n    ] = Field(\n        default_factory=list\n    )\n\n    hidden_root_cause_keyword_groups: list[\n        list[str]\n    ] = Field(\n        default_factory=list\n    )\n\n    hidden_missing_capability_keywords: list[\n        str\n    ] = Field(\n        default_factory=list\n    )\n\n    hidden_max_reasonable_tool_calls: int = Field(\n        default=4,\n        ge=0,\n        le=10,\n    )\n\n\nclass ScenarioScore(BaseModel):\n    model_config = ConfigDict(\n        frozen=True,\n        extra="forbid",\n    )\n\n    scenario_key: str\n    title: str\n\n    score: float = Field(\n        ge=0.0,\n        le=100.0,\n    )\n\n    expected_stop_reason: str\n    outcome_correct: bool\n    grounding_correct: bool\n    required_probe_coverage: float\n    first_probe_quality: bool | None\n    tool_efficiency: float\n    root_cause_or_abstention_correct: bool\n    missing_capability_awareness: bool | None\n\n    final_status: str\n    final_stop_reason: str | None\n    failure_code: str | None\n    epistemic_guard_code: str | None\n    guard_rescued: bool\n\n    attempted_probes: list[str]\n    tool_call_count: int\n    iteration_count: int\n\n    conclusion_root_cause: str | None\n    conclusion_confidence: float | None\n\n    decision_trace: list[\n        dict[str, Any]\n    ]\n\n    notes: list[str] = Field(\n        default_factory=list\n    )\n\n\nclass IntelligenceBenchmarkReport(BaseModel):\n    model_config = ConfigDict(\n        frozen=True,\n        extra="forbid",\n    )\n\n    schema_version: str = "v1"\n    generated_at: datetime\n\n    provider: str\n    mode: str\n\n    scenario_count: int\n    overall_score: float = Field(\n        ge=0.0,\n        le=100.0,\n    )\n\n    outcome_accuracy: float = Field(\n        ge=0.0,\n        le=100.0,\n    )\n\n    abstention_accuracy: float = Field(\n        ge=0.0,\n        le=100.0,\n    )\n\n    sufficient_evidence_accuracy: float = Field(\n        ge=0.0,\n        le=100.0,\n    )\n\n    average_tool_calls: float = Field(\n        ge=0.0,\n    )\n\n    guard_rescue_count: int = Field(\n        ge=0,\n    )\n\n    guard_rescue_rate: float = Field(\n        ge=0.0,\n        le=100.0,\n    )\n\n    scenarios: list[\n        ScenarioScore\n    ]\n\n    strongest_signals: list[str]\n    weakest_signals: list[str]\n\n\nclass BenchmarkProbeExecutor:\n    """\n    Synthetic evidence backend for model-intelligence evaluation.\n\n    The model sees only the evidence corresponding to probes it chose.\n    Hidden labels remain inside BenchmarkScenario and never cross this class\n    into EvidenceItem.\n    """\n\n    def __init__(\n        self,\n        scenario: BenchmarkScenario,\n        *,\n        observed_at: datetime,\n    ) -> None:\n        self.scenario = scenario\n        self.observed_at = observed_at\n        self.calls: list[\n            InvestigationProbe\n        ] = []\n\n    async def collect(\n        self,\n        context,\n        scope,\n        probe,\n    ) -> EvidenceItem:\n        self.calls.append(\n            probe\n        )\n\n        value = (\n            self.scenario\n            .evidence_by_probe\n            .get(\n                probe\n            )\n        )\n\n        if isinstance(\n            value,\n            str,\n        ):\n            raise RuntimeError(\n                "Benchmark probe unavailable"\n            )\n\n        if value is None:\n            raise RuntimeError(\n                "Benchmark probe has no observation"\n            )\n\n        source = (\n            "kubernetes"\n            if probe\n            in {\n                InvestigationProbe.KUBERNETES_POD_STATE,\n                (\n                    InvestigationProbe\n                    .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n                ),\n            }\n            else "prometheus"\n        )\n\n        return EvidenceItem(\n            evidence_id=(\n                f"{self.scenario.key}:"\n                f"{probe.value}"\n            ),\n            probe=probe,\n            source=source,\n            success=True,\n            trusted=True,\n            production_signal=True,\n            reliability=1.0,\n            observed_at=self.observed_at,\n            facts=dict(\n                value\n            ),\n        )\n\n\nclass TracingReasoner(\n    BaseInvestigationReasoner\n):\n    """\n    Transparent delegate that records the actual Agent decisions.\n\n    It does not modify prompts, decisions, state or provider behavior.\n    """\n\n    def __init__(\n        self,\n        delegate: BaseInvestigationReasoner,\n    ) -> None:\n        if not isinstance(\n            delegate,\n            BaseInvestigationReasoner,\n        ):\n            raise TypeError(\n                "Benchmark delegate reasoner is invalid"\n            )\n\n        self.delegate = delegate\n\n        self.decisions: list[\n            InvestigationDecision\n        ] = []\n\n        self.states: list[\n            InvestigationState\n        ] = []\n\n    async def decide(\n        self,\n        scope,\n        state,\n    ) -> InvestigationDecision:\n        self.states.append(\n            state.model_copy(\n                deep=True\n            )\n        )\n\n        decision = await (\n            self.delegate.decide(\n                scope,\n                state,\n            )\n        )\n\n        self.decisions.append(\n            decision.model_copy(\n                deep=True\n            )\n        )\n\n        return decision\n\n\ndef _context(\n    scenario: BenchmarkScenario,\n):\n    return SimpleNamespace(\n        event=SimpleNamespace(\n            signal=SimpleNamespace(\n                name=scenario.alert_name,\n                message=(\n                    scenario.alert_message\n                ),\n            ),\n            resources=[\n                SimpleNamespace(\n                    name=scenario.resource,\n                    namespace=scenario.namespace,\n                    cluster=scenario.cluster,\n                )\n            ],\n        ),\n        metadata={},\n        variables={},\n    )\n\n\ndef _normalize_text(\n    value: str | None,\n) -> str:\n    if not value:\n        return ""\n\n    return (\n        value\n        .strip()\n        .lower()\n    )\n\n\ndef _missing_capability_text(\n    decisions: list[\n        InvestigationDecision\n    ],\n) -> str:\n    """\n    Return only explicit unresolved-evidence language.\n\n    Hypothesis causes, rationale prose and conclusion root-cause text are\n    intentionally excluded. Guessing "application panic" is not the same as\n    recognizing that application/container logs are missing.\n    """\n\n    fragments: list[\n        str\n    ] = []\n\n    for decision in decisions:\n        for hypothesis in decision.hypotheses:\n            fragments.extend(\n                hypothesis.missing_evidence\n            )\n\n        if decision.conclusion is not None:\n            fragments.extend(\n                decision.conclusion.remaining_uncertainties\n            )\n\n    return _normalize_text(\n        "\\n".join(\n            fragments\n        )\n    )\n\n\ndef _keyword_groups_match(\n    text: str,\n    groups: list[\n        list[str]\n    ],\n) -> bool:\n    normalized = _normalize_text(\n        text\n    )\n\n    if not groups:\n        return True\n\n    for group in groups:\n        if not any(\n            _normalize_text(\n                token\n            )\n            in normalized\n            for token in group\n        ):\n            return False\n\n    return True\n\n\ndef _decision_trace(\n    decisions: list[\n        InvestigationDecision\n    ],\n) -> list[\n    dict[str, Any]\n]:\n    trace = []\n\n    for index, decision in enumerate(\n        decisions,\n        start=1,\n    ):\n        trace.append(\n            {\n                "iteration": index,\n                "hypotheses": [\n                    {\n                        "hypothesis_id": (\n                            item.hypothesis_id\n                        ),\n                        "cause": item.cause,\n                        "confidence": (\n                            item.confidence\n                        ),\n                        "supporting_evidence_ids": list(\n                            item.supporting_evidence_ids\n                        ),\n                        "conflicting_evidence_ids": list(\n                            item.conflicting_evidence_ids\n                        ),\n                        "missing_evidence": list(\n                            item.missing_evidence\n                        ),\n                        "optional_evidence": list(\n                            item.optional_evidence\n                        ),\n                    }\n                    for item in decision.hypotheses\n                ],\n                "rationale_summary": (\n                    decision.rationale_summary\n                ),\n                "stop": decision.stop,\n                "stop_reason": (\n                    decision.stop_reason.value\n                    if decision.stop_reason\n                    is not None\n                    else None\n                ),\n                "next_probe": (\n                    decision.next_probe.value\n                    if decision.next_probe\n                    is not None\n                    else None\n                ),\n                "conclusion": (\n                    decision.conclusion.model_dump(\n                        mode="json"\n                    )\n                    if decision.conclusion\n                    is not None\n                    else None\n                ),\n            }\n        )\n\n    return trace\n\n\ndef score_scenario(\n    *,\n    scenario: BenchmarkScenario,\n    state: InvestigationState,\n    decisions: list[\n        InvestigationDecision\n    ],\n) -> ScenarioScore:\n    attempted = list(state.attempted_probes)\n    expected_stop = scenario.hidden_expected_stop_reason\n\n    accepted_stop_reasons = {\n        expected_stop,\n        *scenario.hidden_acceptable_stop_reasons,\n    }\n\n    legitimate_terminal = (\n        state.status.value == "concluded"\n        and state.stop_reason in accepted_stop_reasons\n    )\n    outcome_correct = legitimate_terminal\n\n    if expected_stop == InvestigationStopReason.SUFFICIENT_EVIDENCE:\n        if not legitimate_terminal or state.conclusion is None:\n            grounding_correct = False\n        else:\n            trusted_ids = {\n                item.evidence_id\n                for item in state.evidence\n                if (\n                    item.success\n                    and item.trusted\n                    and item.production_signal\n                )\n            }\n            conclusion_ids = set(state.conclusion.evidence_ids)\n            grounding_correct = (\n                bool(conclusion_ids)\n                and conclusion_ids.issubset(trusted_ids)\n            )\n    else:\n        grounding_correct = (\n            legitimate_terminal\n            and state.conclusion is None\n        )\n\n    required = set(scenario.hidden_required_probes)\n    attempted_set = set(attempted)\n    required_probe_coverage = (\n        len(required & attempted_set) / len(required)\n        if required\n        else 1.0\n    )\n\n    if scenario.hidden_preferred_first_probes:\n        first_probe_quality = (\n            bool(attempted)\n            and attempted[0]\n            in scenario.hidden_preferred_first_probes\n        )\n    else:\n        first_probe_quality = None\n\n    max_calls = scenario.hidden_max_reasonable_tool_calls\n    if max_calls <= 0:\n        tool_efficiency = 1.0 if state.tool_call_count == 0 else 0.0\n    elif state.tool_call_count <= max_calls:\n        tool_efficiency = 1.0\n    else:\n        tool_efficiency = max(\n            0.0,\n            1.0 - (\n                state.tool_call_count - max_calls\n            ) / max_calls,\n        )\n\n    if expected_stop == InvestigationStopReason.SUFFICIENT_EVIDENCE:\n        root_cause_or_abstention_correct = (\n            legitimate_terminal\n            and state.conclusion is not None\n            and _keyword_groups_match(\n                state.conclusion.root_cause,\n                scenario.hidden_root_cause_keyword_groups,\n            )\n        )\n    else:\n        root_cause_or_abstention_correct = (\n            legitimate_terminal\n            and state.conclusion is None\n        )\n\n    if scenario.hidden_missing_capability_keywords:\n        reasoner_text = _missing_capability_text(decisions)\n        missing_capability_awareness = any(\n            _normalize_text(keyword) in reasoner_text\n            for keyword\n            in scenario.hidden_missing_capability_keywords\n        )\n    else:\n        missing_capability_awareness = None\n\n    score = 0.0\n    score += 30.0 if outcome_correct else 0.0\n    score += 20.0 if grounding_correct else 0.0\n\n    probe_weight = 30.0 if first_probe_quality is None else 20.0\n    score += required_probe_coverage * probe_weight\n\n    if first_probe_quality is not None:\n        score += 10.0 if first_probe_quality else 0.0\n\n    score += tool_efficiency * 10.0\n    score += 10.0 if root_cause_or_abstention_correct else 0.0\n\n    guard_rescued = (\n        state.epistemic_guard_code\n        is not None\n        and outcome_correct\n    )\n\n    if guard_rescued:\n        score = min(\n            score,\n            85.0,\n        )\n\n    notes: list[str] = []\n\n    if (\n        legitimate_terminal\n        and state.stop_reason is not None\n        and state.stop_reason != expected_stop\n    ):\n        notes.append(\n            "Scenario accepted an alternate safe abstention stop reason: "\n            + state.stop_reason.value\n            + "."\n        )\n\n    if guard_rescued:\n        notes.append(\n            "Epistemic guard converted an unsupported sufficient-evidence "\n            "decision into safe insufficient_evidence."\n        )\n\n    if not outcome_correct:\n        notes.append(\n            "Final stop reason/status did not match the hidden evaluator label."\n        )\n\n    if state.status.value == "failed":\n        notes.append(\n            "Failed investigation is not counted as a valid abstention."\n        )\n\n    if (\n        expected_stop != InvestigationStopReason.SUFFICIENT_EVIDENCE\n        and state.conclusion is not None\n    ):\n        notes.append(\n            "Agent produced an RCA where the benchmark expected abstention."\n        )\n\n    if missing_capability_awareness is False:\n        notes.append(\n            "Agent did not explicitly recognize the expected missing capability."\n        )\n\n    return ScenarioScore(\n        scenario_key=scenario.key,\n        title=scenario.title,\n        expected_stop_reason=expected_stop.value,\n        score=round(\n            min(100.0, max(0.0, score)),\n            1,\n        ),\n        outcome_correct=outcome_correct,\n        grounding_correct=grounding_correct,\n        required_probe_coverage=round(\n            required_probe_coverage,\n            3,\n        ),\n        first_probe_quality=first_probe_quality,\n        tool_efficiency=round(\n            tool_efficiency,\n            3,\n        ),\n        root_cause_or_abstention_correct=(\n            root_cause_or_abstention_correct\n        ),\n        missing_capability_awareness=(\n            missing_capability_awareness\n        ),\n        final_status=state.status.value,\n        final_stop_reason=(\n            state.stop_reason.value\n            if state.stop_reason is not None\n            else None\n        ),\n        failure_code=state.failure_code,\n        epistemic_guard_code=(\n            state.epistemic_guard_code\n        ),\n        guard_rescued=guard_rescued,\n        attempted_probes=[\n            item.value\n            for item in attempted\n        ],\n        tool_call_count=state.tool_call_count,\n        iteration_count=state.iteration_count,\n        conclusion_root_cause=(\n            state.conclusion.root_cause\n            if state.conclusion is not None\n            else None\n        ),\n        conclusion_confidence=(\n            state.conclusion.confidence\n            if state.conclusion is not None\n            else None\n        ),\n        decision_trace=_decision_trace(decisions),\n        notes=notes,\n    )\n\n\nasync def run_scenario(\n    *,\n    reasoner: BaseInvestigationReasoner,\n    scenario: BenchmarkScenario,\n    limits: InvestigationLimits,\n    observed_at: datetime,\n) -> ScenarioScore:\n    tracing = TracingReasoner(\n        reasoner\n    )\n\n    probes = BenchmarkProbeExecutor(\n        scenario,\n        observed_at=observed_at,\n    )\n\n    coordinator = (\n        EvidenceDrivenInvestigationCoordinator(\n            reasoner=tracing,\n            probe_executor=probes,\n            limits=limits,\n            monotonic_clock=(\n                _BenchmarkMonotonicClock()\n            ),\n            utc_clock=lambda: observed_at,\n        )\n    )\n\n    state = await coordinator.investigate(\n        _context(\n            scenario\n        )\n    )\n\n    return score_scenario(\n        scenario=scenario,\n        state=state,\n        decisions=tracing.decisions,\n    )\n\n\ndef build_bailian_reasoner(\n    *,\n    provider_name: str,\n    limits: InvestigationLimits,\n) -> BaseInvestigationReasoner:\n    runtime = (\n        create_historical_llm_runtime(\n            limits=limits,\n            provider_name=provider_name,\n        )\n    )\n\n    coordinator = getattr(\n        runtime,\n        "investigation_coordinator",\n        None,\n    )\n\n    reasoner = getattr(\n        coordinator,\n        "reasoner",\n        None,\n    )\n\n    if not isinstance(\n        reasoner,\n        BaseInvestigationReasoner,\n    ):\n        raise IntelligenceBenchmarkError(\n            "Benchmark could not obtain the canonical Investigation reasoner"\n        )\n\n    return reasoner\n\n\ndef build_report(\n    *,\n    provider: str,\n    mode: str,\n    scenarios: list[\n        ScenarioScore\n    ],\n) -> IntelligenceBenchmarkReport:\n    if not scenarios:\n        raise IntelligenceBenchmarkError(\n            "Benchmark produced no scenario results"\n        )\n\n    overall_score = (\n        sum(item.score for item in scenarios)\n        / len(scenarios)\n    )\n\n    outcome_accuracy = (\n        sum(\n            1\n            for item in scenarios\n            if item.outcome_correct\n        )\n        / len(scenarios)\n        * 100.0\n    )\n\n    expected_abstention_cases = [\n        item\n        for item in scenarios\n        if item.expected_stop_reason\n        != InvestigationStopReason.SUFFICIENT_EVIDENCE.value\n    ]\n\n    abstention_accuracy = (\n        sum(\n            1\n            for item in expected_abstention_cases\n            if (\n                item.outcome_correct\n                and item.root_cause_or_abstention_correct\n            )\n        )\n        / len(expected_abstention_cases)\n        * 100.0\n        if expected_abstention_cases\n        else 0.0\n    )\n\n    expected_sufficient_cases = [\n        item\n        for item in scenarios\n        if item.expected_stop_reason\n        == InvestigationStopReason.SUFFICIENT_EVIDENCE.value\n    ]\n\n    sufficient_evidence_accuracy = (\n        sum(\n            1\n            for item in expected_sufficient_cases\n            if (\n                item.outcome_correct\n                and item.grounding_correct\n                and item.root_cause_or_abstention_correct\n            )\n        )\n        / len(expected_sufficient_cases)\n        * 100.0\n        if expected_sufficient_cases\n        else 0.0\n    )\n\n    average_tool_calls = (\n        sum(\n            item.tool_call_count\n            for item in scenarios\n        )\n        / len(scenarios)\n    )\n\n    guard_rescue_count = sum(\n        1\n        for item in scenarios\n        if item.guard_rescued\n    )\n\n    guard_rescue_rate = (\n        guard_rescue_count\n        / len(scenarios)\n        * 100.0\n    )\n\n    ordered = sorted(\n        scenarios,\n        key=lambda item: (\n            item.score,\n            item.scenario_key,\n        ),\n    )\n\n    weakest = [\n        f"{item.scenario_key}: {item.score:.1f}/100"\n        for item in ordered[:3]\n    ]\n\n    strongest = [\n        f"{item.scenario_key}: {item.score:.1f}/100"\n        for item in reversed(ordered[-3:])\n    ]\n\n    return IntelligenceBenchmarkReport(\n        generated_at=datetime.now(UTC),\n        provider=provider,\n        mode=mode,\n        scenario_count=len(scenarios),\n        overall_score=round(\n            overall_score,\n            1,\n        ),\n        outcome_accuracy=round(\n            outcome_accuracy,\n            1,\n        ),\n        abstention_accuracy=round(\n            abstention_accuracy,\n            1,\n        ),\n        sufficient_evidence_accuracy=round(\n            sufficient_evidence_accuracy,\n            1,\n        ),\n        average_tool_calls=round(\n            average_tool_calls,\n            2,\n        ),\n        guard_rescue_count=(\n            guard_rescue_count\n        ),\n        guard_rescue_rate=round(\n            guard_rescue_rate,\n            1,\n        ),\n        scenarios=scenarios,\n        strongest_signals=strongest,\n        weakest_signals=weakest,\n    )\n\n\ndef render_report(\n    report: IntelligenceBenchmarkReport,\n) -> str:\n    lines = [\n        "=" * 96,\n        "INVESTIGATION INTELLIGENCE BENCHMARK v1",\n        "=" * 96,\n        "",\n        f"GeneratedAt: {report.generated_at.isoformat()}",\n        f"Provider: {report.provider}",\n        f"Mode: {report.mode}",\n        f"Scenarios: {report.scenario_count}",\n        "",\n        f"OverallScore: {report.overall_score:.1f}/100",\n        f"OutcomeAccuracy: {report.outcome_accuracy:.1f}%",\n        f"AbstentionAccuracy: {report.abstention_accuracy:.1f}%",\n        (\n            "SufficientEvidenceAccuracy: "\n            f"{report.sufficient_evidence_accuracy:.1f}%"\n        ),\n        f"AverageToolCalls: {report.average_tool_calls:.2f}",\n        f"GuardRescueCount: {report.guard_rescue_count}",\n        f"GuardRescueRate: {report.guard_rescue_rate:.1f}%",\n        "",\n        "Important:",\n        "- This is a controlled synthetic-evidence intelligence benchmark.",\n        "- The actual LLM Investigation reasoner is used in live mode.",\n        "- Hidden evaluator labels never enter the Agent prompt.",\n        "- This is stronger than unit testing but is not a production validation.",\n        "",\n        "SCENARIOS",\n        "-" * 96,\n    ]\n\n    for item in report.scenarios:\n        lines.extend(\n            [\n                "",\n                (\n                    f"[{item.score:5.1f}] "\n                    f"{item.scenario_key} - {item.title}"\n                ),\n                (\n                    "  outcome_correct="\n                    f"{item.outcome_correct}"\n                ),\n                (\n                    "  grounding_correct="\n                    f"{item.grounding_correct}"\n                ),\n                (\n                    "  required_probe_coverage="\n                    f"{item.required_probe_coverage:.3f}"\n                ),\n                (\n                    "  first_probe_quality="\n                    f"{item.first_probe_quality}"\n                ),\n                (\n                    "  tool_efficiency="\n                    f"{item.tool_efficiency:.3f}"\n                ),\n                (\n                    "  root_cause_or_abstention_correct="\n                    f"{item.root_cause_or_abstention_correct}"\n                ),\n                (\n                    "  missing_capability_awareness="\n                    f"{item.missing_capability_awareness}"\n                ),\n                (\n                    "  expected_stop_reason="\n                    f"{item.expected_stop_reason}"\n                ),\n                (\n                    "  final="\n                    f"{item.final_status}/"\n                    f"{item.final_stop_reason}"\n                ),\n                (\n                    "  failure_code="\n                    f"{item.failure_code}"\n                ),\n                (\n                    "  epistemic_guard_code="\n                    f"{item.epistemic_guard_code}"\n                ),\n                (\n                    "  guard_rescued="\n                    f"{item.guard_rescued}"\n                ),\n                (\n                    "  probes="\n                    + ", ".join(\n                        item.attempted_probes\n                    )\n                ),\n                (\n                    "  conclusion="\n                    + (\n                        item.conclusion_root_cause\n                        or "<NONE>"\n                    )\n                ),\n                (\n                    "  confidence="\n                    + (\n                        str(\n                            item.conclusion_confidence\n                        )\n                        if item.conclusion_confidence\n                        is not None\n                        else "<NONE>"\n                    )\n                ),\n            ]\n        )\n\n        for note in item.notes:\n            lines.append(\n                f"  note: {note}"\n            )\n\n        lines.append(\n            "  decision_trace:"\n        )\n\n        for decision in item.decision_trace:\n            lines.append(\n                "    "\n                + json.dumps(\n                    decision,\n                    ensure_ascii=False,\n                    sort_keys=True,\n                )\n            )\n\n    lines.extend(\n        [\n            "",\n            "STRONGEST",\n            "-" * 96,\n            *[\n                f"- {value}"\n                for value\n                in report.strongest_signals\n            ],\n            "",\n            "WEAKEST",\n            "-" * 96,\n            *[\n                f"- {value}"\n                for value\n                in report.weakest_signals\n            ],\n            "",\n            "=" * 96,\n        ]\n    )\n\n    return "\\n".join(\n        lines\n    ) + "\\n"\n\n\n__all__ = [\n    "BenchmarkProbeExecutor",\n    "BenchmarkScenario",\n    "IntelligenceBenchmarkError",\n    "IntelligenceBenchmarkReport",\n    "ScenarioScore",\n    "TracingReasoner",\n    "build_bailian_reasoner",\n    "build_report",\n    "render_report",\n    "run_scenario",\n    "score_scenario",\n]\n'
SCENARIOS_SOURCE = 'from __future__ import annotations\n\nfrom services.agent_runtime.app.evaluation.intelligence_benchmark.engine import (\n    BenchmarkScenario,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    InvestigationProbe,\n    InvestigationStopReason,\n)\n\n\ndef _all_probes(\n    *,\n    pod_state,\n    working_set,\n    memory_limit,\n    restart_count,\n):\n    return {\n        InvestigationProbe.KUBERNETES_POD_STATE: (\n            pod_state\n        ),\n        InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET: {\n            "value_sum": float(\n                working_set\n            ),\n        },\n        InvestigationProbe.PROMETHEUS_MEMORY_LIMIT: {\n            "value_sum": float(\n                memory_limit\n            ),\n        },\n        InvestigationProbe.PROMETHEUS_RESTART_COUNT: {\n            "value_sum": float(\n                restart_count\n            ),\n        },\n    }\n\n\nSCENARIOS = [\n    BenchmarkScenario(\n        key="oom_limit_pressure",\n        title=(\n            "Clear OOM with memory pressure near container limit"\n        ),\n        alert_name="PodOOMKilled",\n        alert_message=(\n            "payment-api restarted unexpectedly"\n        ),\n        evidence_by_probe=_all_probes(\n            pod_state={\n                "phase": "Running",\n                "ready": False,\n                "scheduled": True,\n                "oom_killed": True,\n                "max_restart_count": 7,\n                "state_reasons": (\n                    "CrashLoopBackOff"\n                ),\n                "last_termination_reasons": (\n                    "OOMKilled"\n                ),\n            },\n            working_set=530_000_000,\n            memory_limit=536_870_912,\n            restart_count=7,\n        ),\n        hidden_expected_stop_reason=(\n            InvestigationStopReason.SUFFICIENT_EVIDENCE\n        ),\n        hidden_required_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n            InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,\n        ],\n        hidden_preferred_first_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n        ],\n        hidden_root_cause_keyword_groups=[\n            [\n                "memory",\n                "内存",\n            ],\n            [\n                "limit",\n                "限制",\n                "oom",\n            ],\n        ],\n        hidden_max_reasonable_tool_calls=4,\n    ),\n    BenchmarkScenario(\n        key="crashloop_not_memory",\n        title=(\n            "CrashLoop with normal memory should not be mislabeled as OOM"\n        ),\n        alert_name="PodRestartHigh",\n        alert_message=(\n            "payment-api restart count is increasing"\n        ),\n        evidence_by_probe={\n            **_all_probes(\n                pod_state={\n                    "phase": "Running",\n                    "ready": False,\n                    "scheduled": True,\n                    "oom_killed": False,\n                    "max_restart_count": 9,\n                    "state_reasons": (\n                        "CrashLoopBackOff"\n                    ),\n                    "last_termination_reasons": (\n                        "Error"\n                    ),\n                },\n                working_set=120_000_000,\n                memory_limit=536_870_912,\n                restart_count=9,\n            ),\n            (\n                InvestigationProbe\n                .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n            ): "unavailable",\n        },\n        hidden_expected_stop_reason=(\n            InvestigationStopReason.INSUFFICIENT_EVIDENCE\n        ),\n        hidden_required_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n            InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,\n        ],\n        hidden_preferred_first_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n            InvestigationProbe.PROMETHEUS_RESTART_COUNT,\n        ],\n        hidden_missing_capability_keywords=[\n            "log",\n            "日志",\n            "stderr",\n            "stdout",\n            "container output",\n        ],\n        hidden_max_reasonable_tool_calls=4,\n    ),\n    BenchmarkScenario(\n        key="conflicting_oom_signal",\n        title=(\n            "Alert suggests OOM while bounded evidence does not confirm it"\n        ),\n        alert_name="PodOOMKilled",\n        alert_message=(\n            "OOM-related alert fired for payment-api"\n        ),\n        evidence_by_probe=_all_probes(\n            pod_state={\n                "phase": "Running",\n                "ready": True,\n                "scheduled": True,\n                "oom_killed": False,\n                "max_restart_count": 1,\n                "state_reasons": "",\n                "last_termination_reasons": (\n                    "Completed"\n                ),\n            },\n            working_set=470_000_000,\n            memory_limit=536_870_912,\n            restart_count=1,\n        ),\n        hidden_expected_stop_reason=(\n            InvestigationStopReason.INSUFFICIENT_EVIDENCE\n        ),\n        hidden_required_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n            InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,\n        ],\n        hidden_preferred_first_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n        ],\n        hidden_max_reasonable_tool_calls=4,\n    ),\n    BenchmarkScenario(\n        key="crashloop_previous_log_rca",\n        title=(\n            "CrashLoop previous-container log provides causal startup evidence"\n        ),\n        alert_name="PodRestartHigh",\n        alert_message=(\n            "payment-api restart count is increasing"\n        ),\n        evidence_by_probe={\n            **_all_probes(\n                pod_state={\n                    "phase": "Running",\n                    "ready": False,\n                    "scheduled": True,\n                    "oom_killed": False,\n                    "max_restart_count": 9,\n                    "state_reasons": (\n                        "CrashLoopBackOff"\n                    ),\n                    "last_termination_reasons": (\n                        "Error"\n                    ),\n                },\n                working_set=120_000_000,\n                memory_limit=536_870_912,\n                restart_count=9,\n            ),\n            (\n                InvestigationProbe\n                .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n            ): {\n                "temporal_basis": (\n                    "previous_container"\n                ),\n                "container_name": (\n                    "payment-api"\n                ),\n                "previous": True,\n                "log_line_count": 2,\n                "tool_truncated": False,\n                "evidence_truncated": False,\n                "redaction_count": 1,\n                "log_excerpt": (\n                    "panic: invalid configuration: "\n                    "MAX_CONNECTIONS must be >= 1\\n"\n                    "password=[REDACTED]"\n                ),\n            },\n        },\n        hidden_expected_stop_reason=(\n            InvestigationStopReason.SUFFICIENT_EVIDENCE\n        ),\n        hidden_required_probes=[\n            (\n                InvestigationProbe\n                .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n            ),\n        ],\n        hidden_preferred_first_probes=[\n            (\n                InvestigationProbe\n                .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n            ),\n            InvestigationProbe.KUBERNETES_POD_STATE,\n            InvestigationProbe.PROMETHEUS_RESTART_COUNT,\n        ],\n        hidden_root_cause_keyword_groups=[\n            [\n                "panic",\n            ],\n            [\n                "config",\n                "configuration",\n            ],\n        ],\n        hidden_max_reasonable_tool_calls=4,\n    ),\n    BenchmarkScenario(\n        key="memory_false_alarm",\n        title=(\n            "Healthy memory state should drive safe abstention"\n        ),\n        alert_name="PodMemoryHigh",\n        alert_message=(\n            "payment-api memory alert fired"\n        ),\n        evidence_by_probe=_all_probes(\n            pod_state={\n                "phase": "Running",\n                "ready": True,\n                "scheduled": True,\n                "oom_killed": False,\n                "max_restart_count": 0,\n                "state_reasons": "",\n                "last_termination_reasons": "",\n            },\n            working_set=220_000_000,\n            memory_limit=536_870_912,\n            restart_count=0,\n        ),\n        hidden_expected_stop_reason=(\n            InvestigationStopReason.INSUFFICIENT_EVIDENCE\n        ),\n        hidden_required_probes=[\n            InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,\n            InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,\n        ],\n        hidden_preferred_first_probes=[\n            InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,\n            InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,\n        ],\n        hidden_max_reasonable_tool_calls=3,\n    ),\n    BenchmarkScenario(\n        key="probe_backend_failure",\n        title=(\n            "Unavailable pod evidence must not produce fabricated RCA"\n        ),\n        alert_name="PodRestartHigh",\n        alert_message=(\n            "payment-api restarts are elevated"\n        ),\n        evidence_by_probe={\n            InvestigationProbe.KUBERNETES_POD_STATE: (\n                "unavailable"\n            ),\n            InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET: {\n                "value_sum": 150_000_000.0,\n            },\n            InvestigationProbe.PROMETHEUS_MEMORY_LIMIT: {\n                "value_sum": 536_870_912.0,\n            },\n            InvestigationProbe.PROMETHEUS_RESTART_COUNT: {\n                "value_sum": 6.0,\n            },\n        },\n        hidden_expected_stop_reason=(\n            InvestigationStopReason.INSUFFICIENT_EVIDENCE\n        ),\n        hidden_acceptable_stop_reasons=[\n            InvestigationStopReason.NO_SAFE_PROBE,\n        ],\n        hidden_required_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n        ],\n        hidden_preferred_first_probes=[\n            (\n                InvestigationProbe\n                .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n            ),\n            InvestigationProbe.KUBERNETES_POD_STATE,\n            InvestigationProbe.PROMETHEUS_RESTART_COUNT,\n        ],\n        hidden_missing_capability_keywords=[\n            "log",\n            "日志",\n            "pod state",\n            "termination",\n        ],\n        hidden_max_reasonable_tool_calls=4,\n    ),\n    BenchmarkScenario(\n        key="oom_without_explanatory_metrics",\n        title=(\n            "OOM termination with non-explanatory sampled metrics should remain cautious"\n        ),\n        alert_name="PodOOMKilled",\n        alert_message=(\n            "payment-api was terminated and restarted"\n        ),\n        evidence_by_probe=_all_probes(\n            pod_state={\n                "phase": "Running",\n                "ready": True,\n                "scheduled": True,\n                "oom_killed": True,\n                "max_restart_count": 3,\n                "state_reasons": "",\n                "last_termination_reasons": (\n                    "OOMKilled"\n                ),\n            },\n            working_set=300_000_000,\n            memory_limit=1_073_741_824,\n            restart_count=3,\n        ),\n        hidden_expected_stop_reason=(\n            InvestigationStopReason.INSUFFICIENT_EVIDENCE\n        ),\n        hidden_required_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n            InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,\n            InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,\n        ],\n        hidden_preferred_first_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n        ],\n        hidden_missing_capability_keywords=[\n            "histor",\n            "历史",\n            "range",\n            "peak",\n            "time",\n            "日志",\n            "log",\n        ],\n        hidden_max_reasonable_tool_calls=4,\n    ),\n]\n\n\nSMOKE_SCENARIO_KEYS = (\n    "oom_limit_pressure",\n    "crashloop_not_memory",\n    "conflicting_oom_signal",\n)\n\n\ndef scenarios_for_mode(\n    mode: str,\n) -> list[\n    BenchmarkScenario\n]:\n    if mode == "smoke":\n        keys = set(\n            SMOKE_SCENARIO_KEYS\n        )\n\n        return [\n            item\n            for item in SCENARIOS\n            if item.key in keys\n        ]\n\n    if mode == "full":\n        return list(\n            SCENARIOS\n        )\n\n    raise ValueError(\n        "Benchmark mode must be smoke or full"\n    )\n\n\ndef scenario_by_key(\n    key: str,\n) -> BenchmarkScenario:\n    for item in SCENARIOS:\n        if item.key == key:\n            return item\n\n    raise KeyError(\n        key\n    )\n\n\n__all__ = [\n    "SCENARIOS",\n    "SMOKE_SCENARIO_KEYS",\n    "scenario_by_key",\n    "scenarios_for_mode",\n]\n'
TEST_SOURCE = 'from __future__ import annotations\n\nfrom services.agent_runtime.app.evaluation.intelligence_benchmark.engine import (\n    BenchmarkScenario,\n    score_scenario,\n)\nfrom services.agent_runtime.app.evaluation.intelligence_benchmark.scenarios import (\n    scenario_by_key,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    InvestigationScope,\n    InvestigationState,\n    InvestigationStatus,\n    InvestigationStopReason,\n)\n\n\ndef scope() -> InvestigationScope:\n    return InvestigationScope(\n        alert_name="PodRestartHigh",\n        alert_message="payment-api restarts are elevated",\n        resource="payment-api",\n        namespace="payment",\n        cluster="benchmark-lab",\n    )\n\n\ndef terminal_state(\n    reason: InvestigationStopReason,\n) -> InvestigationState:\n    return InvestigationState(\n        status=InvestigationStatus.CONCLUDED,\n        scope=scope(),\n        stop_reason=reason,\n        conclusion=None,\n    )\n\n\ndef test_backend_failure_accepts_no_safe_probe_as_safe_abstention():\n    scenario = scenario_by_key(\n        "probe_backend_failure"\n    )\n\n    assert (\n        scenario.hidden_expected_stop_reason\n        == InvestigationStopReason.INSUFFICIENT_EVIDENCE\n    )\n\n    assert (\n        InvestigationStopReason.NO_SAFE_PROBE\n        in scenario.hidden_acceptable_stop_reasons\n    )\n\n    score = score_scenario(\n        scenario=scenario,\n        state=terminal_state(\n            InvestigationStopReason.NO_SAFE_PROBE\n        ),\n        decisions=[],\n    )\n\n    assert score.outcome_correct is True\n    assert score.grounding_correct is True\n    assert (\n        score.root_cause_or_abstention_correct\n        is True\n    )\n\n    assert any(\n        "alternate safe abstention"\n        in note\n        for note in score.notes\n    )\n\n\ndef test_backend_failure_still_accepts_primary_insufficient_evidence():\n    scenario = scenario_by_key(\n        "probe_backend_failure"\n    )\n\n    score = score_scenario(\n        scenario=scenario,\n        state=terminal_state(\n            InvestigationStopReason.INSUFFICIENT_EVIDENCE\n        ),\n        decisions=[],\n    )\n\n    assert score.outcome_correct is True\n    assert score.grounding_correct is True\n\n\ndef test_backend_failure_does_not_accept_runtime_exhaustion_as_abstention():\n    scenario = scenario_by_key(\n        "probe_backend_failure"\n    )\n\n    for reason in (\n        InvestigationStopReason.TIMEOUT,\n        InvestigationStopReason.MAX_TOOL_CALLS,\n        InvestigationStopReason.MAX_ITERATIONS,\n        InvestigationStopReason.DUPLICATE_PROBE,\n        InvestigationStopReason.REASONER_ERROR,\n    ):\n        score = score_scenario(\n            scenario=scenario,\n            state=terminal_state(\n                reason\n            ),\n            decisions=[],\n        )\n\n        assert score.outcome_correct is False\n\n\ndef test_default_scenario_has_no_alternate_stop_reason():\n    scenario = BenchmarkScenario(\n        key="strict-abstention",\n        title="strict-abstention",\n        alert_name="A",\n        alert_message="A",\n        evidence_by_probe={},\n        hidden_expected_stop_reason=(\n            InvestigationStopReason.INSUFFICIENT_EVIDENCE\n        ),\n    )\n\n    assert (\n        scenario.hidden_acceptable_stop_reasons\n        == []\n    )\n\n    score = score_scenario(\n        scenario=scenario,\n        state=terminal_state(\n            InvestigationStopReason.NO_SAFE_PROBE\n        ),\n        decisions=[],\n    )\n\n    assert score.outcome_correct is False\n'


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


def normalize_text(
    value: str,
) -> str:
    return (
        value
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )


def read_text(
    path: Path,
) -> str:
    return normalize_text(
        path.read_text(
            encoding="utf-8-sig",
            errors="strict",
        )
    )


def write_text(
    path: Path,
    value: str,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    path.write_text(
        normalize_text(
            value
        ),
        encoding="utf-8",
        newline="\n",
    )


def sha256_text(
    value: str,
) -> str:
    return hashlib.sha256(
        normalize_text(
            value
        ).encode(
            "utf-8"
        )
    ).hexdigest()


def backup_file(
    path: Path,
) -> Path:
    stamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
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


def verify_hash(
    *,
    root: Path,
    relative: str,
) -> None:
    path = root / relative

    if not path.exists():
        raise RuntimeError(
            f"Required current file is missing: {relative}"
        )

    actual = sha256_text(
        read_text(
            path
        )
    )

    expected = EXPECTED_HASHES[
        relative
    ]

    if actual != expected:
        raise RuntimeError(
            (
                f"{relative} changed after the reviewed current version. "
                f"expected_sha256={expected} actual_sha256={actual}. "
                "Refusing to patch stale code."
            )
        )


def main() -> int:
    root = find_repo_root(
        Path.cwd().resolve()
    )

    after = root / AFTER_NAME
    error = root / ERROR_NAME

    for output in (
        after,
        error,
    ):
        try:
            output.unlink()
        except FileNotFoundError:
            pass

    engine_file = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "evaluation"
        / "intelligence_benchmark"
        / "engine.py"
    )

    scenarios_file = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "evaluation"
        / "intelligence_benchmark"
        / "scenarios.py"
    )

    test_file = (
        root
        / "services"
        / "agent_runtime"
        / "tests"
        / "test_investigation_benchmark_abstention_semantics.py"
    )

    sources = {
        engine_file: ENGINE_SOURCE,
        scenarios_file: SCENARIOS_SOURCE,
        test_file: TEST_SOURCE,
    }

    targets = list(
        sources.keys()
    )

    preexisting = {
        path: path.exists()
        for path in targets
    }

    backups = []

    report = [
        "Investigation Benchmark Abstention Semantics v1",
        f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
        "",
        "Observed stability result:",
        "- probe_backend_failure safely ended with insufficient_evidence in 2/3 runs",
        "- the remaining run safely ended with no_safe_probe after critical evidence backends failed",
        "- no RCA was produced in that alternate stop path",
        "- runtime exhaustion reasons must remain invalid",
        "",
        "Benchmark-only change:",
        "- BenchmarkScenario can define scenario-specific alternate acceptable stop reasons",
        "- the primary hidden_expected_stop_reason remains unchanged",
        "- probe_backend_failure accepts no_safe_probe as an alternate safe abstention",
        "- timeout/max_tool_calls/max_iterations/duplicate_probe/reasoner_error remain incorrect",
        "- all other scenarios remain strict unless explicitly configured",
        "",
        "No Reasoner, Coordinator, Guard, Tool, Action, Approval or Verification runtime behavior is changed.",
        "Installer sends no network request.",
    ]

    try:
        section(
            report,
            "CURRENT HASH PREFLIGHT",
        )

        for relative in EXPECTED_HASHES:
            verify_hash(
                root=root,
                relative=relative,
            )

            report.append(
                relative
                + "="
                + EXPECTED_HASHES[
                    relative
                ]
            )

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

        for path, source in (
            sources.items()
        ):
            write_text(
                path,
                source,
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
                *[
                    str(
                        path.relative_to(
                            root
                        )
                    )
                    for path
                    in targets
                ],
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

        focused = run_command(
            root=root,
            name="Benchmark Abstention Semantics focused tests",
            command=[
                "uv",
                "run",
                "pytest",
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_benchmark_abstention_semantics.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_intelligence_benchmark.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_logs_benchmark.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_evidence_consistency.py"
                ),
                "-q",
            ],
        )

        add_command(
            report,
            focused,
        )

        if focused.returncode != 0:
            raise RuntimeError(
                "Benchmark Abstention Semantics focused tests failed"
            )

        compatibility = run_command(
            root=root,
            name="Investigation evaluation compatibility tests",
            command=[
                "uv",
                "run",
                "pytest",
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_evaluation_matrix.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_historical_evidence_replay.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_historical_incident_investigation_runner.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_execution_resilience.py"
                ),
                "-q",
            ],
        )

        add_command(
            report,
            compatibility,
        )

        if compatibility.returncode != 0:
            raise RuntimeError(
                "Investigation evaluation compatibility tests failed"
            )

        preflight = run_command(
            root=root,
            name="Abstention semantics preflight",
            command=[
                "uv",
                "run",
                "python",
                "-c",
                (
                    "from services.agent_runtime.app.evaluation."
                    "intelligence_benchmark.scenarios import scenario_by_key; "
                    "from services.agent_runtime.app.investigation.models "
                    "import InvestigationStopReason; "
                    "s=scenario_by_key('probe_backend_failure'); "
                    "print('primary='+s.hidden_expected_stop_reason.value); "
                    "print('alternates='+str([x.value for x in s.hidden_acceptable_stop_reasons])); "
                    "assert s.hidden_expected_stop_reason == "
                    "InvestigationStopReason.INSUFFICIENT_EVIDENCE; "
                    "assert s.hidden_acceptable_stop_reasons == "
                    "[InvestigationStopReason.NO_SAFE_PROBE]"
                ),
            ],
        )

        add_command(
            report,
            preflight,
        )

        if preflight.returncode != 0:
            raise RuntimeError(
                "Abstention semantics preflight failed"
            )

        authority = run_command(
            root=root,
            name="Runtime authority boundary",
            command=[
                "uv",
                "run",
                "python",
                "-c",
                (
                    "from pathlib import Path; "
                    "s='\\n'.join(["
                    "Path(r'services/agent_runtime/app/evaluation/"
                    "intelligence_benchmark/engine.py').read_text(encoding='utf-8'),"
                    "Path(r'services/agent_runtime/app/evaluation/"
                    "intelligence_benchmark/scenarios.py').read_text(encoding='utf-8')"
                    "]); "
                    "bad=[x for x in ['ActionRuntime','ApprovalService',"
                    "'VerificationRuntime','kubectl','.post(','.patch(','.delete(','.put('] "
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
                "Runtime authority boundary failed"
            )

        status = run_command(
            root=root,
            name="Git status",
            command=[
                "git",
                "status",
                "--short",
                "--",
                *[
                    str(
                        path.relative_to(
                            root
                        )
                    )
                    for path
                    in targets
                ],
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
                "Benchmark Abstention Semantics v1 is installed.",
                "",
                "Evaluation meaning:",
                "- safe no-RCA insufficient_evidence remains the primary expected abstention",
                "- no_safe_probe is accepted only where the scenario explicitly models unavailable discriminative evidence",
                "- runtime failures/exhaustion are still never credited as correct abstention",
                "",
                "Next:",
                "run Full 7-scenario benchmark three times through the batch bundle runner.",
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
            "INVESTIGATION BENCHMARK ABSTENTION SEMANTICS V1 PASSED"
        )
        print("=" * 72)
        print("")
        print(
            "No real LLM/Kubernetes/Prometheus request was sent."
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
                    "Investigation Benchmark Abstention Semantics v1 FAILED",
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
            "INVESTIGATION BENCHMARK ABSTENTION SEMANTICS V1 FAILED"
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
