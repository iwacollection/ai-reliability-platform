from __future__ import annotations

import shutil
import subprocess
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


VERSION = "investigation-intelligence-benchmark-v2"

AFTER_NAME = (
    "investigation_intelligence_benchmark_v2_after.txt"
)

ERROR_NAME = (
    "investigation_intelligence_benchmark_v2_install_error.txt"
)

ENGINE_SOURCE = 'from __future__ import annotations\n\nimport json\nfrom dataclasses import dataclass\nfrom datetime import UTC, datetime\nfrom types import SimpleNamespace\nfrom typing import Any\n\nfrom pydantic import BaseModel, ConfigDict, Field\n\nfrom services.agent_runtime.app.evaluation.real_incident.llm_run import (\n    create_historical_llm_runtime,\n)\nfrom services.agent_runtime.app.investigation.coordinator import (\n    EvidenceDrivenInvestigationCoordinator,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    EvidenceItem,\n    InvestigationDecision,\n    InvestigationLimits,\n    InvestigationProbe,\n    InvestigationState,\n    InvestigationStopReason,\n)\nfrom services.agent_runtime.app.investigation.reasoner import (\n    BaseInvestigationReasoner,\n)\n\n\nclass IntelligenceBenchmarkError(\n    RuntimeError\n):\n    pass\n\n\nclass BenchmarkScenario(BaseModel):\n    """\n    One hidden-label Investigation exam.\n\n    hidden_* fields are evaluator-only. They never enter the Agent context,\n    InvestigationScope, LLM prompt, EvidenceItem facts or decision history.\n    """\n\n    model_config = ConfigDict(\n        frozen=True,\n        extra="forbid",\n    )\n\n    key: str\n    title: str\n\n    alert_name: str\n    alert_message: str\n\n    resource: str = "payment-api"\n    namespace: str = "payment"\n    cluster: str = "benchmark-lab"\n\n    evidence_by_probe: dict[\n        InvestigationProbe,\n        dict[str, Any] | str,\n    ]\n\n    hidden_expected_stop_reason: (\n        InvestigationStopReason\n    )\n\n    hidden_required_probes: list[\n        InvestigationProbe\n    ] = Field(\n        default_factory=list\n    )\n\n    hidden_preferred_first_probes: list[\n        InvestigationProbe\n    ] = Field(\n        default_factory=list\n    )\n\n    hidden_root_cause_keyword_groups: list[\n        list[str]\n    ] = Field(\n        default_factory=list\n    )\n\n    hidden_missing_capability_keywords: list[\n        str\n    ] = Field(\n        default_factory=list\n    )\n\n    hidden_max_reasonable_tool_calls: int = Field(\n        default=4,\n        ge=0,\n        le=10,\n    )\n\n\nclass ScenarioScore(BaseModel):\n    model_config = ConfigDict(\n        frozen=True,\n        extra="forbid",\n    )\n\n    scenario_key: str\n    title: str\n\n    score: float = Field(\n        ge=0.0,\n        le=100.0,\n    )\n\n    outcome_correct: bool\n    grounding_correct: bool\n    required_probe_coverage: float\n    first_probe_quality: bool | None\n    tool_efficiency: float\n    root_cause_or_abstention_correct: bool\n    missing_capability_awareness: bool | None\n\n    final_status: str\n    final_stop_reason: str | None\n\n    attempted_probes: list[str]\n    tool_call_count: int\n    iteration_count: int\n\n    conclusion_root_cause: str | None\n    conclusion_confidence: float | None\n\n    decision_trace: list[\n        dict[str, Any]\n    ]\n\n    notes: list[str] = Field(\n        default_factory=list\n    )\n\n\nclass IntelligenceBenchmarkReport(BaseModel):\n    model_config = ConfigDict(\n        frozen=True,\n        extra="forbid",\n    )\n\n    schema_version: str = "v1"\n    generated_at: datetime\n\n    provider: str\n    mode: str\n\n    scenario_count: int\n    overall_score: float = Field(\n        ge=0.0,\n        le=100.0,\n    )\n\n    outcome_accuracy: float = Field(\n        ge=0.0,\n        le=100.0,\n    )\n\n    abstention_accuracy: float = Field(\n        ge=0.0,\n        le=100.0,\n    )\n\n    sufficient_evidence_accuracy: float = Field(\n        ge=0.0,\n        le=100.0,\n    )\n\n    average_tool_calls: float = Field(\n        ge=0.0,\n    )\n\n    scenarios: list[\n        ScenarioScore\n    ]\n\n    strongest_signals: list[str]\n    weakest_signals: list[str]\n\n\nclass BenchmarkProbeExecutor:\n    """\n    Synthetic evidence backend for model-intelligence evaluation.\n\n    The model sees only the evidence corresponding to probes it chose.\n    Hidden labels remain inside BenchmarkScenario and never cross this class\n    into EvidenceItem.\n    """\n\n    def __init__(\n        self,\n        scenario: BenchmarkScenario,\n        *,\n        observed_at: datetime,\n    ) -> None:\n        self.scenario = scenario\n        self.observed_at = observed_at\n        self.calls: list[\n            InvestigationProbe\n        ] = []\n\n    async def collect(\n        self,\n        context,\n        scope,\n        probe,\n    ) -> EvidenceItem:\n        self.calls.append(\n            probe\n        )\n\n        value = (\n            self.scenario\n            .evidence_by_probe\n            .get(\n                probe\n            )\n        )\n\n        if isinstance(\n            value,\n            str,\n        ):\n            raise RuntimeError(\n                "Benchmark probe unavailable"\n            )\n\n        if value is None:\n            raise RuntimeError(\n                "Benchmark probe has no observation"\n            )\n\n        source = (\n            "kubernetes"\n            if probe\n            == InvestigationProbe.KUBERNETES_POD_STATE\n            else "prometheus"\n        )\n\n        return EvidenceItem(\n            evidence_id=(\n                f"{self.scenario.key}:"\n                f"{probe.value}"\n            ),\n            probe=probe,\n            source=source,\n            success=True,\n            trusted=True,\n            production_signal=True,\n            reliability=1.0,\n            observed_at=self.observed_at,\n            facts=dict(\n                value\n            ),\n        )\n\n\nclass TracingReasoner(\n    BaseInvestigationReasoner\n):\n    """\n    Transparent delegate that records the actual Agent decisions.\n\n    It does not modify prompts, decisions, state or provider behavior.\n    """\n\n    def __init__(\n        self,\n        delegate: BaseInvestigationReasoner,\n    ) -> None:\n        if not isinstance(\n            delegate,\n            BaseInvestigationReasoner,\n        ):\n            raise TypeError(\n                "Benchmark delegate reasoner is invalid"\n            )\n\n        self.delegate = delegate\n\n        self.decisions: list[\n            InvestigationDecision\n        ] = []\n\n        self.states: list[\n            InvestigationState\n        ] = []\n\n    async def decide(\n        self,\n        scope,\n        state,\n    ) -> InvestigationDecision:\n        self.states.append(\n            state.model_copy(\n                deep=True\n            )\n        )\n\n        decision = await (\n            self.delegate.decide(\n                scope,\n                state,\n            )\n        )\n\n        self.decisions.append(\n            decision.model_copy(\n                deep=True\n            )\n        )\n\n        return decision\n\n\ndef _context(\n    scenario: BenchmarkScenario,\n):\n    return SimpleNamespace(\n        event=SimpleNamespace(\n            signal=SimpleNamespace(\n                name=scenario.alert_name,\n                message=(\n                    scenario.alert_message\n                ),\n            ),\n            resources=[\n                SimpleNamespace(\n                    name=scenario.resource,\n                    namespace=scenario.namespace,\n                    cluster=scenario.cluster,\n                )\n            ],\n        ),\n        metadata={},\n        variables={},\n    )\n\n\ndef _normalize_text(\n    value: str | None,\n) -> str:\n    if not value:\n        return ""\n\n    return (\n        value\n        .strip()\n        .lower()\n    )\n\n\ndef _all_reasoner_text(\n    decisions: list[\n        InvestigationDecision\n    ],\n) -> str:\n    fragments: list[\n        str\n    ] = []\n\n    for decision in decisions:\n        fragments.append(\n            decision.rationale_summary\n        )\n\n        for hypothesis in decision.hypotheses:\n            fragments.append(\n                hypothesis.cause\n            )\n\n            fragments.extend(\n                hypothesis.missing_evidence\n            )\n\n        if decision.conclusion is not None:\n            fragments.append(\n                decision.conclusion.root_cause\n            )\n\n            fragments.extend(\n                decision.conclusion.remaining_uncertainties\n            )\n\n    return _normalize_text(\n        "\\n".join(\n            fragments\n        )\n    )\n\n\ndef _keyword_groups_match(\n    text: str,\n    groups: list[\n        list[str]\n    ],\n) -> bool:\n    normalized = _normalize_text(\n        text\n    )\n\n    if not groups:\n        return True\n\n    for group in groups:\n        if not any(\n            _normalize_text(\n                token\n            )\n            in normalized\n            for token in group\n        ):\n            return False\n\n    return True\n\n\ndef _decision_trace(\n    decisions: list[\n        InvestigationDecision\n    ],\n) -> list[\n    dict[str, Any]\n]:\n    trace = []\n\n    for index, decision in enumerate(\n        decisions,\n        start=1,\n    ):\n        trace.append(\n            {\n                "iteration": index,\n                "hypotheses": [\n                    {\n                        "hypothesis_id": (\n                            item.hypothesis_id\n                        ),\n                        "cause": item.cause,\n                        "confidence": (\n                            item.confidence\n                        ),\n                        "supporting_evidence_ids": list(\n                            item.supporting_evidence_ids\n                        ),\n                        "conflicting_evidence_ids": list(\n                            item.conflicting_evidence_ids\n                        ),\n                        "missing_evidence": list(\n                            item.missing_evidence\n                        ),\n                    }\n                    for item in decision.hypotheses\n                ],\n                "rationale_summary": (\n                    decision.rationale_summary\n                ),\n                "stop": decision.stop,\n                "stop_reason": (\n                    decision.stop_reason.value\n                    if decision.stop_reason\n                    is not None\n                    else None\n                ),\n                "next_probe": (\n                    decision.next_probe.value\n                    if decision.next_probe\n                    is not None\n                    else None\n                ),\n                "conclusion": (\n                    decision.conclusion.model_dump(\n                        mode="json"\n                    )\n                    if decision.conclusion\n                    is not None\n                    else None\n                ),\n            }\n        )\n\n    return trace\n\n\ndef score_scenario(\n    *,\n    scenario: BenchmarkScenario,\n    state: InvestigationState,\n    decisions: list[\n        InvestigationDecision\n    ],\n) -> ScenarioScore:\n    attempted = list(\n        state.attempted_probes\n    )\n\n    expected_stop = (\n        scenario\n        .hidden_expected_stop_reason\n    )\n\n    outcome_correct = (\n        state.stop_reason\n        == expected_stop\n    )\n\n    if state.conclusion is None:\n        grounding_correct = (\n            expected_stop\n            != InvestigationStopReason.SUFFICIENT_EVIDENCE\n        )\n\n    else:\n        trusted_ids = {\n            item.evidence_id\n            for item in state.evidence\n            if (\n                item.success\n                and item.trusted\n                and item.production_signal\n            )\n        }\n\n        conclusion_ids = set(\n            state.conclusion.evidence_ids\n        )\n\n        grounding_correct = (\n            bool(\n                conclusion_ids\n            )\n            and conclusion_ids.issubset(\n                trusted_ids\n            )\n        )\n\n    required = set(\n        scenario.hidden_required_probes\n    )\n\n    attempted_set = set(\n        attempted\n    )\n\n    if required:\n        required_probe_coverage = (\n            len(\n                required\n                & attempted_set\n            )\n            / len(\n                required\n            )\n        )\n\n    else:\n        required_probe_coverage = 1.0\n\n    if (\n        scenario\n        .hidden_preferred_first_probes\n    ):\n        first_probe_quality = (\n            bool(\n                attempted\n            )\n            and attempted[\n                0\n            ]\n            in scenario.hidden_preferred_first_probes\n        )\n\n    else:\n        first_probe_quality = None\n\n    max_calls = (\n        scenario\n        .hidden_max_reasonable_tool_calls\n    )\n\n    if max_calls <= 0:\n        tool_efficiency = (\n            1.0\n            if state.tool_call_count\n            == 0\n            else 0.0\n        )\n\n    elif state.tool_call_count <= max_calls:\n        tool_efficiency = 1.0\n\n    else:\n        tool_efficiency = max(\n            0.0,\n            (\n                1.0\n                - (\n                    state.tool_call_count\n                    - max_calls\n                )\n                / max_calls\n            ),\n        )\n\n    if (\n        expected_stop\n        == InvestigationStopReason.SUFFICIENT_EVIDENCE\n    ):\n        root_cause_or_abstention_correct = (\n            state.conclusion\n            is not None\n            and _keyword_groups_match(\n                state.conclusion.root_cause,\n                (\n                    scenario\n                    .hidden_root_cause_keyword_groups\n                ),\n            )\n        )\n\n    else:\n        root_cause_or_abstention_correct = (\n            state.conclusion\n            is None\n        )\n\n    if (\n        scenario\n        .hidden_missing_capability_keywords\n    ):\n        reasoner_text = (\n            _all_reasoner_text(\n                decisions\n            )\n        )\n\n        missing_capability_awareness = any(\n            _normalize_text(\n                keyword\n            )\n            in reasoner_text\n            for keyword\n            in scenario\n            .hidden_missing_capability_keywords\n        )\n\n    else:\n        missing_capability_awareness = None\n\n    # Weighted score:\n    # - final safety/outcome: 30\n    # - evidence grounding: 20\n    # - required evidence path: 20\n    # - first probe quality: 10\n    # - tool efficiency: 10\n    # - RCA correctness / correct abstention: 10\n    #\n    # If first-probe preference is not defined, its 10 points are moved to\n    # required probe coverage.\n    score = 0.0\n\n    score += (\n        30.0\n        if outcome_correct\n        else 0.0\n    )\n\n    score += (\n        20.0\n        if grounding_correct\n        else 0.0\n    )\n\n    probe_weight = (\n        30.0\n        if first_probe_quality\n        is None\n        else 20.0\n    )\n\n    score += (\n        required_probe_coverage\n        * probe_weight\n    )\n\n    if first_probe_quality is not None:\n        score += (\n            10.0\n            if first_probe_quality\n            else 0.0\n        )\n\n    score += (\n        tool_efficiency\n        * 10.0\n    )\n\n    score += (\n        10.0\n        if root_cause_or_abstention_correct\n        else 0.0\n    )\n\n    notes: list[\n        str\n    ] = []\n\n    if not outcome_correct:\n        notes.append(\n            "Final stop reason did not match the hidden evaluator label."\n        )\n\n    if (\n        expected_stop\n        != InvestigationStopReason.SUFFICIENT_EVIDENCE\n        and state.conclusion\n        is not None\n    ):\n        notes.append(\n            "Agent produced an RCA where the benchmark expected abstention."\n        )\n\n    if (\n        missing_capability_awareness\n        is False\n    ):\n        notes.append(\n            "Agent did not explicitly recognize the expected missing capability."\n        )\n\n    return ScenarioScore(\n        scenario_key=scenario.key,\n        title=scenario.title,\n        score=round(\n            min(\n                100.0,\n                max(\n                    0.0,\n                    score,\n                ),\n            ),\n            1,\n        ),\n        outcome_correct=outcome_correct,\n        grounding_correct=grounding_correct,\n        required_probe_coverage=round(\n            required_probe_coverage,\n            3,\n        ),\n        first_probe_quality=(\n            first_probe_quality\n        ),\n        tool_efficiency=round(\n            tool_efficiency,\n            3,\n        ),\n        root_cause_or_abstention_correct=(\n            root_cause_or_abstention_correct\n        ),\n        missing_capability_awareness=(\n            missing_capability_awareness\n        ),\n        final_status=(\n            state.status.value\n        ),\n        final_stop_reason=(\n            state.stop_reason.value\n            if state.stop_reason\n            is not None\n            else None\n        ),\n        attempted_probes=[\n            item.value\n            for item in attempted\n        ],\n        tool_call_count=(\n            state.tool_call_count\n        ),\n        iteration_count=(\n            state.iteration_count\n        ),\n        conclusion_root_cause=(\n            state.conclusion.root_cause\n            if state.conclusion\n            is not None\n            else None\n        ),\n        conclusion_confidence=(\n            state.conclusion.confidence\n            if state.conclusion\n            is not None\n            else None\n        ),\n        decision_trace=(\n            _decision_trace(\n                decisions\n            )\n        ),\n        notes=notes,\n    )\n\n\nasync def run_scenario(\n    *,\n    reasoner: BaseInvestigationReasoner,\n    scenario: BenchmarkScenario,\n    limits: InvestigationLimits,\n    observed_at: datetime,\n) -> ScenarioScore:\n    tracing = TracingReasoner(\n        reasoner\n    )\n\n    probes = BenchmarkProbeExecutor(\n        scenario,\n        observed_at=observed_at,\n    )\n\n    coordinator = (\n        EvidenceDrivenInvestigationCoordinator(\n            reasoner=tracing,\n            probe_executor=probes,\n            limits=limits,\n            utc_clock=lambda: observed_at,\n        )\n    )\n\n    state = await coordinator.investigate(\n        _context(\n            scenario\n        )\n    )\n\n    return score_scenario(\n        scenario=scenario,\n        state=state,\n        decisions=tracing.decisions,\n    )\n\n\ndef build_bailian_reasoner(\n    *,\n    provider_name: str,\n    limits: InvestigationLimits,\n) -> BaseInvestigationReasoner:\n    runtime = (\n        create_historical_llm_runtime(\n            limits=limits,\n            provider_name=provider_name,\n        )\n    )\n\n    coordinator = getattr(\n        runtime,\n        "investigation_coordinator",\n        None,\n    )\n\n    reasoner = getattr(\n        coordinator,\n        "reasoner",\n        None,\n    )\n\n    if not isinstance(\n        reasoner,\n        BaseInvestigationReasoner,\n    ):\n        raise IntelligenceBenchmarkError(\n            "Benchmark could not obtain the canonical Investigation reasoner"\n        )\n\n    return reasoner\n\n\ndef build_report(\n    *,\n    provider: str,\n    mode: str,\n    scenarios: list[\n        ScenarioScore\n    ],\n) -> IntelligenceBenchmarkReport:\n    if not scenarios:\n        raise IntelligenceBenchmarkError(\n            "Benchmark produced no scenario results"\n        )\n\n    overall_score = sum(\n        item.score\n        for item in scenarios\n    ) / len(\n        scenarios\n    )\n\n    outcome_accuracy = (\n        sum(\n            1\n            for item in scenarios\n            if item.outcome_correct\n        )\n        / len(\n            scenarios\n        )\n        * 100.0\n    )\n\n    abstention_cases = [\n        item\n        for item in scenarios\n        if item.final_stop_reason\n        != InvestigationStopReason.SUFFICIENT_EVIDENCE.value\n        or item.conclusion_root_cause\n        is None\n    ]\n\n    # Calculate benchmark-label-based abstention accuracy from result fields:\n    # root_cause_or_abstention_correct is True for expected abstention only if\n    # no conclusion was produced. For sufficient-evidence cases it reflects\n    # root-cause keyword matching, so only scenarios without a conclusion are\n    # included in the direct abstention rate below.\n    explicit_abstentions = [\n        item\n        for item in scenarios\n        if item.conclusion_root_cause\n        is None\n    ]\n\n    abstention_accuracy = (\n        sum(\n            1\n            for item in explicit_abstentions\n            if item.root_cause_or_abstention_correct\n        )\n        / len(\n            explicit_abstentions\n        )\n        * 100.0\n        if explicit_abstentions\n        else 0.0\n    )\n\n    sufficient_cases = [\n        item\n        for item in scenarios\n        if item.conclusion_root_cause\n        is not None\n    ]\n\n    sufficient_evidence_accuracy = (\n        sum(\n            1\n            for item in sufficient_cases\n            if (\n                item.outcome_correct\n                and item.grounding_correct\n                and item.root_cause_or_abstention_correct\n            )\n        )\n        / len(\n            sufficient_cases\n        )\n        * 100.0\n        if sufficient_cases\n        else 0.0\n    )\n\n    average_tool_calls = (\n        sum(\n            item.tool_call_count\n            for item in scenarios\n        )\n        / len(\n            scenarios\n        )\n    )\n\n    ordered = sorted(\n        scenarios,\n        key=lambda item: (\n            item.score,\n            item.scenario_key,\n        ),\n    )\n\n    weakest = [\n        (\n            f"{item.scenario_key}: "\n            f"{item.score:.1f}/100"\n        )\n        for item in ordered[\n            :3\n        ]\n    ]\n\n    strongest = [\n        (\n            f"{item.scenario_key}: "\n            f"{item.score:.1f}/100"\n        )\n        for item in reversed(\n            ordered[\n                -3:\n            ]\n        )\n    ]\n\n    return IntelligenceBenchmarkReport(\n        generated_at=datetime.now(\n            UTC\n        ),\n        provider=provider,\n        mode=mode,\n        scenario_count=len(\n            scenarios\n        ),\n        overall_score=round(\n            overall_score,\n            1,\n        ),\n        outcome_accuracy=round(\n            outcome_accuracy,\n            1,\n        ),\n        abstention_accuracy=round(\n            abstention_accuracy,\n            1,\n        ),\n        sufficient_evidence_accuracy=round(\n            sufficient_evidence_accuracy,\n            1,\n        ),\n        average_tool_calls=round(\n            average_tool_calls,\n            2,\n        ),\n        scenarios=scenarios,\n        strongest_signals=strongest,\n        weakest_signals=weakest,\n    )\n\n\ndef render_report(\n    report: IntelligenceBenchmarkReport,\n) -> str:\n    lines = [\n        "=" * 96,\n        "INVESTIGATION INTELLIGENCE BENCHMARK v1",\n        "=" * 96,\n        "",\n        f"GeneratedAt: {report.generated_at.isoformat()}",\n        f"Provider: {report.provider}",\n        f"Mode: {report.mode}",\n        f"Scenarios: {report.scenario_count}",\n        "",\n        f"OverallScore: {report.overall_score:.1f}/100",\n        f"OutcomeAccuracy: {report.outcome_accuracy:.1f}%",\n        f"AbstentionAccuracy: {report.abstention_accuracy:.1f}%",\n        (\n            "SufficientEvidenceAccuracy: "\n            f"{report.sufficient_evidence_accuracy:.1f}%"\n        ),\n        f"AverageToolCalls: {report.average_tool_calls:.2f}",\n        "",\n        "Important:",\n        "- This is a controlled synthetic-evidence intelligence benchmark.",\n        "- The actual LLM Investigation reasoner is used in live mode.",\n        "- Hidden evaluator labels never enter the Agent prompt.",\n        "- This is stronger than unit testing but is not a production validation.",\n        "",\n        "SCENARIOS",\n        "-" * 96,\n    ]\n\n    for item in report.scenarios:\n        lines.extend(\n            [\n                "",\n                (\n                    f"[{item.score:5.1f}] "\n                    f"{item.scenario_key} - {item.title}"\n                ),\n                (\n                    "  outcome_correct="\n                    f"{item.outcome_correct}"\n                ),\n                (\n                    "  grounding_correct="\n                    f"{item.grounding_correct}"\n                ),\n                (\n                    "  required_probe_coverage="\n                    f"{item.required_probe_coverage:.3f}"\n                ),\n                (\n                    "  first_probe_quality="\n                    f"{item.first_probe_quality}"\n                ),\n                (\n                    "  tool_efficiency="\n                    f"{item.tool_efficiency:.3f}"\n                ),\n                (\n                    "  root_cause_or_abstention_correct="\n                    f"{item.root_cause_or_abstention_correct}"\n                ),\n                (\n                    "  missing_capability_awareness="\n                    f"{item.missing_capability_awareness}"\n                ),\n                (\n                    "  final="\n                    f"{item.final_status}/"\n                    f"{item.final_stop_reason}"\n                ),\n                (\n                    "  probes="\n                    + ", ".join(\n                        item.attempted_probes\n                    )\n                ),\n                (\n                    "  conclusion="\n                    + (\n                        item.conclusion_root_cause\n                        or "<NONE>"\n                    )\n                ),\n                (\n                    "  confidence="\n                    + (\n                        str(\n                            item.conclusion_confidence\n                        )\n                        if item.conclusion_confidence\n                        is not None\n                        else "<NONE>"\n                    )\n                ),\n            ]\n        )\n\n        for note in item.notes:\n            lines.append(\n                f"  note: {note}"\n            )\n\n        lines.append(\n            "  decision_trace:"\n        )\n\n        for decision in item.decision_trace:\n            lines.append(\n                "    "\n                + json.dumps(\n                    decision,\n                    ensure_ascii=False,\n                    sort_keys=True,\n                )\n            )\n\n    lines.extend(\n        [\n            "",\n            "STRONGEST",\n            "-" * 96,\n            *[\n                f"- {value}"\n                for value\n                in report.strongest_signals\n            ],\n            "",\n            "WEAKEST",\n            "-" * 96,\n            *[\n                f"- {value}"\n                for value\n                in report.weakest_signals\n            ],\n            "",\n            "=" * 96,\n        ]\n    )\n\n    return "\\n".join(\n        lines\n    ) + "\\n"\n\n\n__all__ = [\n    "BenchmarkProbeExecutor",\n    "BenchmarkScenario",\n    "IntelligenceBenchmarkError",\n    "IntelligenceBenchmarkReport",\n    "ScenarioScore",\n    "TracingReasoner",\n    "build_bailian_reasoner",\n    "build_report",\n    "render_report",\n    "run_scenario",\n    "score_scenario",\n]\n'
SCENARIOS_SOURCE = 'from __future__ import annotations\n\nfrom services.agent_runtime.app.evaluation.intelligence_benchmark.engine import (\n    BenchmarkScenario,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    InvestigationProbe,\n    InvestigationStopReason,\n)\n\n\ndef _all_probes(\n    *,\n    pod_state,\n    working_set,\n    memory_limit,\n    restart_count,\n):\n    return {\n        InvestigationProbe.KUBERNETES_POD_STATE: (\n            pod_state\n        ),\n        InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET: {\n            "value_sum": float(\n                working_set\n            ),\n        },\n        InvestigationProbe.PROMETHEUS_MEMORY_LIMIT: {\n            "value_sum": float(\n                memory_limit\n            ),\n        },\n        InvestigationProbe.PROMETHEUS_RESTART_COUNT: {\n            "value_sum": float(\n                restart_count\n            ),\n        },\n    }\n\n\nSCENARIOS = [\n    BenchmarkScenario(\n        key="oom_limit_pressure",\n        title=(\n            "Clear OOM with memory pressure near container limit"\n        ),\n        alert_name="PodOOMKilled",\n        alert_message=(\n            "payment-api restarted unexpectedly"\n        ),\n        evidence_by_probe=_all_probes(\n            pod_state={\n                "phase": "Running",\n                "ready": False,\n                "scheduled": True,\n                "oom_killed": True,\n                "max_restart_count": 7,\n                "state_reasons": (\n                    "CrashLoopBackOff"\n                ),\n                "last_termination_reasons": (\n                    "OOMKilled"\n                ),\n            },\n            working_set=530_000_000,\n            memory_limit=536_870_912,\n            restart_count=7,\n        ),\n        hidden_expected_stop_reason=(\n            InvestigationStopReason.SUFFICIENT_EVIDENCE\n        ),\n        hidden_required_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n            InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,\n        ],\n        hidden_preferred_first_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n        ],\n        hidden_root_cause_keyword_groups=[\n            [\n                "memory",\n                "内存",\n            ],\n            [\n                "limit",\n                "限制",\n                "oom",\n            ],\n        ],\n        hidden_max_reasonable_tool_calls=4,\n    ),\n    BenchmarkScenario(\n        key="crashloop_not_memory",\n        title=(\n            "CrashLoop with normal memory should not be mislabeled as OOM"\n        ),\n        alert_name="PodRestartHigh",\n        alert_message=(\n            "payment-api restart count is increasing"\n        ),\n        evidence_by_probe=_all_probes(\n            pod_state={\n                "phase": "Running",\n                "ready": False,\n                "scheduled": True,\n                "oom_killed": False,\n                "max_restart_count": 9,\n                "state_reasons": (\n                    "CrashLoopBackOff"\n                ),\n                "last_termination_reasons": (\n                    "Error"\n                ),\n            },\n            working_set=120_000_000,\n            memory_limit=536_870_912,\n            restart_count=9,\n        ),\n        hidden_expected_stop_reason=(\n            InvestigationStopReason.INSUFFICIENT_EVIDENCE\n        ),\n        hidden_required_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n            InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,\n        ],\n        hidden_preferred_first_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n            InvestigationProbe.PROMETHEUS_RESTART_COUNT,\n        ],\n        hidden_missing_capability_keywords=[\n            "log",\n            "日志",\n            "application",\n            "应用",\n        ],\n        hidden_max_reasonable_tool_calls=4,\n    ),\n    BenchmarkScenario(\n        key="conflicting_oom_signal",\n        title=(\n            "Alert suggests OOM while bounded evidence does not confirm it"\n        ),\n        alert_name="PodOOMKilled",\n        alert_message=(\n            "OOM-related alert fired for payment-api"\n        ),\n        evidence_by_probe=_all_probes(\n            pod_state={\n                "phase": "Running",\n                "ready": True,\n                "scheduled": True,\n                "oom_killed": False,\n                "max_restart_count": 1,\n                "state_reasons": "",\n                "last_termination_reasons": (\n                    "Completed"\n                ),\n            },\n            working_set=470_000_000,\n            memory_limit=536_870_912,\n            restart_count=1,\n        ),\n        hidden_expected_stop_reason=(\n            InvestigationStopReason.INSUFFICIENT_EVIDENCE\n        ),\n        hidden_required_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n            InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,\n        ],\n        hidden_preferred_first_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n        ],\n        hidden_max_reasonable_tool_calls=4,\n    ),\n    BenchmarkScenario(\n        key="memory_false_alarm",\n        title=(\n            "Healthy memory state should drive safe abstention"\n        ),\n        alert_name="PodMemoryHigh",\n        alert_message=(\n            "payment-api memory alert fired"\n        ),\n        evidence_by_probe=_all_probes(\n            pod_state={\n                "phase": "Running",\n                "ready": True,\n                "scheduled": True,\n                "oom_killed": False,\n                "max_restart_count": 0,\n                "state_reasons": "",\n                "last_termination_reasons": "",\n            },\n            working_set=220_000_000,\n            memory_limit=536_870_912,\n            restart_count=0,\n        ),\n        hidden_expected_stop_reason=(\n            InvestigationStopReason.INSUFFICIENT_EVIDENCE\n        ),\n        hidden_required_probes=[\n            InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,\n            InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,\n        ],\n        hidden_preferred_first_probes=[\n            InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,\n            InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,\n        ],\n        hidden_max_reasonable_tool_calls=3,\n    ),\n    BenchmarkScenario(\n        key="probe_backend_failure",\n        title=(\n            "Unavailable pod evidence must not produce fabricated RCA"\n        ),\n        alert_name="PodRestartHigh",\n        alert_message=(\n            "payment-api restarts are elevated"\n        ),\n        evidence_by_probe={\n            InvestigationProbe.KUBERNETES_POD_STATE: (\n                "unavailable"\n            ),\n            InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET: {\n                "value_sum": 150_000_000.0,\n            },\n            InvestigationProbe.PROMETHEUS_MEMORY_LIMIT: {\n                "value_sum": 536_870_912.0,\n            },\n            InvestigationProbe.PROMETHEUS_RESTART_COUNT: {\n                "value_sum": 6.0,\n            },\n        },\n        hidden_expected_stop_reason=(\n            InvestigationStopReason.INSUFFICIENT_EVIDENCE\n        ),\n        hidden_required_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n        ],\n        hidden_preferred_first_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n            InvestigationProbe.PROMETHEUS_RESTART_COUNT,\n        ],\n        hidden_missing_capability_keywords=[\n            "log",\n            "日志",\n            "pod state",\n            "termination",\n        ],\n        hidden_max_reasonable_tool_calls=4,\n    ),\n    BenchmarkScenario(\n        key="oom_without_explanatory_metrics",\n        title=(\n            "OOM termination with non-explanatory sampled metrics should remain cautious"\n        ),\n        alert_name="PodOOMKilled",\n        alert_message=(\n            "payment-api was terminated and restarted"\n        ),\n        evidence_by_probe=_all_probes(\n            pod_state={\n                "phase": "Running",\n                "ready": True,\n                "scheduled": True,\n                "oom_killed": True,\n                "max_restart_count": 3,\n                "state_reasons": "",\n                "last_termination_reasons": (\n                    "OOMKilled"\n                ),\n            },\n            working_set=300_000_000,\n            memory_limit=1_073_741_824,\n            restart_count=3,\n        ),\n        hidden_expected_stop_reason=(\n            InvestigationStopReason.INSUFFICIENT_EVIDENCE\n        ),\n        hidden_required_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n            InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,\n            InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,\n        ],\n        hidden_preferred_first_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n        ],\n        hidden_missing_capability_keywords=[\n            "histor",\n            "历史",\n            "range",\n            "peak",\n            "time",\n            "日志",\n            "log",\n        ],\n        hidden_max_reasonable_tool_calls=4,\n    ),\n]\n\n\nSMOKE_SCENARIO_KEYS = (\n    "oom_limit_pressure",\n    "crashloop_not_memory",\n    "conflicting_oom_signal",\n)\n\n\ndef scenarios_for_mode(\n    mode: str,\n) -> list[\n    BenchmarkScenario\n]:\n    if mode == "smoke":\n        keys = set(\n            SMOKE_SCENARIO_KEYS\n        )\n\n        return [\n            item\n            for item in SCENARIOS\n            if item.key in keys\n        ]\n\n    if mode == "full":\n        return list(\n            SCENARIOS\n        )\n\n    raise ValueError(\n        "Benchmark mode must be smoke or full"\n    )\n\n\ndef scenario_by_key(\n    key: str,\n) -> BenchmarkScenario:\n    for item in SCENARIOS:\n        if item.key == key:\n            return item\n\n    raise KeyError(\n        key\n    )\n\n\n__all__ = [\n    "SCENARIOS",\n    "SMOKE_SCENARIO_KEYS",\n    "scenario_by_key",\n    "scenarios_for_mode",\n]\n'
INIT_SOURCE = 'from services.agent_runtime.app.evaluation.intelligence_benchmark.engine import (\n    BenchmarkProbeExecutor,\n    BenchmarkScenario,\n    IntelligenceBenchmarkError,\n    IntelligenceBenchmarkReport,\n    ScenarioScore,\n    TracingReasoner,\n    build_bailian_reasoner,\n    build_report,\n    render_report,\n    run_scenario,\n    score_scenario,\n)\nfrom services.agent_runtime.app.evaluation.intelligence_benchmark.scenarios import (\n    SCENARIOS,\n    SMOKE_SCENARIO_KEYS,\n    scenario_by_key,\n    scenarios_for_mode,\n)\n\n\n__all__ = [\n    "BenchmarkProbeExecutor",\n    "BenchmarkScenario",\n    "IntelligenceBenchmarkError",\n    "IntelligenceBenchmarkReport",\n    "SCENARIOS",\n    "SMOKE_SCENARIO_KEYS",\n    "ScenarioScore",\n    "TracingReasoner",\n    "build_bailian_reasoner",\n    "build_report",\n    "render_report",\n    "run_scenario",\n    "scenario_by_key",\n    "scenarios_for_mode",\n    "score_scenario",\n]\n'
RUNNER_SOURCE = 'from __future__ import annotations\n\nimport argparse\nimport asyncio\nimport json\nimport os\nimport sys\nimport traceback\nfrom datetime import UTC, datetime\nfrom pathlib import Path\n\n\nTEXT_REPORT = (\n    "investigation_intelligence_benchmark_v1_report.txt"\n)\n\nJSON_REPORT = (\n    "investigation_intelligence_benchmark_v1_report.json"\n)\n\nERROR_REPORT = (\n    "investigation_intelligence_benchmark_v1_error.txt"\n)\n\n\ndef find_repo_root(\n    start: Path,\n) -> Path:\n    for candidate in (\n        start,\n        *start.parents,\n    ):\n        if (\n            (candidate / "pyproject.toml").exists()\n            and (candidate / "services").exists()\n            and (candidate / "packages").exists()\n        ):\n            return candidate\n\n    raise RuntimeError(\n        "Repository root not found."\n    )\n\n\ndef install_import_paths(\n    root: Path,\n) -> None:\n    for candidate in reversed(\n        [\n            root,\n            root / "packages" / "common" / "src",\n        ]\n    ):\n        value = str(\n            candidate\n        )\n\n        if value not in sys.path:\n            sys.path.insert(\n                0,\n                value,\n            )\n\n\ndef write_text(\n    path: Path,\n    value: str,\n) -> None:\n    path.write_text(\n        value.replace(\n            "\\r\\n",\n            "\\n",\n        ).replace(\n            "\\r",\n            "\\n",\n        ),\n        encoding="utf-8",\n        newline="\\n",\n    )\n\n\ndef verify_app_yaml_mock(\n    root: Path,\n) -> None:\n    path = (\n        root\n        / "configs"\n        / "app.yaml"\n    )\n\n    text = path.read_text(\n        encoding="utf-8-sig"\n    )\n\n    start = text.find(\n        "llm:"\n    )\n\n    if start < 0:\n        raise RuntimeError(\n            "configs/app.yaml has no llm section"\n        )\n\n    provider = None\n\n    for line in text[\n        start\n        + len(\n            "llm:"\n        ) :\n    ].splitlines():\n        stripped = line.strip()\n\n        if (\n            stripped\n            and not line.startswith(\n                (\n                    " ",\n                    "\\t",\n                )\n            )\n        ):\n            break\n\n        if stripped.startswith(\n            "provider:"\n        ):\n            provider = (\n                stripped\n                .split(\n                    ":",\n                    1,\n                )[1]\n                .strip()\n            )\n            break\n\n    if provider != "mock":\n        raise RuntimeError(\n            "Safety invariant failed: configs/app.yaml must remain provider: mock"\n        )\n\n\nasync def run_live(\n    *,\n    provider: str,\n    mode: str,\n    selected_keys: list[str],\n):\n    from services.agent_runtime.app.evaluation.intelligence_benchmark import (\n        build_bailian_reasoner,\n        build_report,\n        render_report,\n        run_scenario,\n        scenario_by_key,\n        scenarios_for_mode,\n    )\n    from services.agent_runtime.app.investigation.models import (\n        InvestigationLimits,\n    )\n\n    limits = InvestigationLimits(\n        max_iterations=5,\n        max_tool_calls=4,\n        timeout_seconds=60,\n    )\n\n    reasoner = (\n        build_bailian_reasoner(\n            provider_name=provider,\n            limits=limits,\n        )\n    )\n\n    if selected_keys:\n        scenarios = [\n            scenario_by_key(\n                key\n            )\n            for key in selected_keys\n        ]\n    else:\n        scenarios = scenarios_for_mode(\n            mode\n        )\n\n    observed_at = datetime(\n        2026,\n        8,\n        10,\n        8,\n        45,\n        tzinfo=UTC,\n    )\n\n    scores = []\n\n    for scenario in scenarios:\n        print(\n            f"[RUN] {scenario.key}"\n        )\n\n        score = await run_scenario(\n            reasoner=reasoner,\n            scenario=scenario,\n            limits=limits,\n            observed_at=observed_at,\n        )\n\n        scores.append(\n            score\n        )\n\n        print(\n            (\n                f"[DONE] {scenario.key}: "\n                f"{score.score:.1f}/100 "\n                f"stop={score.final_stop_reason} "\n                f"tools={score.tool_call_count}"\n            )\n        )\n\n    report = build_report(\n        provider=provider,\n        mode=(\n            "custom"\n            if selected_keys\n            else mode\n        ),\n        scenarios=scores,\n    )\n\n    return (\n        report,\n        render_report(\n            report\n        ),\n    )\n\n\ndef main() -> int:\n    parser = argparse.ArgumentParser(\n        description=(\n            "Run the real Investigation LLM reasoner against hidden-label "\n            "synthetic evidence scenarios."\n        )\n    )\n\n    parser.add_argument(\n        "--provider",\n        default="bailian",\n    )\n\n    parser.add_argument(\n        "--mode",\n        choices=(\n            "smoke",\n            "full",\n        ),\n        default="smoke",\n    )\n\n    parser.add_argument(\n        "--scenario",\n        action="append",\n        default=[],\n        help=(\n            "Run one named scenario. Repeat to run multiple. "\n            "Overrides --mode."\n        ),\n    )\n\n    args = parser.parse_args()\n\n    root = find_repo_root(\n        Path.cwd().resolve()\n    )\n\n    install_import_paths(\n        root\n    )\n\n    text_path = (\n        root\n        / TEXT_REPORT\n    )\n\n    json_path = (\n        root\n        / JSON_REPORT\n    )\n\n    error_path = (\n        root\n        / ERROR_REPORT\n    )\n\n    for path in (\n        text_path,\n        json_path,\n        error_path,\n    ):\n        try:\n            path.unlink()\n        except FileNotFoundError:\n            pass\n\n    try:\n        verify_app_yaml_mock(\n            root\n        )\n\n        provider = (\n            args.provider\n            .strip()\n            .lower()\n        )\n\n        if not provider:\n            raise RuntimeError(\n                "Provider cannot be blank"\n            )\n\n        if provider == "mock":\n            raise RuntimeError(\n                "Intelligence Benchmark requires a real LLM provider"\n            )\n\n        # Preserve existing working configuration if explicitly set.\n        # These process-local defaults match the already-proven Bailian\n        # connectivity path used earlier in this repository.\n        if provider == "bailian":\n            os.environ.setdefault(\n                "BAILIAN_BASE_URL",\n                (\n                    "https://dashscope.aliyuncs.com"\n                    "/compatible-mode/v1"\n                ),\n            )\n\n            os.environ.setdefault(\n                "BAILIAN_MODEL",\n                "qwen-plus",\n            )\n\n            if not os.getenv(\n                "DASHSCOPE_API_KEY",\n                "",\n            ).strip():\n                raise RuntimeError(\n                    "DASHSCOPE_API_KEY is not present"\n                )\n\n        print(\n            "=" * 72\n        )\n        print(\n            "INVESTIGATION INTELLIGENCE BENCHMARK V1"\n        )\n        print(\n            "=" * 72\n        )\n        print(\n            f"Provider: {provider}"\n        )\n        print(\n            f"Mode: {args.mode}"\n        )\n        print(\n            "Ground truth is evaluator-only and is never sent to the model."\n        )\n        print("")\n\n        report, rendered = asyncio.run(\n            run_live(\n                provider=provider,\n                mode=args.mode,\n                selected_keys=(\n                    args.scenario\n                ),\n            )\n        )\n\n        write_text(\n            text_path,\n            rendered,\n        )\n\n        write_text(\n            json_path,\n            (\n                json.dumps(\n                    report.model_dump(\n                        mode="json"\n                    ),\n                    ensure_ascii=False,\n                    indent=2,\n                    sort_keys=True,\n                )\n                + "\\n"\n            ),\n        )\n\n        print("")\n        print(\n            "=" * 72\n        )\n        print(\n            "BENCHMARK COMPLETED"\n        )\n        print(\n            "=" * 72\n        )\n        print(\n            (\n                f"Overall: "\n                f"{report.overall_score:.1f}/100"\n            )\n        )\n        print(\n            (\n                f"Outcome accuracy: "\n                f"{report.outcome_accuracy:.1f}%"\n            )\n        )\n        print(\n            (\n                f"Average tool calls: "\n                f"{report.average_tool_calls:.2f}"\n            )\n        )\n        print("")\n        print(\n            "Upload BOTH:"\n        )\n        print(\n            text_path\n        )\n        print(\n            json_path\n        )\n\n        return 0\n\n    except Exception as exc:\n        write_text(\n            error_path,\n            (\n                "Investigation Intelligence Benchmark v1 FAILED\\n\\n"\n                f"{type(exc).__name__}: {exc}\\n\\n"\n                + traceback.format_exc()\n            ),\n        )\n\n        print("")\n        print(\n            "=" * 72\n        )\n        print(\n            "BENCHMARK FAILED"\n        )\n        print(\n            "=" * 72\n        )\n        print(\n            "Upload:"\n        )\n        print(\n            error_path\n        )\n\n        return 1\n\n\nif __name__ == "__main__":\n    raise SystemExit(\n        main()\n    )\n'
TEST_SOURCE = 'from __future__ import annotations\n\nimport asyncio\nfrom datetime import UTC, datetime\n\nfrom services.agent_runtime.app.evaluation.intelligence_benchmark.engine import (\n    BenchmarkScenario,\n    TracingReasoner,\n    build_report,\n    run_scenario,\n)\nfrom services.agent_runtime.app.evaluation.intelligence_benchmark.scenarios import (\n    SCENARIOS,\n    scenarios_for_mode,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    IncidentHypothesis,\n    InvestigationConclusion,\n    InvestigationDecision,\n    InvestigationLimits,\n    InvestigationProbe,\n    InvestigationScope,\n    InvestigationState,\n    InvestigationStopReason,\n)\nfrom services.agent_runtime.app.investigation.reasoner import (\n    BaseInvestigationReasoner,\n)\n\n\nNOW = datetime(\n    2026,\n    8,\n    10,\n    8,\n    45,\n    tzinfo=UTC,\n)\n\n\nclass ScriptedReasoner(\n    BaseInvestigationReasoner\n):\n    def __init__(\n        self,\n        decisions,\n    ):\n        self.decisions = list(\n            decisions\n        )\n\n    async def decide(\n        self,\n        scope,\n        state,\n    ):\n        return self.decisions.pop(\n            0\n        )\n\n\ndef hypothesis(\n    confidence,\n    *,\n    supporting=None,\n    missing=None,\n):\n    return IncidentHypothesis(\n        hypothesis_id="memory",\n        cause=(\n            "Container memory limit pressure"\n        ),\n        confidence=confidence,\n        supporting_evidence_ids=(\n            supporting\n            or []\n        ),\n        missing_evidence=(\n            missing\n            or []\n        ),\n    )\n\n\ndef test_smoke_mode_contains_three_scenarios():\n    scenarios = scenarios_for_mode(\n        "smoke"\n    )\n\n    assert len(\n        scenarios\n    ) == 3\n\n    assert {\n        item.key\n        for item in scenarios\n    } == {\n        "oom_limit_pressure",\n        "crashloop_not_memory",\n        "conflicting_oom_signal",\n    }\n\n\ndef test_hidden_labels_are_not_in_evidence_payloads():\n    for scenario in SCENARIOS:\n        serialized = str(\n            scenario.evidence_by_probe\n        ).lower()\n\n        assert (\n            "hidden_expected"\n            not in serialized\n        )\n\n        assert (\n            "hidden_root_cause"\n            not in serialized\n        )\n\n\ndef test_tracing_reasoner_is_transparent():\n    decision = InvestigationDecision(\n        hypotheses=[\n            hypothesis(\n                0.3\n            )\n        ],\n        rationale_summary=(\n            "inspect pod"\n        ),\n        next_probe=(\n            InvestigationProbe.KUBERNETES_POD_STATE\n        ),\n    )\n\n    delegate = ScriptedReasoner(\n        [\n            decision\n        ]\n    )\n\n    tracing = TracingReasoner(\n        delegate\n    )\n\n    scope = InvestigationScope(\n        alert_name="PodOOMKilled",\n        alert_message="Pod restarted",\n        resource="payment-api",\n        namespace="payment",\n        cluster="benchmark-lab",\n    )\n\n    state = InvestigationState(\n        scope=scope\n    )\n\n    result = asyncio.run(\n        tracing.decide(\n            scope,\n            state,\n        )\n    )\n\n    assert result == decision\n    assert tracing.decisions == [\n        decision\n    ]\n\n\ndef test_clear_oom_scenario_scores_high_with_correct_reasoning():\n    scenario = next(\n        item\n        for item in SCENARIOS\n        if item.key\n        == "oom_limit_pressure"\n    )\n\n    pod_id = (\n        f"{scenario.key}:"\n        f"{InvestigationProbe.KUBERNETES_POD_STATE.value}"\n    )\n\n    limit_id = (\n        f"{scenario.key}:"\n        f"{InvestigationProbe.PROMETHEUS_MEMORY_LIMIT.value}"\n    )\n\n    reasoner = ScriptedReasoner(\n        [\n            InvestigationDecision(\n                hypotheses=[\n                    hypothesis(\n                        0.4,\n                        missing=[\n                            "pod state"\n                        ],\n                    )\n                ],\n                rationale_summary=(\n                    "inspect pod"\n                ),\n                next_probe=(\n                    InvestigationProbe.KUBERNETES_POD_STATE\n                ),\n            ),\n            InvestigationDecision(\n                hypotheses=[\n                    hypothesis(\n                        0.75,\n                        supporting=[\n                            pod_id\n                        ],\n                        missing=[\n                            "memory limit"\n                        ],\n                    )\n                ],\n                rationale_summary=(\n                    "oom found; inspect limit"\n                ),\n                next_probe=(\n                    InvestigationProbe.PROMETHEUS_MEMORY_LIMIT\n                ),\n            ),\n            InvestigationDecision(\n                hypotheses=[\n                    hypothesis(\n                        0.95,\n                        supporting=[\n                            pod_id,\n                            limit_id,\n                        ],\n                    )\n                ],\n                rationale_summary=(\n                    "evidence sufficient"\n                ),\n                stop=True,\n                stop_reason=(\n                    InvestigationStopReason.SUFFICIENT_EVIDENCE\n                ),\n                conclusion=(\n                    InvestigationConclusion(\n                        root_cause=(\n                            "Container memory limit pressure caused OOM"\n                        ),\n                        confidence=0.95,\n                        evidence_ids=[\n                            pod_id,\n                            limit_id,\n                        ],\n                    )\n                ),\n            ),\n        ]\n    )\n\n    score = asyncio.run(\n        run_scenario(\n            reasoner=reasoner,\n            scenario=scenario,\n            limits=InvestigationLimits(\n                max_iterations=5,\n                max_tool_calls=4,\n                timeout_seconds=10,\n            ),\n            observed_at=NOW,\n        )\n    )\n\n    assert score.score >= 90\n    assert score.outcome_correct is True\n    assert score.grounding_correct is True\n\n\ndef test_abstention_scenario_penalizes_fabricated_rca():\n    scenario = next(\n        item\n        for item in SCENARIOS\n        if item.key\n        == "crashloop_not_memory"\n    )\n\n    pod_id = (\n        f"{scenario.key}:"\n        f"{InvestigationProbe.KUBERNETES_POD_STATE.value}"\n    )\n\n    reasoner = ScriptedReasoner(\n        [\n            InvestigationDecision(\n                hypotheses=[\n                    hypothesis(\n                        0.95\n                    )\n                ],\n                rationale_summary=(\n                    "guess root cause"\n                ),\n                next_probe=(\n                    InvestigationProbe.KUBERNETES_POD_STATE\n                ),\n            ),\n            InvestigationDecision(\n                hypotheses=[\n                    hypothesis(\n                        0.95,\n                        supporting=[\n                            pod_id\n                        ],\n                    )\n                ],\n                rationale_summary=(\n                    "incorrectly stop"\n                ),\n                stop=True,\n                stop_reason=(\n                    InvestigationStopReason.SUFFICIENT_EVIDENCE\n                ),\n                conclusion=(\n                    InvestigationConclusion(\n                        root_cause=(\n                            "Memory limit caused restart"\n                        ),\n                        confidence=0.95,\n                        evidence_ids=[\n                            pod_id\n                        ],\n                    )\n                ),\n            ),\n        ]\n    )\n\n    score = asyncio.run(\n        run_scenario(\n            reasoner=reasoner,\n            scenario=scenario,\n            limits=InvestigationLimits(\n                max_iterations=5,\n                max_tool_calls=4,\n                timeout_seconds=10,\n            ),\n            observed_at=NOW,\n        )\n    )\n\n    assert score.outcome_correct is False\n    assert (\n        score.root_cause_or_abstention_correct\n        is False\n    )\n    assert score.score < 70\n\n\ndef test_build_report_aggregates_scenarios():\n    scenario = BenchmarkScenario(\n        key="unit",\n        title="unit",\n        alert_name="Unit",\n        alert_message="Unit",\n        evidence_by_probe={},\n        hidden_expected_stop_reason=(\n            InvestigationStopReason.INSUFFICIENT_EVIDENCE\n        ),\n    )\n\n    reasoner = ScriptedReasoner(\n        [\n            InvestigationDecision(\n                hypotheses=[\n                    hypothesis(\n                        0.1\n                    )\n                ],\n                rationale_summary=(\n                    "insufficient"\n                ),\n                stop=True,\n                stop_reason=(\n                    InvestigationStopReason.INSUFFICIENT_EVIDENCE\n                ),\n            )\n        ]\n    )\n\n    score = asyncio.run(\n        run_scenario(\n            reasoner=reasoner,\n            scenario=scenario,\n            limits=InvestigationLimits(\n                max_iterations=3,\n                max_tool_calls=2,\n                timeout_seconds=10,\n            ),\n            observed_at=NOW,\n        )\n    )\n\n    report = build_report(\n        provider="unit",\n        mode="unit",\n        scenarios=[\n            score\n        ],\n    )\n\n    assert report.scenario_count == 1\n    assert report.outcome_accuracy == 100.0\n    assert report.overall_score >= 90\n'


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
            result.stdout.rstrip() or "<EMPTY>",
            "",
            "STDERR",
            "-" * 120,
            result.stderr.rstrip() or "<EMPTY>",
        ]
    )


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

    package_dir = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "evaluation"
        / "intelligence_benchmark"
    )

    engine_file = (
        package_dir
        / "engine.py"
    )

    scenarios_file = (
        package_dir
        / "scenarios.py"
    )

    init_file = (
        package_dir
        / "__init__.py"
    )

    runner_file = (
        root
        / "scripts"
        / "dev"
        / "run_investigation_intelligence_benchmark_v1.py"
    )

    test_file = (
        root
        / "services"
        / "agent_runtime"
        / "tests"
        / "test_investigation_intelligence_benchmark.py"
    )

    targets = [
        engine_file,
        scenarios_file,
        init_file,
        runner_file,
        test_file,
    ]

    preexisting = {
        path: path.exists()
        for path in targets
    }

    backups = []

    report = [
        "Investigation Intelligence Benchmark v2 Installer",
        f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
        "",
        "Purpose:",
        "- benchmark the actual Investigation control loop",
        "- use hidden-label synthetic Evidence backends",
        "- keep evaluator answers out of LLM prompts",
        "- support explicit real-provider smoke/full runs",
        "- modify no Runtime/Reasoner/Coordinator production file",
        "- send no network request during installation",
        "- v2 fixes the transparent reasoner test to construct InvestigationState with required scope",
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

        write_text(
            engine_file,
            ENGINE_SOURCE,
        )

        write_text(
            scenarios_file,
            SCENARIOS_SOURCE,
        )

        write_text(
            init_file,
            INIT_SOURCE,
        )

        write_text(
            runner_file,
            RUNNER_SOURCE,
        )

        write_text(
            test_file,
            TEST_SOURCE,
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
                    engine_file.relative_to(
                        root
                    )
                ),
                str(
                    scenarios_file.relative_to(
                        root
                    )
                ),
                str(
                    init_file.relative_to(
                        root
                    )
                ),
                str(
                    runner_file.relative_to(
                        root
                    )
                ),
                str(
                    test_file.relative_to(
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
            name=(
                "Intelligence Benchmark focused tests"
            ),
            command=[
                "uv",
                "run",
                "pytest",
                str(
                    test_file.relative_to(
                        root
                    )
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_coordinator.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_reasoner.py"
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
                "Intelligence Benchmark focused tests failed"
            )

        import_check = run_command(
            root=root,
            name=(
                "Benchmark import/scenario preflight"
            ),
            command=[
                "uv",
                "run",
                "python",
                "-c",
                (
                    "from services.agent_runtime.app.evaluation."
                    "intelligence_benchmark import SCENARIOS, "
                    "scenarios_for_mode; "
                    "print('scenario_count='+str(len(SCENARIOS))); "
                    "print('smoke_count='+str(len(scenarios_for_mode('smoke'))))"
                ),
            ],
        )

        add_command(
            report,
            import_check,
        )

        if import_check.returncode != 0:
            raise RuntimeError(
                "Benchmark import preflight failed"
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
                    package_dir.relative_to(
                        root
                    )
                ),
                str(
                    runner_file.relative_to(
                        root
                    )
                ),
                str(
                    test_file.relative_to(
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
            "LIVE BENCHMARK COMMANDS",
        )

        report.extend(
            [
                "Smoke (recommended first):",
                (
                    "uv run python scripts/dev/"
                    "run_investigation_intelligence_benchmark_v1.py "
                    "--provider bailian --mode smoke"
                ),
                "",
                "Full:",
                (
                    "uv run python scripts/dev/"
                    "run_investigation_intelligence_benchmark_v1.py "
                    "--provider bailian --mode full"
                ),
                "",
                "Single scenario:",
                (
                    "uv run python scripts/dev/"
                    "run_investigation_intelligence_benchmark_v1.py "
                    "--provider bailian "
                    "--scenario oom_limit_pressure"
                ),
            ]
        )

        section(
            report,
            "RESULT",
        )

        report.extend(
            [
                "PASSED",
                "",
                "Installed benchmark engine, scenarios, runner and focused tests.",
                "No network request was sent.",
                "No Runtime/Reasoner/Coordinator production file was modified.",
                "",
                "Next step:",
                "Run smoke mode against Bailian/Qwen and upload its text + JSON reports.",
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
            "INVESTIGATION INTELLIGENCE BENCHMARK V2 INSTALLED"
        )
        print("=" * 72)
        print("")
        print(
            "Installation sent no LLM/Kubernetes/Prometheus request."
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

        error_lines = [
            "Investigation Intelligence Benchmark v2 Installer FAILED",
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

        write_text(
            error,
            "\n".join(
                error_lines
            )
            + "\n",
        )

        print("=" * 72)
        print(
            "INVESTIGATION INTELLIGENCE BENCHMARK V2 INSTALL FAILED"
        )
        print("=" * 72)
        print("")
        print(
            "Modified/new files were rolled back where possible."
        )
        print("")
        print("Upload:")
        print(error)

        return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
