from __future__ import annotations

import ast
import shutil
import subprocess
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


VERSION = "investigation-terminal-contract-hardening-v1"
AFTER_NAME = "investigation_terminal_contract_hardening_v1_after.txt"
ERROR_NAME = "investigation_terminal_contract_hardening_v1_error.txt"

ENGINE_SOURCE = 'from __future__ import annotations\n\nimport json\nfrom dataclasses import dataclass\nfrom datetime import UTC, datetime\nfrom types import SimpleNamespace\nfrom typing import Any\n\nfrom pydantic import BaseModel, ConfigDict, Field\n\nfrom services.agent_runtime.app.evaluation.real_incident.llm_run import (\n    create_historical_llm_runtime,\n)\nfrom services.agent_runtime.app.investigation.coordinator import (\n    EvidenceDrivenInvestigationCoordinator,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    EvidenceItem,\n    InvestigationDecision,\n    InvestigationLimits,\n    InvestigationProbe,\n    InvestigationState,\n    InvestigationStopReason,\n)\nfrom services.agent_runtime.app.investigation.reasoner import (\n    BaseInvestigationReasoner,\n)\n\n\nclass IntelligenceBenchmarkError(\n    RuntimeError\n):\n    pass\n\n\nclass BenchmarkScenario(BaseModel):\n    """\n    One hidden-label Investigation exam.\n\n    hidden_* fields are evaluator-only. They never enter the Agent context,\n    InvestigationScope, LLM prompt, EvidenceItem facts or decision history.\n    """\n\n    model_config = ConfigDict(\n        frozen=True,\n        extra="forbid",\n    )\n\n    key: str\n    title: str\n\n    alert_name: str\n    alert_message: str\n\n    resource: str = "payment-api"\n    namespace: str = "payment"\n    cluster: str = "benchmark-lab"\n\n    evidence_by_probe: dict[\n        InvestigationProbe,\n        dict[str, Any] | str,\n    ]\n\n    hidden_expected_stop_reason: (\n        InvestigationStopReason\n    )\n\n    hidden_required_probes: list[\n        InvestigationProbe\n    ] = Field(\n        default_factory=list\n    )\n\n    hidden_preferred_first_probes: list[\n        InvestigationProbe\n    ] = Field(\n        default_factory=list\n    )\n\n    hidden_root_cause_keyword_groups: list[\n        list[str]\n    ] = Field(\n        default_factory=list\n    )\n\n    hidden_missing_capability_keywords: list[\n        str\n    ] = Field(\n        default_factory=list\n    )\n\n    hidden_max_reasonable_tool_calls: int = Field(\n        default=4,\n        ge=0,\n        le=10,\n    )\n\n\nclass ScenarioScore(BaseModel):\n    model_config = ConfigDict(\n        frozen=True,\n        extra="forbid",\n    )\n\n    scenario_key: str\n    title: str\n\n    score: float = Field(\n        ge=0.0,\n        le=100.0,\n    )\n\n    expected_stop_reason: str\n    outcome_correct: bool\n    grounding_correct: bool\n    required_probe_coverage: float\n    first_probe_quality: bool | None\n    tool_efficiency: float\n    root_cause_or_abstention_correct: bool\n    missing_capability_awareness: bool | None\n\n    final_status: str\n    final_stop_reason: str | None\n    failure_code: str | None\n\n    attempted_probes: list[str]\n    tool_call_count: int\n    iteration_count: int\n\n    conclusion_root_cause: str | None\n    conclusion_confidence: float | None\n\n    decision_trace: list[\n        dict[str, Any]\n    ]\n\n    notes: list[str] = Field(\n        default_factory=list\n    )\n\n\nclass IntelligenceBenchmarkReport(BaseModel):\n    model_config = ConfigDict(\n        frozen=True,\n        extra="forbid",\n    )\n\n    schema_version: str = "v1"\n    generated_at: datetime\n\n    provider: str\n    mode: str\n\n    scenario_count: int\n    overall_score: float = Field(\n        ge=0.0,\n        le=100.0,\n    )\n\n    outcome_accuracy: float = Field(\n        ge=0.0,\n        le=100.0,\n    )\n\n    abstention_accuracy: float = Field(\n        ge=0.0,\n        le=100.0,\n    )\n\n    sufficient_evidence_accuracy: float = Field(\n        ge=0.0,\n        le=100.0,\n    )\n\n    average_tool_calls: float = Field(\n        ge=0.0,\n    )\n\n    scenarios: list[\n        ScenarioScore\n    ]\n\n    strongest_signals: list[str]\n    weakest_signals: list[str]\n\n\nclass BenchmarkProbeExecutor:\n    """\n    Synthetic evidence backend for model-intelligence evaluation.\n\n    The model sees only the evidence corresponding to probes it chose.\n    Hidden labels remain inside BenchmarkScenario and never cross this class\n    into EvidenceItem.\n    """\n\n    def __init__(\n        self,\n        scenario: BenchmarkScenario,\n        *,\n        observed_at: datetime,\n    ) -> None:\n        self.scenario = scenario\n        self.observed_at = observed_at\n        self.calls: list[\n            InvestigationProbe\n        ] = []\n\n    async def collect(\n        self,\n        context,\n        scope,\n        probe,\n    ) -> EvidenceItem:\n        self.calls.append(\n            probe\n        )\n\n        value = (\n            self.scenario\n            .evidence_by_probe\n            .get(\n                probe\n            )\n        )\n\n        if isinstance(\n            value,\n            str,\n        ):\n            raise RuntimeError(\n                "Benchmark probe unavailable"\n            )\n\n        if value is None:\n            raise RuntimeError(\n                "Benchmark probe has no observation"\n            )\n\n        source = (\n            "kubernetes"\n            if probe\n            == InvestigationProbe.KUBERNETES_POD_STATE\n            else "prometheus"\n        )\n\n        return EvidenceItem(\n            evidence_id=(\n                f"{self.scenario.key}:"\n                f"{probe.value}"\n            ),\n            probe=probe,\n            source=source,\n            success=True,\n            trusted=True,\n            production_signal=True,\n            reliability=1.0,\n            observed_at=self.observed_at,\n            facts=dict(\n                value\n            ),\n        )\n\n\nclass TracingReasoner(\n    BaseInvestigationReasoner\n):\n    """\n    Transparent delegate that records the actual Agent decisions.\n\n    It does not modify prompts, decisions, state or provider behavior.\n    """\n\n    def __init__(\n        self,\n        delegate: BaseInvestigationReasoner,\n    ) -> None:\n        if not isinstance(\n            delegate,\n            BaseInvestigationReasoner,\n        ):\n            raise TypeError(\n                "Benchmark delegate reasoner is invalid"\n            )\n\n        self.delegate = delegate\n\n        self.decisions: list[\n            InvestigationDecision\n        ] = []\n\n        self.states: list[\n            InvestigationState\n        ] = []\n\n    async def decide(\n        self,\n        scope,\n        state,\n    ) -> InvestigationDecision:\n        self.states.append(\n            state.model_copy(\n                deep=True\n            )\n        )\n\n        decision = await (\n            self.delegate.decide(\n                scope,\n                state,\n            )\n        )\n\n        self.decisions.append(\n            decision.model_copy(\n                deep=True\n            )\n        )\n\n        return decision\n\n\ndef _context(\n    scenario: BenchmarkScenario,\n):\n    return SimpleNamespace(\n        event=SimpleNamespace(\n            signal=SimpleNamespace(\n                name=scenario.alert_name,\n                message=(\n                    scenario.alert_message\n                ),\n            ),\n            resources=[\n                SimpleNamespace(\n                    name=scenario.resource,\n                    namespace=scenario.namespace,\n                    cluster=scenario.cluster,\n                )\n            ],\n        ),\n        metadata={},\n        variables={},\n    )\n\n\ndef _normalize_text(\n    value: str | None,\n) -> str:\n    if not value:\n        return ""\n\n    return (\n        value\n        .strip()\n        .lower()\n    )\n\n\ndef _all_reasoner_text(\n    decisions: list[\n        InvestigationDecision\n    ],\n) -> str:\n    fragments: list[\n        str\n    ] = []\n\n    for decision in decisions:\n        fragments.append(\n            decision.rationale_summary\n        )\n\n        for hypothesis in decision.hypotheses:\n            fragments.append(\n                hypothesis.cause\n            )\n\n            fragments.extend(\n                hypothesis.missing_evidence\n            )\n\n        if decision.conclusion is not None:\n            fragments.append(\n                decision.conclusion.root_cause\n            )\n\n            fragments.extend(\n                decision.conclusion.remaining_uncertainties\n            )\n\n    return _normalize_text(\n        "\\n".join(\n            fragments\n        )\n    )\n\n\ndef _keyword_groups_match(\n    text: str,\n    groups: list[\n        list[str]\n    ],\n) -> bool:\n    normalized = _normalize_text(\n        text\n    )\n\n    if not groups:\n        return True\n\n    for group in groups:\n        if not any(\n            _normalize_text(\n                token\n            )\n            in normalized\n            for token in group\n        ):\n            return False\n\n    return True\n\n\ndef _decision_trace(\n    decisions: list[\n        InvestigationDecision\n    ],\n) -> list[\n    dict[str, Any]\n]:\n    trace = []\n\n    for index, decision in enumerate(\n        decisions,\n        start=1,\n    ):\n        trace.append(\n            {\n                "iteration": index,\n                "hypotheses": [\n                    {\n                        "hypothesis_id": (\n                            item.hypothesis_id\n                        ),\n                        "cause": item.cause,\n                        "confidence": (\n                            item.confidence\n                        ),\n                        "supporting_evidence_ids": list(\n                            item.supporting_evidence_ids\n                        ),\n                        "conflicting_evidence_ids": list(\n                            item.conflicting_evidence_ids\n                        ),\n                        "missing_evidence": list(\n                            item.missing_evidence\n                        ),\n                    }\n                    for item in decision.hypotheses\n                ],\n                "rationale_summary": (\n                    decision.rationale_summary\n                ),\n                "stop": decision.stop,\n                "stop_reason": (\n                    decision.stop_reason.value\n                    if decision.stop_reason\n                    is not None\n                    else None\n                ),\n                "next_probe": (\n                    decision.next_probe.value\n                    if decision.next_probe\n                    is not None\n                    else None\n                ),\n                "conclusion": (\n                    decision.conclusion.model_dump(\n                        mode="json"\n                    )\n                    if decision.conclusion\n                    is not None\n                    else None\n                ),\n            }\n        )\n\n    return trace\n\n\ndef score_scenario(\n    *,\n    scenario: BenchmarkScenario,\n    state: InvestigationState,\n    decisions: list[\n        InvestigationDecision\n    ],\n) -> ScenarioScore:\n    attempted = list(state.attempted_probes)\n    expected_stop = scenario.hidden_expected_stop_reason\n\n    legitimate_terminal = (\n        state.status.value == "concluded"\n        and state.stop_reason == expected_stop\n    )\n    outcome_correct = legitimate_terminal\n\n    if expected_stop == InvestigationStopReason.SUFFICIENT_EVIDENCE:\n        if not legitimate_terminal or state.conclusion is None:\n            grounding_correct = False\n        else:\n            trusted_ids = {\n                item.evidence_id\n                for item in state.evidence\n                if (\n                    item.success\n                    and item.trusted\n                    and item.production_signal\n                )\n            }\n            conclusion_ids = set(state.conclusion.evidence_ids)\n            grounding_correct = (\n                bool(conclusion_ids)\n                and conclusion_ids.issubset(trusted_ids)\n            )\n    else:\n        grounding_correct = (\n            legitimate_terminal\n            and state.conclusion is None\n        )\n\n    required = set(scenario.hidden_required_probes)\n    attempted_set = set(attempted)\n    required_probe_coverage = (\n        len(required & attempted_set) / len(required)\n        if required\n        else 1.0\n    )\n\n    if scenario.hidden_preferred_first_probes:\n        first_probe_quality = (\n            bool(attempted)\n            and attempted[0]\n            in scenario.hidden_preferred_first_probes\n        )\n    else:\n        first_probe_quality = None\n\n    max_calls = scenario.hidden_max_reasonable_tool_calls\n    if max_calls <= 0:\n        tool_efficiency = 1.0 if state.tool_call_count == 0 else 0.0\n    elif state.tool_call_count <= max_calls:\n        tool_efficiency = 1.0\n    else:\n        tool_efficiency = max(\n            0.0,\n            1.0 - (\n                state.tool_call_count - max_calls\n            ) / max_calls,\n        )\n\n    if expected_stop == InvestigationStopReason.SUFFICIENT_EVIDENCE:\n        root_cause_or_abstention_correct = (\n            legitimate_terminal\n            and state.conclusion is not None\n            and _keyword_groups_match(\n                state.conclusion.root_cause,\n                scenario.hidden_root_cause_keyword_groups,\n            )\n        )\n    else:\n        root_cause_or_abstention_correct = (\n            legitimate_terminal\n            and state.conclusion is None\n        )\n\n    if scenario.hidden_missing_capability_keywords:\n        reasoner_text = _all_reasoner_text(decisions)\n        missing_capability_awareness = any(\n            _normalize_text(keyword) in reasoner_text\n            for keyword\n            in scenario.hidden_missing_capability_keywords\n        )\n    else:\n        missing_capability_awareness = None\n\n    score = 0.0\n    score += 30.0 if outcome_correct else 0.0\n    score += 20.0 if grounding_correct else 0.0\n\n    probe_weight = 30.0 if first_probe_quality is None else 20.0\n    score += required_probe_coverage * probe_weight\n\n    if first_probe_quality is not None:\n        score += 10.0 if first_probe_quality else 0.0\n\n    score += tool_efficiency * 10.0\n    score += 10.0 if root_cause_or_abstention_correct else 0.0\n\n    notes: list[str] = []\n\n    if not outcome_correct:\n        notes.append(\n            "Final stop reason/status did not match the hidden evaluator label."\n        )\n\n    if state.status.value == "failed":\n        notes.append(\n            "Failed investigation is not counted as a valid abstention."\n        )\n\n    if (\n        expected_stop != InvestigationStopReason.SUFFICIENT_EVIDENCE\n        and state.conclusion is not None\n    ):\n        notes.append(\n            "Agent produced an RCA where the benchmark expected abstention."\n        )\n\n    if missing_capability_awareness is False:\n        notes.append(\n            "Agent did not explicitly recognize the expected missing capability."\n        )\n\n    return ScenarioScore(\n        scenario_key=scenario.key,\n        title=scenario.title,\n        expected_stop_reason=expected_stop.value,\n        score=round(\n            min(100.0, max(0.0, score)),\n            1,\n        ),\n        outcome_correct=outcome_correct,\n        grounding_correct=grounding_correct,\n        required_probe_coverage=round(\n            required_probe_coverage,\n            3,\n        ),\n        first_probe_quality=first_probe_quality,\n        tool_efficiency=round(\n            tool_efficiency,\n            3,\n        ),\n        root_cause_or_abstention_correct=(\n            root_cause_or_abstention_correct\n        ),\n        missing_capability_awareness=(\n            missing_capability_awareness\n        ),\n        final_status=state.status.value,\n        final_stop_reason=(\n            state.stop_reason.value\n            if state.stop_reason is not None\n            else None\n        ),\n        failure_code=state.failure_code,\n        attempted_probes=[\n            item.value\n            for item in attempted\n        ],\n        tool_call_count=state.tool_call_count,\n        iteration_count=state.iteration_count,\n        conclusion_root_cause=(\n            state.conclusion.root_cause\n            if state.conclusion is not None\n            else None\n        ),\n        conclusion_confidence=(\n            state.conclusion.confidence\n            if state.conclusion is not None\n            else None\n        ),\n        decision_trace=_decision_trace(decisions),\n        notes=notes,\n    )\n\n\nasync def run_scenario(\n    *,\n    reasoner: BaseInvestigationReasoner,\n    scenario: BenchmarkScenario,\n    limits: InvestigationLimits,\n    observed_at: datetime,\n) -> ScenarioScore:\n    tracing = TracingReasoner(\n        reasoner\n    )\n\n    probes = BenchmarkProbeExecutor(\n        scenario,\n        observed_at=observed_at,\n    )\n\n    coordinator = (\n        EvidenceDrivenInvestigationCoordinator(\n            reasoner=tracing,\n            probe_executor=probes,\n            limits=limits,\n            utc_clock=lambda: observed_at,\n        )\n    )\n\n    state = await coordinator.investigate(\n        _context(\n            scenario\n        )\n    )\n\n    return score_scenario(\n        scenario=scenario,\n        state=state,\n        decisions=tracing.decisions,\n    )\n\n\ndef build_bailian_reasoner(\n    *,\n    provider_name: str,\n    limits: InvestigationLimits,\n) -> BaseInvestigationReasoner:\n    runtime = (\n        create_historical_llm_runtime(\n            limits=limits,\n            provider_name=provider_name,\n        )\n    )\n\n    coordinator = getattr(\n        runtime,\n        "investigation_coordinator",\n        None,\n    )\n\n    reasoner = getattr(\n        coordinator,\n        "reasoner",\n        None,\n    )\n\n    if not isinstance(\n        reasoner,\n        BaseInvestigationReasoner,\n    ):\n        raise IntelligenceBenchmarkError(\n            "Benchmark could not obtain the canonical Investigation reasoner"\n        )\n\n    return reasoner\n\n\ndef build_report(\n    *,\n    provider: str,\n    mode: str,\n    scenarios: list[\n        ScenarioScore\n    ],\n) -> IntelligenceBenchmarkReport:\n    if not scenarios:\n        raise IntelligenceBenchmarkError(\n            "Benchmark produced no scenario results"\n        )\n\n    overall_score = (\n        sum(item.score for item in scenarios)\n        / len(scenarios)\n    )\n\n    outcome_accuracy = (\n        sum(\n            1\n            for item in scenarios\n            if item.outcome_correct\n        )\n        / len(scenarios)\n        * 100.0\n    )\n\n    expected_abstention_cases = [\n        item\n        for item in scenarios\n        if item.expected_stop_reason\n        != InvestigationStopReason.SUFFICIENT_EVIDENCE.value\n    ]\n\n    abstention_accuracy = (\n        sum(\n            1\n            for item in expected_abstention_cases\n            if (\n                item.outcome_correct\n                and item.root_cause_or_abstention_correct\n            )\n        )\n        / len(expected_abstention_cases)\n        * 100.0\n        if expected_abstention_cases\n        else 0.0\n    )\n\n    expected_sufficient_cases = [\n        item\n        for item in scenarios\n        if item.expected_stop_reason\n        == InvestigationStopReason.SUFFICIENT_EVIDENCE.value\n    ]\n\n    sufficient_evidence_accuracy = (\n        sum(\n            1\n            for item in expected_sufficient_cases\n            if (\n                item.outcome_correct\n                and item.grounding_correct\n                and item.root_cause_or_abstention_correct\n            )\n        )\n        / len(expected_sufficient_cases)\n        * 100.0\n        if expected_sufficient_cases\n        else 0.0\n    )\n\n    average_tool_calls = (\n        sum(\n            item.tool_call_count\n            for item in scenarios\n        )\n        / len(scenarios)\n    )\n\n    ordered = sorted(\n        scenarios,\n        key=lambda item: (\n            item.score,\n            item.scenario_key,\n        ),\n    )\n\n    weakest = [\n        f"{item.scenario_key}: {item.score:.1f}/100"\n        for item in ordered[:3]\n    ]\n\n    strongest = [\n        f"{item.scenario_key}: {item.score:.1f}/100"\n        for item in reversed(ordered[-3:])\n    ]\n\n    return IntelligenceBenchmarkReport(\n        generated_at=datetime.now(UTC),\n        provider=provider,\n        mode=mode,\n        scenario_count=len(scenarios),\n        overall_score=round(\n            overall_score,\n            1,\n        ),\n        outcome_accuracy=round(\n            outcome_accuracy,\n            1,\n        ),\n        abstention_accuracy=round(\n            abstention_accuracy,\n            1,\n        ),\n        sufficient_evidence_accuracy=round(\n            sufficient_evidence_accuracy,\n            1,\n        ),\n        average_tool_calls=round(\n            average_tool_calls,\n            2,\n        ),\n        scenarios=scenarios,\n        strongest_signals=strongest,\n        weakest_signals=weakest,\n    )\n\n\ndef render_report(\n    report: IntelligenceBenchmarkReport,\n) -> str:\n    lines = [\n        "=" * 96,\n        "INVESTIGATION INTELLIGENCE BENCHMARK v1",\n        "=" * 96,\n        "",\n        f"GeneratedAt: {report.generated_at.isoformat()}",\n        f"Provider: {report.provider}",\n        f"Mode: {report.mode}",\n        f"Scenarios: {report.scenario_count}",\n        "",\n        f"OverallScore: {report.overall_score:.1f}/100",\n        f"OutcomeAccuracy: {report.outcome_accuracy:.1f}%",\n        f"AbstentionAccuracy: {report.abstention_accuracy:.1f}%",\n        (\n            "SufficientEvidenceAccuracy: "\n            f"{report.sufficient_evidence_accuracy:.1f}%"\n        ),\n        f"AverageToolCalls: {report.average_tool_calls:.2f}",\n        "",\n        "Important:",\n        "- This is a controlled synthetic-evidence intelligence benchmark.",\n        "- The actual LLM Investigation reasoner is used in live mode.",\n        "- Hidden evaluator labels never enter the Agent prompt.",\n        "- This is stronger than unit testing but is not a production validation.",\n        "",\n        "SCENARIOS",\n        "-" * 96,\n    ]\n\n    for item in report.scenarios:\n        lines.extend(\n            [\n                "",\n                (\n                    f"[{item.score:5.1f}] "\n                    f"{item.scenario_key} - {item.title}"\n                ),\n                (\n                    "  outcome_correct="\n                    f"{item.outcome_correct}"\n                ),\n                (\n                    "  grounding_correct="\n                    f"{item.grounding_correct}"\n                ),\n                (\n                    "  required_probe_coverage="\n                    f"{item.required_probe_coverage:.3f}"\n                ),\n                (\n                    "  first_probe_quality="\n                    f"{item.first_probe_quality}"\n                ),\n                (\n                    "  tool_efficiency="\n                    f"{item.tool_efficiency:.3f}"\n                ),\n                (\n                    "  root_cause_or_abstention_correct="\n                    f"{item.root_cause_or_abstention_correct}"\n                ),\n                (\n                    "  missing_capability_awareness="\n                    f"{item.missing_capability_awareness}"\n                ),\n                (\n                    "  expected_stop_reason="\n                    f"{item.expected_stop_reason}"\n                ),\n                (\n                    "  final="\n                    f"{item.final_status}/"\n                    f"{item.final_stop_reason}"\n                ),\n                (\n                    "  failure_code="\n                    f"{item.failure_code}"\n                ),\n                (\n                    "  probes="\n                    + ", ".join(\n                        item.attempted_probes\n                    )\n                ),\n                (\n                    "  conclusion="\n                    + (\n                        item.conclusion_root_cause\n                        or "<NONE>"\n                    )\n                ),\n                (\n                    "  confidence="\n                    + (\n                        str(\n                            item.conclusion_confidence\n                        )\n                        if item.conclusion_confidence\n                        is not None\n                        else "<NONE>"\n                    )\n                ),\n            ]\n        )\n\n        for note in item.notes:\n            lines.append(\n                f"  note: {note}"\n            )\n\n        lines.append(\n            "  decision_trace:"\n        )\n\n        for decision in item.decision_trace:\n            lines.append(\n                "    "\n                + json.dumps(\n                    decision,\n                    ensure_ascii=False,\n                    sort_keys=True,\n                )\n            )\n\n    lines.extend(\n        [\n            "",\n            "STRONGEST",\n            "-" * 96,\n            *[\n                f"- {value}"\n                for value\n                in report.strongest_signals\n            ],\n            "",\n            "WEAKEST",\n            "-" * 96,\n            *[\n                f"- {value}"\n                for value\n                in report.weakest_signals\n            ],\n            "",\n            "=" * 96,\n        ]\n    )\n\n    return "\\n".join(\n        lines\n    ) + "\\n"\n\n\n__all__ = [\n    "BenchmarkProbeExecutor",\n    "BenchmarkScenario",\n    "IntelligenceBenchmarkError",\n    "IntelligenceBenchmarkReport",\n    "ScenarioScore",\n    "TracingReasoner",\n    "build_bailian_reasoner",\n    "build_report",\n    "render_report",\n    "run_scenario",\n    "score_scenario",\n]\n'
BENCHMARK_TEST_SOURCE = 'from __future__ import annotations\n\nimport asyncio\nfrom datetime import UTC, datetime\n\nfrom services.agent_runtime.app.evaluation.intelligence_benchmark.engine import (\n    BenchmarkScenario,\n    TracingReasoner,\n    build_report,\n    run_scenario,\n)\nfrom services.agent_runtime.app.evaluation.intelligence_benchmark.scenarios import (\n    SCENARIOS,\n    scenarios_for_mode,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    IncidentHypothesis,\n    InvestigationConclusion,\n    InvestigationDecision,\n    InvestigationLimits,\n    InvestigationProbe,\n    InvestigationScope,\n    InvestigationState,\n    InvestigationStatus,\n    InvestigationStopReason,\n)\nfrom services.agent_runtime.app.investigation.reasoner import (\n    BaseInvestigationReasoner,\n)\n\n\nNOW = datetime(\n    2026,\n    8,\n    10,\n    8,\n    45,\n    tzinfo=UTC,\n)\n\n\nclass ScriptedReasoner(\n    BaseInvestigationReasoner\n):\n    def __init__(\n        self,\n        decisions,\n    ):\n        self.decisions = list(\n            decisions\n        )\n\n    async def decide(\n        self,\n        scope,\n        state,\n    ):\n        return self.decisions.pop(\n            0\n        )\n\n\ndef hypothesis(\n    confidence,\n    *,\n    supporting=None,\n    missing=None,\n):\n    return IncidentHypothesis(\n        hypothesis_id="memory",\n        cause=(\n            "Container memory limit pressure"\n        ),\n        confidence=confidence,\n        supporting_evidence_ids=(\n            supporting\n            or []\n        ),\n        missing_evidence=(\n            missing\n            or []\n        ),\n    )\n\n\ndef test_smoke_mode_contains_three_scenarios():\n    scenarios = scenarios_for_mode(\n        "smoke"\n    )\n\n    assert len(\n        scenarios\n    ) == 3\n\n    assert {\n        item.key\n        for item in scenarios\n    } == {\n        "oom_limit_pressure",\n        "crashloop_not_memory",\n        "conflicting_oom_signal",\n    }\n\n\ndef test_hidden_labels_are_not_in_evidence_payloads():\n    for scenario in SCENARIOS:\n        serialized = str(\n            scenario.evidence_by_probe\n        ).lower()\n\n        assert (\n            "hidden_expected"\n            not in serialized\n        )\n\n        assert (\n            "hidden_root_cause"\n            not in serialized\n        )\n\n\ndef test_tracing_reasoner_is_transparent():\n    decision = InvestigationDecision(\n        hypotheses=[\n            hypothesis(\n                0.3\n            )\n        ],\n        rationale_summary=(\n            "inspect pod"\n        ),\n        next_probe=(\n            InvestigationProbe.KUBERNETES_POD_STATE\n        ),\n    )\n\n    delegate = ScriptedReasoner(\n        [\n            decision\n        ]\n    )\n\n    tracing = TracingReasoner(\n        delegate\n    )\n\n    scope = InvestigationScope(\n        alert_name="PodOOMKilled",\n        alert_message="Pod restarted",\n        resource="payment-api",\n        namespace="payment",\n        cluster="benchmark-lab",\n    )\n\n    state = InvestigationState(\n        scope=scope\n    )\n\n    result = asyncio.run(\n        tracing.decide(\n            scope,\n            state,\n        )\n    )\n\n    assert result == decision\n    assert tracing.decisions == [\n        decision\n    ]\n\n\ndef test_clear_oom_scenario_scores_high_with_correct_reasoning():\n    scenario = next(\n        item\n        for item in SCENARIOS\n        if item.key\n        == "oom_limit_pressure"\n    )\n\n    pod_id = (\n        f"{scenario.key}:"\n        f"{InvestigationProbe.KUBERNETES_POD_STATE.value}"\n    )\n\n    limit_id = (\n        f"{scenario.key}:"\n        f"{InvestigationProbe.PROMETHEUS_MEMORY_LIMIT.value}"\n    )\n\n    reasoner = ScriptedReasoner(\n        [\n            InvestigationDecision(\n                hypotheses=[\n                    hypothesis(\n                        0.4,\n                        missing=[\n                            "pod state"\n                        ],\n                    )\n                ],\n                rationale_summary=(\n                    "inspect pod"\n                ),\n                next_probe=(\n                    InvestigationProbe.KUBERNETES_POD_STATE\n                ),\n            ),\n            InvestigationDecision(\n                hypotheses=[\n                    hypothesis(\n                        0.75,\n                        supporting=[\n                            pod_id\n                        ],\n                        missing=[\n                            "memory limit"\n                        ],\n                    )\n                ],\n                rationale_summary=(\n                    "oom found; inspect limit"\n                ),\n                next_probe=(\n                    InvestigationProbe.PROMETHEUS_MEMORY_LIMIT\n                ),\n            ),\n            InvestigationDecision(\n                hypotheses=[\n                    hypothesis(\n                        0.95,\n                        supporting=[\n                            pod_id,\n                            limit_id,\n                        ],\n                    )\n                ],\n                rationale_summary=(\n                    "evidence sufficient"\n                ),\n                stop=True,\n                stop_reason=(\n                    InvestigationStopReason.SUFFICIENT_EVIDENCE\n                ),\n                conclusion=(\n                    InvestigationConclusion(\n                        root_cause=(\n                            "Container memory limit pressure caused OOM"\n                        ),\n                        confidence=0.95,\n                        evidence_ids=[\n                            pod_id,\n                            limit_id,\n                        ],\n                    )\n                ),\n            ),\n        ]\n    )\n\n    score = asyncio.run(\n        run_scenario(\n            reasoner=reasoner,\n            scenario=scenario,\n            limits=InvestigationLimits(\n                max_iterations=5,\n                max_tool_calls=4,\n                timeout_seconds=10,\n            ),\n            observed_at=NOW,\n        )\n    )\n\n    assert score.score >= 90\n    assert score.outcome_correct is True\n    assert score.grounding_correct is True\n\n\ndef test_abstention_scenario_penalizes_fabricated_rca():\n    scenario = next(\n        item\n        for item in SCENARIOS\n        if item.key\n        == "crashloop_not_memory"\n    )\n\n    pod_id = (\n        f"{scenario.key}:"\n        f"{InvestigationProbe.KUBERNETES_POD_STATE.value}"\n    )\n\n    reasoner = ScriptedReasoner(\n        [\n            InvestigationDecision(\n                hypotheses=[\n                    hypothesis(\n                        0.95\n                    )\n                ],\n                rationale_summary=(\n                    "guess root cause"\n                ),\n                next_probe=(\n                    InvestigationProbe.KUBERNETES_POD_STATE\n                ),\n            ),\n            InvestigationDecision(\n                hypotheses=[\n                    hypothesis(\n                        0.95,\n                        supporting=[\n                            pod_id\n                        ],\n                    )\n                ],\n                rationale_summary=(\n                    "incorrectly stop"\n                ),\n                stop=True,\n                stop_reason=(\n                    InvestigationStopReason.SUFFICIENT_EVIDENCE\n                ),\n                conclusion=(\n                    InvestigationConclusion(\n                        root_cause=(\n                            "Memory limit caused restart"\n                        ),\n                        confidence=0.95,\n                        evidence_ids=[\n                            pod_id\n                        ],\n                    )\n                ),\n            ),\n        ]\n    )\n\n    score = asyncio.run(\n        run_scenario(\n            reasoner=reasoner,\n            scenario=scenario,\n            limits=InvestigationLimits(\n                max_iterations=5,\n                max_tool_calls=4,\n                timeout_seconds=10,\n            ),\n            observed_at=NOW,\n        )\n    )\n\n    assert score.outcome_correct is False\n    assert (\n        score.root_cause_or_abstention_correct\n        is False\n    )\n    assert score.score < 70\n\n\ndef test_build_report_aggregates_scenarios():\n    scenario = BenchmarkScenario(\n        key="unit",\n        title="unit",\n        alert_name="Unit",\n        alert_message="Unit",\n        evidence_by_probe={},\n        hidden_expected_stop_reason=(\n            InvestigationStopReason.INSUFFICIENT_EVIDENCE\n        ),\n    )\n\n    reasoner = ScriptedReasoner(\n        [\n            InvestigationDecision(\n                hypotheses=[\n                    hypothesis(\n                        0.1\n                    )\n                ],\n                rationale_summary=(\n                    "insufficient"\n                ),\n                stop=True,\n                stop_reason=(\n                    InvestigationStopReason.INSUFFICIENT_EVIDENCE\n                ),\n            )\n        ]\n    )\n\n    score = asyncio.run(\n        run_scenario(\n            reasoner=reasoner,\n            scenario=scenario,\n            limits=InvestigationLimits(\n                max_iterations=3,\n                max_tool_calls=2,\n                timeout_seconds=10,\n            ),\n            observed_at=NOW,\n        )\n    )\n\n    report = build_report(\n        provider="unit",\n        mode="unit",\n        scenarios=[\n            score\n        ],\n    )\n\n    assert report.scenario_count == 1\n    assert report.outcome_accuracy == 100.0\n    assert report.overall_score >= 90\n\n\ndef test_failed_reasoner_is_not_counted_as_abstention():\n    scenario = BenchmarkScenario(\n        key="failed-abstention",\n        title="failed-abstention",\n        alert_name="PodRestartHigh",\n        alert_message="restart",\n        evidence_by_probe={},\n        hidden_expected_stop_reason=(\n            InvestigationStopReason.INSUFFICIENT_EVIDENCE\n        ),\n    )\n\n    state = InvestigationState(\n        status=InvestigationStatus.FAILED,\n        scope=InvestigationScope(\n            alert_name="PodRestartHigh",\n            alert_message="restart",\n            resource="payment-api",\n            namespace="payment",\n            cluster="benchmark-lab",\n        ),\n        stop_reason=InvestigationStopReason.REASONER_ERROR,\n        failure_code="InvestigationReasonerError",\n    )\n\n    from services.agent_runtime.app.evaluation.intelligence_benchmark.engine import (\n        score_scenario,\n    )\n\n    score = score_scenario(\n        scenario=scenario,\n        state=state,\n        decisions=[],\n    )\n\n    assert score.outcome_correct is False\n    assert score.grounding_correct is False\n    assert score.root_cause_or_abstention_correct is False\n    assert score.failure_code == "InvestigationReasonerError"\n\n    report = build_report(\n        provider="unit",\n        mode="unit",\n        scenarios=[score],\n    )\n\n    assert report.abstention_accuracy == 0.0\n    assert report.outcome_accuracy == 0.0\n'
TERMINAL_TEST_SOURCE = 'from __future__ import annotations\n\nfrom datetime import UTC, datetime\n\nfrom services.agent_runtime.app.investigation.models import (\n    EvidenceItem,\n    InvestigationProbe,\n    InvestigationScope,\n    InvestigationState,\n)\nfrom services.agent_runtime.app.investigation.reasoner import (\n    LLMInvestigationReasoner,\n)\n\n\ndef test_reasoner_prompt_contains_terminal_contracts():\n    scope = InvestigationScope(\n        alert_name="PodOOMKilled",\n        alert_message="Pod restarted",\n        resource="payment-api",\n        namespace="payment",\n        cluster="benchmark-lab",\n    )\n    state = InvestigationState(scope=scope)\n\n    prompt = LLMInvestigationReasoner._build_prompt(\n        scope=scope,\n        state=state,\n    )\n\n    assert "sufficient_evidence" in prompt\n    assert "insufficient_evidence" in prompt\n    assert "no_safe_probe" in prompt\n    assert "Terminal sufficient-evidence shape" in prompt\n    assert "Terminal insufficient/no-safe-probe shape" in prompt\n    assert "Never repeat a probe already listed in attempted_probes" in prompt\n    assert "Never cite an evidence ID that is absent from State.evidence" in prompt\n\n\ndef test_reasoner_prompt_lists_trusted_evidence_ids():\n    scope = InvestigationScope(\n        alert_name="PodOOMKilled",\n        alert_message="Pod restarted",\n        resource="payment-api",\n        namespace="payment",\n        cluster="benchmark-lab",\n    )\n\n    state = InvestigationState(\n        scope=scope,\n        evidence=[\n            EvidenceItem(\n                evidence_id="known-evidence-001",\n                probe=InvestigationProbe.KUBERNETES_POD_STATE,\n                source="kubernetes",\n                success=True,\n                trusted=True,\n                production_signal=True,\n                reliability=1.0,\n                observed_at=datetime(\n                    2026, 8, 10, 9, 0, tzinfo=UTC\n                ),\n                facts={"oom_killed": True},\n            )\n        ],\n    )\n\n    prompt = LLMInvestigationReasoner._build_prompt(\n        scope=scope,\n        state=state,\n    )\n\n    assert "trusted_evidence_ids" in prompt\n    assert "known-evidence-001" in prompt\n'
OLD_REASONER_BLOCK = '        state_payload = {\n            "scope": scope.model_dump(\n                mode="json"\n            ),\n            "iteration_count": (\n                state.iteration_count\n            ),\n            "tool_call_count": (\n                state.tool_call_count\n            ),\n            "attempted_probes": [\n                probe.value\n                for probe in state.attempted_probes\n            ],\n            "hypotheses": [\n                item.model_dump(\n                    mode="json"\n                )\n                for item in state.hypotheses\n            ],\n            "evidence": [\n                item.model_dump(\n                    mode="json"\n                )\n                for item in state.evidence\n            ],\n            "allowed_probes": [\n                probe.value\n                for probe in InvestigationProbe\n            ],\n        }\n\n        return (\n            "Investigate the incident using the bounded state below.\\n"\n            "Return JSON only. Do not return markdown.\\n"\n            "A continuing decision requires next_probe and stop=false.\\n"\n            "A terminal decision requires stop=true, stop_reason, and no "\n            "next_probe. sufficient_evidence also requires conclusion.\\n"\n            "The response schema is:\\n"\n            "{\\n"\n            \'  "hypotheses": [{"hypothesis_id": "", "cause": "", \'\n            \'"confidence": 0.0, "supporting_evidence_ids": [], \'\n            \'"conflicting_evidence_ids": [], "missing_evidence": []}],\\n\'\n            \'  "rationale_summary": "bounded explanation",\\n\'\n            \'  "stop": false,\\n\'\n            \'  "stop_reason": null,\\n\'\n            \'  "next_probe": "kubernetes_pod_state",\\n\'\n            \'  "conclusion": null\\n\'\n            "}\\n"\n            "State:\\n"\n            f"{json.dumps(state_payload, ensure_ascii=True, sort_keys=True)}"\n        )\n'
NEW_REASONER_BLOCK = '        trusted_evidence_ids = [\n            item.evidence_id\n            for item in state.evidence\n            if (\n                item.success\n                and item.trusted\n                and item.production_signal\n            )\n        ]\n\n        state_payload = {\n            "scope": scope.model_dump(\n                mode="json"\n            ),\n            "iteration_count": state.iteration_count,\n            "tool_call_count": state.tool_call_count,\n            "attempted_probes": [\n                probe.value\n                for probe in state.attempted_probes\n            ],\n            "hypotheses": [\n                item.model_dump(mode="json")\n                for item in state.hypotheses\n            ],\n            "evidence": [\n                item.model_dump(mode="json")\n                for item in state.evidence\n            ],\n            "trusted_evidence_ids": trusted_evidence_ids,\n            "allowed_probes": [\n                probe.value\n                for probe in InvestigationProbe\n            ],\n        }\n\n        return (\n            "Investigate the incident using the bounded state below.\\n"\n            "Return exactly one JSON object only. Do not return markdown.\\n"\n            "Maintain competing hypotheses and update confidence from evidence.\\n"\n            "Never repeat a probe already listed in attempted_probes.\\n"\n            "Never cite an evidence ID that is absent from State.evidence.\\n"\n            "For a conclusion, evidence_ids must be non-empty and copied "\n            "exactly from trusted_evidence_ids.\\n"\n            "Allowed model terminal stop_reason values are exactly: "\n            "sufficient_evidence, insufficient_evidence, no_safe_probe.\\n"\n            "Do not emit internal coordinator reasons such as timeout, "\n            "max_iterations, max_tool_calls, duplicate_probe or reasoner_error.\\n"\n            "\\n"\n            "Continuing shape:\\n"\n            "{\\n"\n            \'  "hypotheses": [{"hypothesis_id": "h1", "cause": "candidate", \'\n            \'"confidence": 0.5, "supporting_evidence_ids": [], \'\n            \'"conflicting_evidence_ids": [], "missing_evidence": ["needed evidence"]}],\\n\'\n            \'  "rationale_summary": "why this probe is most discriminative",\\n\'\n            \'  "stop": false,\\n\'\n            \'  "stop_reason": null,\\n\'\n            \'  "next_probe": "one unattempted value from allowed_probes",\\n\'\n            \'  "conclusion": null\\n\'\n            "}\\n"\n            "\\n"\n            "Terminal sufficient-evidence shape:\\n"\n            "{\\n"\n            \'  "hypotheses": [{"hypothesis_id": "h1", "cause": "supported cause", \'\n            \'"confidence": 0.9, "supporting_evidence_ids": ["copy exact trusted id"], \'\n            \'"conflicting_evidence_ids": [], "missing_evidence": []}],\\n\'\n            \'  "rationale_summary": "why trusted evidence is sufficient",\\n\'\n            \'  "stop": true,\\n\'\n            \'  "stop_reason": "sufficient_evidence",\\n\'\n            \'  "next_probe": null,\\n\'\n            \'  "conclusion": {"root_cause": "bounded root cause", \'\n            \'"confidence": 0.9, "evidence_ids": ["copy exact trusted id"], \'\n            \'"remaining_uncertainties": []}\\n\'\n            "}\\n"\n            "\\n"\n            "Terminal insufficient/no-safe-probe shape:\\n"\n            "{\\n"\n            \'  "hypotheses": [{"hypothesis_id": "h1", "cause": "candidate", \'\n            \'"confidence": 0.3, "supporting_evidence_ids": [], \'\n            \'"conflicting_evidence_ids": [], "missing_evidence": ["what is still missing"]}],\\n\'\n            \'  "rationale_summary": "why current bounded evidence cannot support an RCA",\\n\'\n            \'  "stop": true,\\n\'\n            \'  "stop_reason": "insufficient_evidence",\\n\'\n            \'  "next_probe": null,\\n\'\n            \'  "conclusion": null\\n\'\n            "}\\n"\n            "\\n"\n            "If no useful unattempted allowed probe can resolve the remaining "\n            "uncertainty, stop with insufficient_evidence or no_safe_probe.\\n"\n            "State:\\n"\n            f"{json.dumps(state_payload, ensure_ascii=True, sort_keys=True)}"\n        )\n'


