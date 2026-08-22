from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from services.agent_runtime.app.evaluation.real_incident.llm_run import (
    create_historical_llm_runtime,
)
from services.agent_runtime.app.investigation.coordinator import (
    EvidenceDrivenInvestigationCoordinator,
)
from services.agent_runtime.app.investigation.models import (
    EvidenceItem,
    InvestigationDecision,
    InvestigationLimits,
    InvestigationProbe,
    InvestigationState,
    InvestigationStopReason,
    default_investigation_probes,
)
from services.agent_runtime.app.investigation.reasoner import (
    BaseInvestigationReasoner,
)


class IntelligenceBenchmarkError(
    RuntimeError
):
    pass


class _BenchmarkMonotonicClock:
    """
    Deterministic logical clock for Intelligence Benchmark control limits.

    Real provider/network latency must not decide whether an intelligence
    scenario reaches its terminal reasoning step. asyncio.wait_for still keeps
    the coordinator's per-call timeout protection, while cumulative benchmark
    elapsed time advances only by a tiny logical step per control check.
    """

    def __init__(
        self,
        *,
        step_seconds: float = 0.001,
    ) -> None:
        self._value = 0.0
        self._step_seconds = (
            step_seconds
        )

    def __call__(
        self,
    ) -> float:
        current = self._value
        self._value += (
            self._step_seconds
        )
        return current