@dataclass(frozen=True)
class CommandResult:
    name: str
    command: list[str]
    returncode: int
    stdout: str
    stderr: str


def find_repo_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (
            (candidate / "pyproject.toml").exists()
            and (candidate / "services").exists()
            and (candidate / "packages").exists()
        ):
            return candidate
    raise RuntimeError(
        "Repository root not found. Run from inside ai-reliability-platform."
    )


def read_text(path: Path) -> str:
    return path.read_text(
        encoding="utf-8-sig",
        errors="strict",
    )


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    path.write_text(
        text.replace("\r\n", "\n").replace("\r", "\n"),
        encoding="utf-8",
        newline="\n",
    )


def backup_file(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = path.with_name(
        f"{path.name}.before_{VERSION}_{stamp}.bak"
    )
    shutil.copy2(path, backup)
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


def section(lines: list[str], title: str) -> None:
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
    section(lines, f"COMMAND: {result.name}")
    lines.extend(
        [
            " ".join(result.command),
            "",
            f"ExitCode: {result.returncode}",
            "",
            "STDOUT",
            "-" * 120,
            result.stdout.rstrip() or "<EMPTY>",
            "",
            "STDERR",
            "-" * 120,
            result.stderr.rstrip() or "<EMPTY>",
        ]
    )


def patch_reasoner(path: Path) -> None:
    text = read_text(path)

    if "Terminal sufficient-evidence shape" in text:
        raise RuntimeError(
            "Terminal contract hardening already appears installed"
        )

    if text.count(OLD_REASONER_BLOCK) != 1:
        raise RuntimeError(
            "Could not locate exact current Investigation reasoner prompt block"
        )

    updated = text.replace(
        OLD_REASONER_BLOCK,
        NEW_REASONER_BLOCK,
        1,
    )

    ast.parse(updated)
    write_text(path, updated)


def main() -> int:
    root = find_repo_root(
        Path.cwd().resolve()
    )

    after = root / AFTER_NAME
    error = root / ERROR_NAME

    for path in (after, error):
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    reasoner_file = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "investigation"
        / "reasoner.py"
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

    benchmark_test_file = (
        root
        / "services"
        / "agent_runtime"
        / "tests"
        / "test_investigation_intelligence_benchmark.py"
    )

    terminal_test_file = (
        root
        / "services"
        / "agent_runtime"
        / "tests"
        / "test_investigation_terminal_contract.py"
    )

    for required in (
        reasoner_file,
        engine_file,
        benchmark_test_file,
    ):
        if not required.exists():
            raise RuntimeError(
                f"Required file missing: {required}"
            )

    targets = (
        reasoner_file,
        engine_file,
        benchmark_test_file,
        terminal_test_file,
    )

    preexisting = {
        path: path.exists()
        for path in targets
    }
    backups = []

    report = [
        "Investigation Terminal Contract Hardening v1",
        f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
        "",
        "Changes:",
        "- strengthen terminal decision prompt contract",
        "- preserve fail-closed InvestigationDecision validation",
        "- failed/exhausted benchmark runs no longer count as abstention",
        "- benchmark reports expected_stop_reason + sanitized failure_code",
        "- add focused regression tests",
        "",
        "No network request is sent by this installer.",
    ]

    try:
        section(report, "BACKUP")

        for path in targets:
            if path.exists():
                backup = backup_file(path)
                backups.append(
                    (path, backup)
                )
                report.append(
                    "backup="
                    + str(
                        backup.relative_to(root)
                    )
                )

        patch_reasoner(reasoner_file)
        write_text(engine_file, ENGINE_SOURCE)
        write_text(
            benchmark_test_file,
            BENCHMARK_TEST_SOURCE,
        )
        write_text(
            terminal_test_file,
            TERMINAL_TEST_SOURCE,
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
                str(reasoner_file.relative_to(root)),
                str(engine_file.relative_to(root)),
                str(benchmark_test_file.relative_to(root)),
                str(terminal_test_file.relative_to(root)),
            ],
        )
        add_command(report, syntax)

        if syntax.returncode != 0:
            raise RuntimeError(
                "Python syntax verification failed"
            )

        tests = run_command(
            root=root,
            name="Terminal contract + benchmark scoring tests",
            command=[
                "uv",
                "run",
                "pytest",
                str(terminal_test_file.relative_to(root)),
                str(benchmark_test_file.relative_to(root)),
                "services/agent_runtime/tests/test_investigation_reasoner.py",
                "services/agent_runtime/tests/test_investigation_models.py",
                "services/agent_runtime/tests/test_investigation_coordinator.py",
                "-q",
            ],
        )
        add_command(report, tests)

        if tests.returncode != 0:
            raise RuntimeError(
                "Terminal contract/scoring tests failed"
            )

        preflight = run_command(
            root=root,
            name="Terminal contract prompt preflight",
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
                    "print('sufficient=' + "
                    "str('Terminal sufficient-evidence shape' in p)); "
                    "print('insufficient=' + "
                    "str('Terminal insufficient/no-safe-probe shape' in p)); "
                    "print('trusted_ids=' + "
                    "str('trusted_evidence_ids' in p))"
                ),
            ],
        )
        add_command(report, preflight)

        if preflight.returncode != 0:
            raise RuntimeError(
                "Terminal contract prompt preflight failed"
            )

        status = run_command(
            root=root,
            name="Git status",
            command=[
                "git",
                "status",
                "--short",
                "--",
                str(reasoner_file.relative_to(root)),
                str(engine_file.relative_to(root)),
                str(benchmark_test_file.relative_to(root)),
                str(terminal_test_file.relative_to(root)),
            ],
        )
        add_command(report, status)

        section(report, "RESULT")
        report.extend(
            [
                "PASSED",
                "",
                "InvestigationDecision model was not weakened.",
                "Invalid decisions still fail closed.",
                "No remediation/action authority was changed.",
                "",
                "Next:",
                "rerun the same Bailian smoke benchmark.",
            ]
        )

        write_text(
            after,
            "\n".join(report) + "\n",
        )

        print("=" * 72)
        print(
            "INVESTIGATION TERMINAL CONTRACT HARDENING V1 PASSED"
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
                        original.relative_to(root)
                    )
                )
            except Exception as rollback_exc:
                rollback.append(
                    "ROLLBACK FAILED "
                    + str(
                        original.relative_to(root)
                    )
                    + ": "
                    + f"{type(rollback_exc).__name__}: {rollback_exc}"
                )

        for path in targets:
            if (
                not preexisting[path]
                and path.exists()
            ):
                try:
                    path.unlink()
                    rollback.append(
                        "REMOVED newly-created "
                        + str(
                            path.relative_to(root)
                        )
                    )
                except Exception as rollback_exc:
                    rollback.append(
                        "ROLLBACK FAILED removing "
                        + str(
                            path.relative_to(root)
                        )
                        + ": "
                        + f"{type(rollback_exc).__name__}: {rollback_exc}"
                    )

        write_text(
            error,
            "\n".join(
                [
                    "Investigation Terminal Contract Hardening v1 FAILED",
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
            "INVESTIGATION TERMINAL CONTRACT HARDENING V1 FAILED"
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