class BenchmarkScenario(BaseModel):
    """
    One hidden-label Investigation exam.

    hidden_* fields are evaluator-only. They never enter the Agent context,
    InvestigationScope, LLM prompt, EvidenceItem facts or decision history.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    key: str
    title: str

    alert_name: str
    alert_message: str

    resource: str = "payment-api"
    namespace: str = "payment"
    cluster: str = "benchmark-lab"
    event_occurred_at: datetime | None = None

    evidence_by_probe: dict[
        InvestigationProbe,
        dict[str, Any] | str,
    ]

    hidden_expected_stop_reason: (
        InvestigationStopReason
    )

    hidden_acceptable_stop_reasons: list[
        InvestigationStopReason
    ] = Field(
        default_factory=list
    )

    hidden_required_probes: list[
        InvestigationProbe
    ] = Field(
        default_factory=list
    )

    hidden_preferred_first_probes: list[
        InvestigationProbe
    ] = Field(
        default_factory=list
    )

    hidden_root_cause_keyword_groups: list[
        list[str]
    ] = Field(
        default_factory=list
    )

    hidden_missing_capability_keywords: list[
        str
    ] = Field(
        default_factory=list
    )

    hidden_max_reasonable_tool_calls: int = Field(
        default=4,
        ge=0,
        le=10,
    )


class ScenarioScore(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    scenario_key: str
    title: str

    score: float = Field(
        ge=0.0,
        le=100.0,
    )

    expected_stop_reason: str
    outcome_correct: bool
    grounding_correct: bool
    required_probe_coverage: float
    first_probe_quality: bool | None
    tool_efficiency: float
    root_cause_or_abstention_correct: bool
    missing_capability_awareness: bool | None

    final_status: str
    final_stop_reason: str | None
    failure_code: str | None
    epistemic_guard_code: str | None
    guard_rescued: bool

    attempted_probes: list[str]
    tool_call_count: int
    iteration_count: int

    conclusion_root_cause: str | None
    conclusion_confidence: float | None

    decision_trace: list[
        dict[str, Any]
    ]

    notes: list[str] = Field(
        default_factory=list
    )


class IntelligenceBenchmarkReport(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    schema_version: str = "v1"
    generated_at: datetime

    provider: str
    mode: str

    scenario_count: int
    overall_score: float = Field(
        ge=0.0,
        le=100.0,
    )

    outcome_accuracy: float = Field(
        ge=0.0,
        le=100.0,
    )

    abstention_accuracy: float = Field(
        ge=0.0,
        le=100.0,
    )

    sufficient_evidence_accuracy: float = Field(
        ge=0.0,
        le=100.0,
    )

    average_tool_calls: float = Field(
        ge=0.0,
    )

    guard_rescue_count: int = Field(
        ge=0,
    )

    guard_rescue_rate: float = Field(
        ge=0.0,
        le=100.0,
    )

    scenarios: list[
        ScenarioScore
    ]

    strongest_signals: list[str]
    weakest_signals: list[str]


def benchmark_evidence_id(
    scenario_key: str,
    probe: InvestigationProbe,
) -> str:
    """
    Return a stable EvidenceItem-compatible benchmark ID.

    Historical IDs that already fit the domain limit are preserved exactly.
    Only overlong scenario/probe combinations are compacted, using a readable
    prefix plus a deterministic SHA-256 suffix. This keeps evaluator fixtures
    stable while preventing a benchmark key from violating EvidenceItem's
    max_length=64 contract.
    """

    raw = (
        f"{scenario_key}:"
        f"{probe.value}"
    )

    if len(
        raw
    ) <= 64:
        return raw

    digest = sha256(
        raw.encode(
            "utf-8"
        )
    ).hexdigest()[
        :12
    ]

    prefix_length = (
        64
        - len(
            digest
        )
        - 1
    )

    return (
        raw[
            :prefix_length
        ]
        + ":"
        + digest
    )


class BenchmarkProbeExecutor:
    """
    Synthetic evidence backend for model-intelligence evaluation.

    The model sees only the evidence corresponding to probes it chose.
    Hidden labels remain inside BenchmarkScenario and never cross this class
    into EvidenceItem.
    """

    def __init__(
        self,
        scenario: BenchmarkScenario,
        *,
        observed_at: datetime,
    ) -> None:
        self.scenario = scenario
        self.observed_at = observed_at
        self.calls: list[
            InvestigationProbe
        ] = []

    def available_probes(
        self,
        context,
    ) -> list[InvestigationProbe]:
        """
        Preserve the historical five-probe baseline unless the scenario
        explicitly supplies Change evidence.

        This mirrors runtime capability gating: adding a new enum does not
        silently expand old benchmark vocabularies.
        """

        probes = default_investigation_probes()

        if (
            InvestigationProbe.KUBERNETES_WORKLOAD_CHANGE
            in self.scenario.evidence_by_probe
        ):
            probes.append(
                InvestigationProbe.KUBERNETES_WORKLOAD_CHANGE
            )

        if (
            InvestigationProbe.KUBERNETES_CONFIG_CHANGE
            in self.scenario.evidence_by_probe
        ):
            probes.append(
                InvestigationProbe.KUBERNETES_CONFIG_CHANGE
            )

        return probes

    async def collect(
        self,
        context,
        scope,
        probe,
    ) -> EvidenceItem:
        self.calls.append(
            probe
        )

        value = (
            self.scenario
            .evidence_by_probe
            .get(
                probe
            )
        )

        if isinstance(
            value,
            str,
        ):
            raise RuntimeError(
                "Benchmark probe unavailable"
            )

        if value is None:
            raise RuntimeError(
                "Benchmark probe has no observation"
            )

        if probe in {
            InvestigationProbe.KUBERNETES_WORKLOAD_CHANGE,
            InvestigationProbe.KUBERNETES_CONFIG_CHANGE,
        }:
            source = "kubernetes_change"

        elif probe in {
            InvestigationProbe.KUBERNETES_POD_STATE,
            (
                InvestigationProbe
                .KUBERNETES_PREVIOUS_CONTAINER_LOGS
            ),
        }:
            source = "kubernetes"

        else:
            source = "prometheus"

        return EvidenceItem(
            evidence_id=benchmark_evidence_id(
                self.scenario.key,
                probe,
            ),
            probe=probe,
            source=source,
            success=True,
            trusted=True,
            production_signal=True,
            reliability=1.0,
            observed_at=self.observed_at,
            facts=dict(
                value
            ),
        )


class TracingReasoner(
    BaseInvestigationReasoner
):
    """
    Transparent delegate that records the actual Agent decisions.

    It does not modify prompts, decisions, state or provider behavior.
    """

    def __init__(
        self,
        delegate: BaseInvestigationReasoner,
    ) -> None:
        if not isinstance(
            delegate,
            BaseInvestigationReasoner,
        ):
            raise TypeError(
                "Benchmark delegate reasoner is invalid"
            )

        self.delegate = delegate

        self.decisions: list[
            InvestigationDecision
        ] = []

        self.states: list[
            InvestigationState
        ] = []

    async def decide(
        self,
        scope,
        state,
    ) -> InvestigationDecision:
        self.states.append(
            state.model_copy(
                deep=True
            )
        )

        decision = await (
            self.delegate.decide(
                scope,
                state,
            )
        )

        self.decisions.append(
            decision.model_copy(
                deep=True
            )
        )

        return decision


def _context(
    scenario: BenchmarkScenario,
):
    return SimpleNamespace(
        event=SimpleNamespace(
            header=SimpleNamespace(
                occurred_at=(
                    scenario.event_occurred_at
                ),
            ),
            signal=SimpleNamespace(
                name=scenario.alert_name,
                message=(
                    scenario.alert_message
                ),
            ),
            resources=[
                SimpleNamespace(
                    name=scenario.resource,
                    namespace=scenario.namespace,
                    cluster=scenario.cluster,
                )
            ],
        ),
        metadata={},
        variables={},
    )


def _normalize_text(
    value: str | None,
) -> str:
    if not value:
        return ""

    return (
        value
        .strip()
        .lower()
    )


def _missing_capability_text(
    decisions: list[
        InvestigationDecision
    ],
) -> str:
    """
    Return only explicit unresolved-evidence language.

    Hypothesis causes, rationale prose and conclusion root-cause text are
    intentionally excluded. Guessing "application panic" is not the same as
    recognizing that application/container logs are missing.
    """

    fragments: list[
        str
    ] = []

    for decision in decisions:
        for hypothesis in decision.hypotheses:
            fragments.extend(
                hypothesis.missing_evidence
            )

        if decision.conclusion is not None:
            fragments.extend(
                decision.conclusion.remaining_uncertainties
            )

    return _normalize_text(
        "\n".join(
            fragments
        )
    )


def _keyword_groups_match(
    text: str,
    groups: list[
        list[str]
    ],
) -> bool:
    normalized = _normalize_text(
        text
    )

    if not groups:
        return True

    for group in groups:
        if not any(
            _normalize_text(
                token
            )
            in normalized
            for token in group
        ):
            return False

    return True


def _decision_trace(
    decisions: list[
        InvestigationDecision
    ],
) -> list[
    dict[str, Any]
]:
    trace = []

    for index, decision in enumerate(
        decisions,
        start=1,
    ):
        trace.append(
            {
                "iteration": index,
                "hypotheses": [
                    {
                        "hypothesis_id": (
                            item.hypothesis_id
                        ),
                        "cause": item.cause,
                        "confidence": (
                            item.confidence
                        ),
                        "supporting_evidence_ids": list(
                            item.supporting_evidence_ids
                        ),
                        "conflicting_evidence_ids": list(
                            item.conflicting_evidence_ids
                        ),
                        "missing_evidence": list(
                            item.missing_evidence
                        ),
                        "optional_evidence": list(
                            item.optional_evidence
                        ),
                    }
                    for item in decision.hypotheses
                ],
                "rationale_summary": (
                    decision.rationale_summary
                ),
                "stop": decision.stop,
                "stop_reason": (
                    decision.stop_reason.value
                    if decision.stop_reason
                    is not None
                    else None
                ),
                "next_probe": (
                    decision.next_probe.value
                    if decision.next_probe
                    is not None
                    else None
                ),
                "conclusion": (
                    decision.conclusion.model_dump(
                        mode="json"
                    )
                    if decision.conclusion
                    is not None
                    else None
                ),
            }
        )

    return trace


def score_scenario(
    *,
    scenario: BenchmarkScenario,
    state: InvestigationState,
    decisions: list[
        InvestigationDecision
    ],
) -> ScenarioScore:
    attempted = list(state.attempted_probes)
    expected_stop = scenario.hidden_expected_stop_reason

    accepted_stop_reasons = {
        expected_stop,
        *scenario.hidden_acceptable_stop_reasons,
    }

    legitimate_terminal = (
        state.status.value == "concluded"
        and state.stop_reason in accepted_stop_reasons
    )
    outcome_correct = legitimate_terminal

    if expected_stop == InvestigationStopReason.SUFFICIENT_EVIDENCE:
        if not legitimate_terminal or state.conclusion is None:
            grounding_correct = False
        else:
            trusted_ids = {
                item.evidence_id
                for item in state.evidence
                if (
                    item.success
                    and item.trusted
                    and item.production_signal
                )
            }
            conclusion_ids = set(state.conclusion.evidence_ids)
            grounding_correct = (
                bool(conclusion_ids)
                and conclusion_ids.issubset(trusted_ids)
            )
    else:
        grounding_correct = (
            legitimate_terminal
            and state.conclusion is None
        )

    required = set(scenario.hidden_required_probes)
    attempted_set = set(attempted)
    required_probe_coverage = (
        len(required & attempted_set) / len(required)
        if required
        else 1.0
    )

    if scenario.hidden_preferred_first_probes:
        first_probe_quality = (
            bool(attempted)
            and attempted[0]
            in scenario.hidden_preferred_first_probes
        )
    else:
        first_probe_quality = None

    max_calls = scenario.hidden_max_reasonable_tool_calls
    if max_calls <= 0:
        tool_efficiency = 1.0 if state.tool_call_count == 0 else 0.0
    elif state.tool_call_count <= max_calls:
        tool_efficiency = 1.0
    else:
        tool_efficiency = max(
            0.0,
            1.0 - (
                state.tool_call_count - max_calls
            ) / max_calls,
        )

    if expected_stop == InvestigationStopReason.SUFFICIENT_EVIDENCE:
        root_cause_or_abstention_correct = (
            legitimate_terminal
            and state.conclusion is not None
            and _keyword_groups_match(
                state.conclusion.root_cause,
                scenario.hidden_root_cause_keyword_groups,
            )
        )
    else:
        root_cause_or_abstention_correct = (
            legitimate_terminal
            and state.conclusion is None
        )

    if scenario.hidden_missing_capability_keywords:
        reasoner_text = _missing_capability_text(decisions)
        missing_capability_awareness = any(
            _normalize_text(keyword) in reasoner_text
            for keyword
            in scenario.hidden_missing_capability_keywords
        )
    else:
        missing_capability_awareness = None

    score = 0.0
    score += 30.0 if outcome_correct else 0.0
    score += 20.0 if grounding_correct else 0.0

    probe_weight = 30.0 if first_probe_quality is None else 20.0
    score += required_probe_coverage * probe_weight

    if first_probe_quality is not None:
        score += 10.0 if first_probe_quality else 0.0

    score += tool_efficiency * 10.0
    score += 10.0 if root_cause_or_abstention_correct else 0.0

    guard_rescued = (
        state.epistemic_guard_code
        is not None
        and outcome_correct
    )

    if guard_rescued:
        score = min(
            score,
            85.0,
        )

    notes: list[str] = []

    if (
        legitimate_terminal
        and state.stop_reason is not None
        and state.stop_reason != expected_stop
    ):
        notes.append(
            "Scenario accepted an alternate safe abstention stop reason: "
            + state.stop_reason.value
            + "."
        )

    if guard_rescued:
        notes.append(
            "Epistemic guard converted an unsupported sufficient-evidence "
            "decision into safe insufficient_evidence."
        )

    if not outcome_correct:
        notes.append(
            "Final stop reason/status did not match the hidden evaluator label."
        )

    if state.status.value == "failed":
        notes.append(
            "Failed investigation is not counted as a valid abstention."
        )

    if (
        expected_stop != InvestigationStopReason.SUFFICIENT_EVIDENCE
        and state.conclusion is not None
    ):
        notes.append(
            "Agent produced an RCA where the benchmark expected abstention."
        )

    if missing_capability_awareness is False:
        notes.append(
            "Agent did not explicitly recognize the expected missing capability."
        )

    return ScenarioScore(
        scenario_key=scenario.key,
        title=scenario.title,
        expected_stop_reason=expected_stop.value,
        score=round(
            min(100.0, max(0.0, score)),
            1,
        ),
        outcome_correct=outcome_correct,
        grounding_correct=grounding_correct,
        required_probe_coverage=round(
            required_probe_coverage,
            3,
        ),
        first_probe_quality=first_probe_quality,
        tool_efficiency=round(
            tool_efficiency,
            3,
        ),
        root_cause_or_abstention_correct=(
            root_cause_or_abstention_correct
        ),
        missing_capability_awareness=(
            missing_capability_awareness
        ),
        final_status=state.status.value,
        final_stop_reason=(
            state.stop_reason.value
            if state.stop_reason is not None
            else None
        ),
        failure_code=state.failure_code,
        epistemic_guard_code=(
            state.epistemic_guard_code
        ),
        guard_rescued=guard_rescued,
        attempted_probes=[
            item.value
            for item in attempted
        ],
        tool_call_count=state.tool_call_count,
        iteration_count=state.iteration_count,
        conclusion_root_cause=(
            state.conclusion.root_cause
            if state.conclusion is not None
            else None
        ),
        conclusion_confidence=(
            state.conclusion.confidence
            if state.conclusion is not None
            else None
        ),
        decision_trace=_decision_trace(decisions),
        notes=notes,
    )


async def run_scenario(
    *,
    reasoner: BaseInvestigationReasoner,
    scenario: BenchmarkScenario,
    limits: InvestigationLimits,
    observed_at: datetime,
) -> ScenarioScore:
    tracing = TracingReasoner(
        reasoner
    )

    probes = BenchmarkProbeExecutor(
        scenario,
        observed_at=observed_at,
    )

    coordinator = (
        EvidenceDrivenInvestigationCoordinator(
            reasoner=tracing,
            probe_executor=probes,
            limits=limits,
            monotonic_clock=(
                _BenchmarkMonotonicClock()
            ),
            utc_clock=lambda: observed_at,
        )
    )

    state = await coordinator.investigate(
        _context(
            scenario
        )
    )

    return score_scenario(
        scenario=scenario,
        state=state,
        decisions=tracing.decisions,
    )


def build_bailian_reasoner(
    *,
    provider_name: str,
    limits: InvestigationLimits,
) -> BaseInvestigationReasoner:
    runtime = (
        create_historical_llm_runtime(
            limits=limits,
            provider_name=provider_name,
        )
    )

    coordinator = getattr(
        runtime,
        "investigation_coordinator",
        None,
    )

    reasoner = getattr(
        coordinator,
        "reasoner",
        None,
    )

    if not isinstance(
        reasoner,
        BaseInvestigationReasoner,
    ):
        raise IntelligenceBenchmarkError(
            "Benchmark could not obtain the canonical Investigation reasoner"
        )

    return reasoner


def build_report(
    *,
    provider: str,
    mode: str,
    scenarios: list[
        ScenarioScore
    ],
) -> IntelligenceBenchmarkReport:
    if not scenarios:
        raise IntelligenceBenchmarkError(
            "Benchmark produced no scenario results"
        )

    overall_score = (
        sum(item.score for item in scenarios)
        / len(scenarios)
    )

    outcome_accuracy = (
        sum(
            1
            for item in scenarios
            if item.outcome_correct
        )
        / len(scenarios)
        * 100.0
    )

    expected_abstention_cases = [
        item
        for item in scenarios
        if item.expected_stop_reason
        != InvestigationStopReason.SUFFICIENT_EVIDENCE.value
    ]

    abstention_accuracy = (
        sum(
            1
            for item in expected_abstention_cases
            if (
                item.outcome_correct
                and item.root_cause_or_abstention_correct
            )
        )
        / len(expected_abstention_cases)
        * 100.0
        if expected_abstention_cases
        else 0.0
    )

    expected_sufficient_cases = [
        item
        for item in scenarios
        if item.expected_stop_reason
        == InvestigationStopReason.SUFFICIENT_EVIDENCE.value
    ]

    sufficient_evidence_accuracy = (
        sum(
            1
            for item in expected_sufficient_cases
            if (
                item.outcome_correct
                and item.grounding_correct
                and item.root_cause_or_abstention_correct
            )
        )
        / len(expected_sufficient_cases)
        * 100.0
        if expected_sufficient_cases
        else 0.0
    )

    average_tool_calls = (
        sum(
            item.tool_call_count
            for item in scenarios
        )
        / len(scenarios)
    )

    guard_rescue_count = sum(
        1
        for item in scenarios
        if item.guard_rescued
    )

    guard_rescue_rate = (
        guard_rescue_count
        / len(scenarios)
        * 100.0
    )

    ordered = sorted(
        scenarios,
        key=lambda item: (
            item.score,
            item.scenario_key,
        ),
    )

    weakest = [
        f"{item.scenario_key}: {item.score:.1f}/100"
        for item in ordered[:3]
    ]

    strongest = [
        f"{item.scenario_key}: {item.score:.1f}/100"
        for item in reversed(ordered[-3:])
    ]

    return IntelligenceBenchmarkReport(
        generated_at=datetime.now(UTC),
        provider=provider,
        mode=mode,
        scenario_count=len(scenarios),
        overall_score=round(
            overall_score,
            1,
        ),
        outcome_accuracy=round(
            outcome_accuracy,
            1,
        ),
        abstention_accuracy=round(
            abstention_accuracy,
            1,
        ),
        sufficient_evidence_accuracy=round(
            sufficient_evidence_accuracy,
            1,
        ),
        average_tool_calls=round(
            average_tool_calls,
            2,
        ),
        guard_rescue_count=(
            guard_rescue_count
        ),
        guard_rescue_rate=round(
            guard_rescue_rate,
            1,
        ),
        scenarios=scenarios,
        strongest_signals=strongest,
        weakest_signals=weakest,
    )


def render_report(
    report: IntelligenceBenchmarkReport,
) -> str:
    lines = [
        "=" * 96,
        "INVESTIGATION INTELLIGENCE BENCHMARK v1",
        "=" * 96,
        "",
        f"GeneratedAt: {report.generated_at.isoformat()}",
        f"Provider: {report.provider}",
        f"Mode: {report.mode}",
        f"Scenarios: {report.scenario_count}",
        "",
        f"OverallScore: {report.overall_score:.1f}/100",
        f"OutcomeAccuracy: {report.outcome_accuracy:.1f}%",
        f"AbstentionAccuracy: {report.abstention_accuracy:.1f}%",
        (
            "SufficientEvidenceAccuracy: "
            f"{report.sufficient_evidence_accuracy:.1f}%"
        ),
        f"AverageToolCalls: {report.average_tool_calls:.2f}",
        f"GuardRescueCount: {report.guard_rescue_count}",
        f"GuardRescueRate: {report.guard_rescue_rate:.1f}%",
        "",
        "Important:",
        "- This is a controlled synthetic-evidence intelligence benchmark.",
        "- The actual LLM Investigation reasoner is used in live mode.",
        "- Hidden evaluator labels never enter the Agent prompt.",
        "- This is stronger than unit testing but is not a production validation.",
        "",
        "SCENARIOS",
        "-" * 96,
    ]

    for item in report.scenarios:
        lines.extend(
            [
                "",
                (
                    f"[{item.score:5.1f}] "
                    f"{item.scenario_key} - {item.title}"
                ),
                (
                    "  outcome_correct="
                    f"{item.outcome_correct}"
                ),
                (
                    "  grounding_correct="
                    f"{item.grounding_correct}"
                ),
                (
                    "  required_probe_coverage="
                    f"{item.required_probe_coverage:.3f}"
                ),
                (
                    "  first_probe_quality="
                    f"{item.first_probe_quality}"
                ),
                (
                    "  tool_efficiency="
                    f"{item.tool_efficiency:.3f}"
                ),
                (
                    "  root_cause_or_abstention_correct="
                    f"{item.root_cause_or_abstention_correct}"
                ),
                (
                    "  missing_capability_awareness="
                    f"{item.missing_capability_awareness}"
                ),
                (
                    "  expected_stop_reason="
                    f"{item.expected_stop_reason}"
                ),
                (
                    "  final="
                    f"{item.final_status}/"
                    f"{item.final_stop_reason}"
                ),
                (
                    "  failure_code="
                    f"{item.failure_code}"
                ),
                (
                    "  epistemic_guard_code="
                    f"{item.epistemic_guard_code}"
                ),
                (
                    "  guard_rescued="
                    f"{item.guard_rescued}"
                ),
                (
                    "  probes="
                    + ", ".join(
                        item.attempted_probes
                    )
                ),
                (
                    "  conclusion="
                    + (
                        item.conclusion_root_cause
                        or "<NONE>"
                    )
                ),
                (
                    "  confidence="
                    + (
                        str(
                            item.conclusion_confidence
                        )
                        if item.conclusion_confidence
                        is not None
                        else "<NONE>"
                    )
                ),
            ]
        )

        for note in item.notes:
            lines.append(
                f"  note: {note}"
            )

        lines.append(
            "  decision_trace:"
        )

        for decision in item.decision_trace:
            lines.append(
                "    "
                + json.dumps(
                    decision,
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )

    lines.extend(
        [
            "",
            "STRONGEST",
            "-" * 96,
            *[
                f"- {value}"
                for value
                in report.strongest_signals
            ],
            "",
            "WEAKEST",
            "-" * 96,
            *[
                f"- {value}"
                for value
                in report.weakest_signals
            ],
            "",
            "=" * 96,
        ]
    )

    return "\n".join(
        lines
    ) + "\n"


__all__ = [
    "BenchmarkProbeExecutor",
    "BenchmarkScenario",
    "IntelligenceBenchmarkError",
    "IntelligenceBenchmarkReport",
    "ScenarioScore",
    "TracingReasoner",
    "build_bailian_reasoner",
    "build_report",
    "render_report",
    "run_scenario",
    "score_scenario",
]
