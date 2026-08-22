from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import IntEnum
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from services.agent_runtime.app.investigation.coordinator import (
    EvidenceDrivenInvestigationCoordinator,
)
from services.agent_runtime.app.investigation.models import (
    EvidenceItem,
    IncidentHypothesis,
    InvestigationConclusion,
    InvestigationDecision,
    InvestigationLimits,
    InvestigationProbe,
    InvestigationStatus,
    InvestigationStopReason,
)
from services.agent_runtime.app.investigation.reasoner import (
    BaseInvestigationReasoner,
)


class CapabilityLevel(IntEnum):
    """
    Evidence level for one SRE Agent capability.

    L0: absent from the current Agent path
    L1: structural component exists
    L2: contract/schema/guardrail is tested
    L3: closed-loop behavior is tested
    L4: real model or real Lab integration is validated
    L5: real production incidents are validated
    """

    L0 = 0
    L1 = 1
    L2 = 2
    L3 = 3
    L4 = 4
    L5 = 5


LEVEL_SCORE = {
    CapabilityLevel.L0: 0,
    CapabilityLevel.L1: 25,
    CapabilityLevel.L2: 50,
    CapabilityLevel.L3: 75,
    CapabilityLevel.L4: 90,
    CapabilityLevel.L5: 100,
}


class CapabilityAssessment(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    key: str
    name: str
    category: str
    level: CapabilityLevel
    score: int = Field(
        ge=0,
        le=100,
    )
    status: str
    evidence: list[str] = Field(
        default_factory=list,
    )
    gap: str | None = None
    next_step: str | None = None
    weight: float = Field(
        default=1.0,
        gt=0,
    )


class CapabilityExamResult(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    key: str
    name: str
    passed: bool
    detail: str


class CapabilityCategoryScore(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    category: str
    score: float = Field(
        ge=0.0,
        le=100.0,
    )
    capability_count: int = Field(
        ge=1,
    )


class SREAgentCapabilityReport(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    schema_version: str = "v1"
    generated_at: datetime
    overall_score: float = Field(
        ge=0.0,
        le=100.0,
    )
    overall_level: str
    production_validated: bool
    assessments: list[
        CapabilityAssessment
    ]
    behavioral_exams: list[
        CapabilityExamResult
    ]
    categories: list[
        CapabilityCategoryScore
    ]
    top_gaps: list[str]
    recommended_order: list[str]


@dataclass(
    frozen=True,
    slots=True,
)
class RepositorySignals:
    root: Path
    source_text: dict[str, str]
    test_text: str
    real_bailian_decision_validated: bool
    real_bailian_full_rca_validated: bool
    production_incident_validated: bool


def _read_if_exists(
    path: Path,
) -> str:
    if not path.exists():
        return ""

    try:
        return path.read_text(
            encoding="utf-8-sig",
            errors="replace",
        )

    except OSError:
        return ""


def _all_tests_text(
    root: Path,
) -> str:
    tests_root = (
        root
        / "services"
        / "agent_runtime"
        / "tests"
    )

    chunks = []

    if not tests_root.exists():
        return ""

    for path in tests_root.glob(
        "test_*.py"
    ):
        chunks.append(
            _read_if_exists(
                path
            )
        )

    return "\n".join(
        chunks
    )


def collect_repository_signals(
    root: Path,
) -> RepositorySignals:
    files = {
        "investigation_models": (
            root
            / "services"
            / "agent_runtime"
            / "app"
            / "investigation"
            / "models.py"
        ),
        "investigation_coordinator": (
            root
            / "services"
            / "agent_runtime"
            / "app"
            / "investigation"
            / "coordinator.py"
        ),
        "investigation_reasoner": (
            root
            / "services"
            / "agent_runtime"
            / "app"
            / "investigation"
            / "reasoner.py"
        ),
        "investigation_probes": (
            root
            / "services"
            / "agent_runtime"
            / "app"
            / "investigation"
            / "probes.py"
        ),
        "runtime": (
            root
            / "services"
            / "agent_runtime"
            / "app"
            / "runtime"
            / "runtime.py"
        ),
        "registry_factory": (
            root
            / "services"
            / "agent_runtime"
            / "app"
            / "registry"
            / "factory.py"
        ),
        "skill_factory": (
            root
            / "services"
            / "agent_runtime"
            / "app"
            / "skills"
            / "factory.py"
        ),
        "tool_factory": (
            root
            / "services"
            / "agent_runtime"
            / "app"
            / "tools"
            / "factory.py"
        ),
        "historical_runner": (
            root
            / "services"
            / "agent_runtime"
            / "app"
            / "evaluation"
            / "real_incident"
            / "investigation_runner.py"
        ),
        "historical_replay": (
            root
            / "services"
            / "agent_runtime"
            / "app"
            / "evaluation"
            / "real_incident"
            / "historical_replay.py"
        ),
        "real_incident_models": (
            root
            / "services"
            / "agent_runtime"
            / "app"
            / "evaluation"
            / "real_incident"
            / "models.py"
        ),
        "recorder": (
            root
            / "services"
            / "agent_runtime"
            / "app"
            / "incident_evidence"
            / "recorder.py"
        ),
        "action_runtime": (
            root
            / "services"
            / "agent_runtime"
            / "app"
            / "runtime"
            / "action_runtime.py"
        ),
        "verification_runtime": (
            root
            / "services"
            / "agent_runtime"
            / "app"
            / "runtime"
            / "verification_runtime.py"
        ),
    }

    source_text = {
        key: _read_if_exists(
            path
        )
        for key, path
        in files.items()
    }

    bailian_report = _read_if_exists(
        root
        / "bailian_connectivity_preflight_v1_after.txt"
    )

    real_bailian_decision_validated = (
        "LIVE_REQUEST=PASSED"
        in bailian_report
        and "CONTRACT=PASSED"
        in bailian_report
    )

    # v1 intentionally does not call a real model. If a later full
    # historical RCA report exists, the baseline can automatically promote
    # the RCA capability.
    historical_live_reports = [
        _read_if_exists(
            root
            / "historical_llm_investigation_result.json"
        ),
        _read_if_exists(
            root
            / "incident_001_agent_result.json"
        ),
    ]

    real_bailian_full_rca_validated = any(
        (
            '"conclusion"' in text
            and '"root_cause"' in text
        )
        for text in historical_live_reports
        if text
    )

    real_capture_dir = (
        root
        / "evaluation_data"
        / "real_incidents"
    )

    production_incident_validated = bool(
        real_capture_dir.exists()
        and any(
            real_capture_dir.glob(
                "*.json"
            )
        )
    )

    return RepositorySignals(
        root=root,
        source_text=source_text,
        test_text=_all_tests_text(
            root
        ),
        real_bailian_decision_validated=(
            real_bailian_decision_validated
        ),
        real_bailian_full_rca_validated=(
            real_bailian_full_rca_validated
        ),
        production_incident_validated=(
            production_incident_validated
        ),
    )


def _contains(
    signals: RepositorySignals,
    source_key: str,
    *tokens: str,
) -> bool:
    text = signals.source_text.get(
        source_key,
        "",
    )

    return all(
        token in text
        for token in tokens
    )


def _test_contains(
    signals: RepositorySignals,
    *tokens: str,
) -> bool:
    return all(
        token in signals.test_text
        for token in tokens
    )


def _assessment(
    *,
    key: str,
    name: str,
    category: str,
    level: CapabilityLevel,
    evidence: list[str],
    gap: str | None,
    next_step: str | None,
    weight: float = 1.0,
) -> CapabilityAssessment:
    status = {
        CapabilityLevel.L0: "missing",
        CapabilityLevel.L1: "structural",
        CapabilityLevel.L2: "contract_verified",
        CapabilityLevel.L3: "behavior_verified",
        CapabilityLevel.L4: "real_model_or_lab_verified",
        CapabilityLevel.L5: "production_verified",
    }[
        level
    ]

    return CapabilityAssessment(
        key=key,
        name=name,
        category=category,
        level=level,
        score=LEVEL_SCORE[
            level
        ],
        status=status,
        evidence=evidence,
        gap=gap,
        next_step=next_step,
        weight=weight,
    )


def build_capability_assessments(
    signals: RepositorySignals,
) -> list[
    CapabilityAssessment
]:
    assessments: list[
        CapabilityAssessment
    ] = []

    models = signals.source_text[
        "investigation_models"
    ]

    coordinator = signals.source_text[
        "investigation_coordinator"
    ]

    reasoner = signals.source_text[
        "investigation_reasoner"
    ]

    probes = signals.source_text[
        "investigation_probes"
    ]

    runtime = signals.source_text[
        "runtime"
    ]

    registry = signals.source_text[
        "registry_factory"
    ]

    skill_factory = signals.source_text[
        "skill_factory"
    ]

    # Intelligence / reasoning
    event_level = (
        CapabilityLevel.L3
        if (
            "InvestigationScope"
            in models
            and "_scope_from_context"
            in coordinator
            and _test_contains(
                signals,
                "PodOOMKilled",
                "InvestigationScope",
            )
        )
        else CapabilityLevel.L1
    )

    assessments.append(
        _assessment(
            key="event_understanding",
            name="Event / Scope Understanding",
            category="brain",
            level=event_level,
            evidence=[
                "StandardEvent is converted into a trusted InvestigationScope.",
                "Scope is derived from event/resource fields rather than model output.",
            ],
            gap=(
                "Current scope is primarily one-resource oriented; topology/dependency context is not yet first-class."
            ),
            next_step=(
                "Add service/dependency context only when a real investigation capability needs it."
            ),
            weight=1.1,
        )
    )

    hypothesis_level = (
        CapabilityLevel.L4
        if (
            signals.real_bailian_decision_validated
            and "IncidentHypothesis"
            in models
        )
        else (
            CapabilityLevel.L3
            if (
                "IncidentHypothesis"
                in models
                and "hypotheses"
                in reasoner
            )
            else CapabilityLevel.L1
        )
    )

    assessments.append(
        _assessment(
            key="hypothesis_generation",
            name="Hypothesis Generation",
            category="brain",
            level=hypothesis_level,
            evidence=[
                "InvestigationDecision requires one or more bounded hypotheses.",
                (
                    "A real Bailian/Qwen InvestigationDecision has been validated."
                    if signals.real_bailian_decision_validated
                    else "Only deterministic/local behavior is currently evidenced."
                ),
            ],
            gap=(
                "No benchmark yet measures hypothesis diversity, ranking quality, or false-hypothesis rate."
            ),
            next_step=(
                "Add scenario exams that compare hypothesis sets against hidden labels."
            ),
            weight=1.4,
        )
    )

    probe_level = (
        CapabilityLevel.L4
        if (
            signals.real_bailian_decision_validated
            and "next_probe"
            in models
        )
        else CapabilityLevel.L3
    )

    assessments.append(
        _assessment(
            key="probe_selection",
            name="Autonomous Probe Selection",
            category="brain",
            level=probe_level,
            evidence=[
                "Continuing decisions must select exactly one symbolic read-only InvestigationProbe.",
                "The model cannot choose raw Kubernetes verbs, URLs, credentials or PromQL.",
                (
                    "Real Qwen selected a valid next probe."
                    if signals.real_bailian_decision_validated
                    else "Probe selection is currently behavior-tested locally."
                ),
            ],
            gap=(
                "The allowed Probe vocabulary is still narrow."
            ),
            next_step=(
                "Expand Probe/Skill vocabulary based on measured capability gaps."
            ),
            weight=1.5,
        )
    )

    iterative_level = (
        CapabilityLevel.L3
        if (
            "while state.status"
            in coordinator
            and "iteration_count"
            in coordinator
            and "tool_call_count"
            in coordinator
        )
        else CapabilityLevel.L1
    )

    assessments.append(
        _assessment(
            key="iterative_investigation",
            name="Iterative Investigation Loop",
            category="brain",
            level=iterative_level,
            evidence=[
                "Coordinator loops Reason -> Probe -> Evidence -> Reason under hard limits.",
                "Iterations, tool calls, timeout and attempted probes are bounded.",
            ],
            gap=(
                "The loop is implemented, but quality under diverse incidents is not benchmarked."
            ),
            next_step=(
                "Build multi-scenario exams covering direction changes, dead ends and partial evidence."
            ),
            weight=1.5,
        )
    )

    evidence_reasoning_level = (
        CapabilityLevel.L3
        if (
            "supporting_evidence_ids"
            in models
            and "conflicting_evidence_ids"
            in models
            and "_evidence_references_are_valid"
            in coordinator
        )
        else CapabilityLevel.L1
    )

    assessments.append(
        _assessment(
            key="evidence_reasoning",
            name="Evidence Support / Conflict Reasoning",
            category="brain",
            level=evidence_reasoning_level,
            evidence=[
                "Hypotheses carry supporting, conflicting and missing evidence.",
                "Unknown evidence references fail closed.",
                "Conclusions must reference collected trusted evidence.",
            ],
            gap=(
                "No quantitative measure yet scores whether confidence moves correctly when evidence supports or contradicts a hypothesis."
            ),
            next_step=(
                "Add confidence-update and contradiction scenario exams."
            ),
            weight=1.5,
        )
    )

    replanning_level = (
        CapabilityLevel.L3
        if (
            "attempted_probes"
            in models
            and "DUPLICATE_PROBE"
            in models
            and "probe in state.attempted_probes"
            in coordinator
        )
        else CapabilityLevel.L1
    )

    assessments.append(
        _assessment(
            key="replanning",
            name="Replanning / Direction Change",
            category="brain",
            level=replanning_level,
            evidence=[
                "Each reasoner decision receives the full bounded state and prior evidence.",
                "Different probes can be selected across iterations; duplicate probes are blocked.",
            ],
            gap=(
                "There is no dedicated benchmark proving the real model abandons a wrong hypothesis when conflicting evidence arrives."
            ),
            next_step=(
                "Add adversarial scenario exams with misleading first evidence."
            ),
            weight=1.4,
        )
    )

    stop_level = (
        CapabilityLevel.L3
        if (
            "INSUFFICIENT_EVIDENCE"
            in models
            and "NO_SAFE_PROBE"
            in models
            and "SUFFICIENT_EVIDENCE"
            in models
        )
        else CapabilityLevel.L1
    )

    assessments.append(
        _assessment(
            key="stop_and_abstain",
            name="Stop / Abstain Decision",
            category="brain",
            level=stop_level,
            evidence=[
                "Reasoner may stop for sufficient evidence, insufficient evidence or no safe probe.",
                "Internal timeout/tool-limit stop reasons cannot be claimed by the model.",
            ],
            gap=(
                "Abstention quality and overconfidence rate are not calibrated against a benchmark set."
            ),
            next_step=(
                "Measure false-positive RCA vs correct abstention on underdetermined incidents."
            ),
            weight=1.3,
        )
    )

    rca_level = (
        CapabilityLevel.L4
        if signals.real_bailian_full_rca_validated
        else (
            CapabilityLevel.L3
            if (
                "InvestigationConclusion"
                in models
                and "root_cause"
                in models
            )
            else CapabilityLevel.L1
        )
    )

    assessments.append(
        _assessment(
            key="rca_conclusion",
            name="Structured RCA Conclusion",
            category="brain",
            level=rca_level,
            evidence=[
                "Sufficient-evidence stops require a structured InvestigationConclusion.",
                "Conclusion carries root cause, confidence, evidence IDs and remaining uncertainties.",
                (
                    "A real-model full RCA artifact is present."
                    if signals.real_bailian_full_rca_validated
                    else "Full real-model historical RCA is not yet evidenced."
                ),
            ],
            gap=(
                "No real/lab full incident has yet established RCA accuracy."
            ),
            next_step=(
                "After capability baseline, run scored Lab scenarios or curated historical cases."
            ),
            weight=1.7,
        )
    )

    calibration_level = (
        CapabilityLevel.L2
        if (
            "confidence"
            in models
            and "ge=0.0"
            in models
            and "le=1.0"
            in models
        )
        else CapabilityLevel.L0
    )

    assessments.append(
        _assessment(
            key="confidence_calibration",
            name="Confidence Calibration",
            category="brain",
            level=calibration_level,
            evidence=[
                "Hypothesis and conclusion confidence are structurally bounded to [0,1].",
            ],
            gap=(
                "Bounded confidence is not the same as calibrated confidence; no reliability curve/Brier-style evaluation exists."
            ),
            next_step=(
                "Add confidence calibration metrics after scenario labels exist."
            ),
            weight=1.1,
        )
    )

    # Evidence / tools
    kubernetes_probe_present = (
        "KUBERNETES_POD_STATE"
        in models
        and "kubernetes"
        in probes
    )

    kubernetes_level = (
        CapabilityLevel.L3
        if (
            kubernetes_probe_present
            and _test_contains(
                signals,
                "KUBERNETES_POD_STATE",
                "production_signal",
            )
        )
        else (
            CapabilityLevel.L1
            if kubernetes_probe_present
            else CapabilityLevel.L0
        )
    )

    assessments.append(
        _assessment(
            key="kubernetes_investigation",
            name="Kubernetes Investigation",
            category="evidence",
            level=kubernetes_level,
            evidence=[
                "Current autonomous probe vocabulary includes kubernetes_pod_state.",
                "Read-only production-signal trust boundaries are tested.",
            ],
            gap=(
                "Coverage is narrow: no Events, rollout history, controller state, node pressure, scheduling path or network state in the autonomous loop."
            ),
            next_step=(
                "Do not expand blindly; add the next Kubernetes probe when scenario exams expose the need."
            ),
            weight=1.2,
        )
    )

    metric_probe_count = sum(
        token in models
        for token in (
            "PROMETHEUS_MEMORY_WORKING_SET",
            "PROMETHEUS_MEMORY_LIMIT",
            "PROMETHEUS_RESTART_COUNT",
        )
    )

    metrics_level = (
        CapabilityLevel.L3
        if metric_probe_count == 3
        else (
            CapabilityLevel.L1
            if metric_probe_count
            else CapabilityLevel.L0
        )
    )

    assessments.append(
        _assessment(
            key="metrics_investigation",
            name="Metrics Investigation",
            category="evidence",
            level=metrics_level,
            evidence=[
                f"{metric_probe_count}/3 current Prometheus memory/restart probes are present.",
                "The reasoner selects symbolic probes; query templates remain outside model control.",
            ],
            gap=(
                "No CPU, latency, error-rate, saturation, range-query, correlation or arbitrary metric discovery capability."
            ),
            next_step=(
                "Build a bounded Metrics Skill/Probe expansion driven by scenario gaps."
            ),
            weight=1.2,
        )
    )

    logs_present = (
        "LOG"
        in models.upper()
        and "InvestigationProbe"
        in models
    )

    assessments.append(
        _assessment(
            key="logs_investigation",
            name="Logs Investigation",
            category="evidence",
            level=(
                CapabilityLevel.L1
                if logs_present
                else CapabilityLevel.L0
            ),
            evidence=(
                [
                    "A log-related Investigation capability token was detected."
                ]
                if logs_present
                else [
                    "No log probe is present in the current InvestigationProbe contract."
                ]
            ),
            gap=(
                "The autonomous Investigation loop cannot currently request bounded application/container logs."
            ),
            next_step=(
                "High priority after baseline: design a bounded Log Skill with time window, resource scope and secret redaction."
            ),
            weight=1.5,
        )
    )

    change_agent_present = (
        "ChangeAgent"
        in registry
        or "change"
        in registry.lower()
    )

    change_probe_present = (
        "CHANGE"
        in models.upper()
        and "InvestigationProbe"
        in models
    )

    change_level = (
        CapabilityLevel.L2
        if change_probe_present
        else (
            CapabilityLevel.L1
            if change_agent_present
            else CapabilityLevel.L0
        )
    )

    assessments.append(
        _assessment(
            key="change_investigation",
            name="Change / Deployment Correlation",
            category="evidence",
            level=change_level,
            evidence=[
                (
                    "A Change-related structural component exists."
                    if change_agent_present
                    else "No Change component was detected in the current registry source."
                ),
                (
                    "Change is available to the autonomous Investigation loop."
                    if change_probe_present
                    else "Change is not a current InvestigationProbe."
                ),
            ],
            gap=(
                "Even if a ChangeAgent exists in Pipeline, autonomous Investigation cannot yet ask for deployment/config/image change evidence."
            ),
            next_step=(
                "Add a bounded Change Evidence Skill after Logs or when baseline scenarios show change-correlation failures."
            ),
            weight=1.4,
        )
    )

    dependency_present = any(
        token in (
            models
            + reasoner
            + probes
        ).lower()
        for token in (
            "dependency_graph",
            "service_topology",
            "upstream",
            "downstream",
        )
    )

    assessments.append(
        _assessment(
            key="dependency_reasoning",
            name="Dependency / Topology Reasoning",
            category="knowledge",
            level=(
                CapabilityLevel.L1
                if dependency_present
                else CapabilityLevel.L0
            ),
            evidence=[
                (
                    "A topology/dependency token exists in the Investigation path."
                    if dependency_present
                    else "No first-class service topology/dependency contract is present in the Investigation path."
                ),
            ],
            gap=(
                "The Agent cannot yet reason over upstream/downstream service topology as trusted evidence."
            ),
            next_step=(
                "Introduce topology/CMDB evidence only after a scenario requires cross-service reasoning."
            ),
            weight=1.1,
        )
    )

    temporal_level = (
        CapabilityLevel.L2
        if (
            "event_occurred_at"
            in models
            and (
                signals.root
                / "services"
                / "agent_runtime"
                / "app"
                / "investigation"
                / "evidence_time.py"
            ).exists()
        )
        else CapabilityLevel.L1
    )

    assessments.append(
        _assessment(
            key="temporal_reasoning",
            name="Causal / Temporal Evidence Handling",
            category="knowledge",
            level=temporal_level,
            evidence=[
                "Incident-time-aware evidence policy exists for Prometheus probes.",
                "Historical Replay enforces causal time cutoffs.",
            ],
            gap=(
                "This is causal evidence timing, not full timeline reasoning over deploys, logs and human/system events."
            ),
            next_step=(
                "Add multi-source timeline reasoning when Change/Logs evidence enters the loop."
            ),
            weight=1.0,
        )
    )

    rag_present = any(
        (
            signals.root
            / "services"
            / "agent_runtime"
            / "app"
            / name
        ).exists()
        for name in (
            "rag",
            "knowledge",
            "retrieval",
        )
    )

    assessments.append(
        _assessment(
            key="rag_knowledge",
            name="RAG / SRE Knowledge Retrieval",
            category="knowledge",
            level=(
                CapabilityLevel.L1
                if rag_present
                else CapabilityLevel.L0
            ),
            evidence=[
                (
                    "A knowledge/retrieval package exists."
                    if rag_present
                    else "No dedicated RAG/knowledge/retrieval package is present in the current Agent path."
                ),
            ],
            gap=(
                "Runbooks, architecture docs, historical incidents and service knowledge are not yet retrievable by Investigation."
            ),
            next_step=(
                "Build industrial RAG after Logs/Change baseline gaps are quantified."
            ),
            weight=1.4,
        )
    )

    memory_level = (
        CapabilityLevel.L1
        if "MemoryStore"
        in runtime
        else CapabilityLevel.L0
    )

    assessments.append(
        _assessment(
            key="long_term_memory",
            name="Operational Memory / Experience",
            category="knowledge",
            level=memory_level,
            evidence=[
                (
                    "Runtime owns a MemoryStore."
                    if memory_level
                    else "No Runtime MemoryStore was detected."
                ),
                "InvestigationState currently carries incident-local state independently.",
            ],
            gap=(
                "No evidence that Investigation retrieves and applies long-term incident experience."
            ),
            next_step=(
                "Do not expand memory generically; connect historical incident knowledge through explicit retrieval semantics."
            ),
            weight=0.9,
        )
    )

    skill_level = (
        CapabilityLevel.L1
        if (
            "create_skill_registry"
            in runtime
            or "SkillRegistry"
            in skill_factory
        )
        else CapabilityLevel.L0
    )

    assessments.append(
        _assessment(
            key="skill_selection",
            name="Skill Registry / Intelligent Skill Selection",
            category="knowledge",
            level=skill_level,
            evidence=[
                "A Skill registry is wired into Runtime."
                if skill_level
                else "No Skill registry wiring was detected.",
                "The current autonomous Investigation brain selects InvestigationProbe values, not arbitrary Skills.",
            ],
            gap=(
                "Skill infrastructure exists, but intelligent Skill discovery/selection is not yet the Investigation control plane."
            ),
            next_step=(
                "Evolve Probe selection toward capability/Skill selection only after the bounded contracts are preserved."
            ),
            weight=1.2,
        )
    )

    # Remediation / governance
    action_present = (
        "ActionRuntime"
        in runtime
        and bool(
            signals.source_text[
                "action_runtime"
            ]
        )
    )

    assessments.append(
        _assessment(
            key="remediation_planning_execution",
            name="Remediation / Action Runtime",
            category="remediation",
            level=(
                CapabilityLevel.L3
                if (
                    action_present
                    and _test_contains(
                        signals,
                        "ActionRuntime",
                        "approval",
                    )
                )
                else (
                    CapabilityLevel.L1
                    if action_present
                    else CapabilityLevel.L0
                )
            ),
            evidence=[
                "Runtime owns ActionRuntime and approval-linked action execution."
                if action_present
                else "ActionRuntime is not wired.",
            ],
            gap=(
                "Current capability baseline does not claim autonomous production remediation; production execution remains gated."
            ),
            next_step=(
                "Keep execution gated; first improve diagnosis quality before expanding autonomous remediation."
            ),
            weight=0.9,
        )
    )

    approval_present = (
        "ApprovalService"
        in runtime
    )

    assessments.append(
        _assessment(
            key="approval_governance",
            name="Approval / Human Governance",
            category="remediation",
            level=(
                CapabilityLevel.L3
                if (
                    approval_present
                    and _test_contains(
                        signals,
                        "approved",
                        "rejected",
                    )
                )
                else (
                    CapabilityLevel.L1
                    if approval_present
                    else CapabilityLevel.L0
                )
            ),
            evidence=[
                "Explicit ApprovalService is shared by Runtime."
                if approval_present
                else "Approval service is absent.",
            ],
            gap=None,
            next_step=(
                "Preserve human governance while intelligence quality is being raised."
            ),
            weight=0.7,
        )
    )

    verification_present = (
        "VerificationRuntime"
        in runtime
        and bool(
            signals.source_text[
                "verification_runtime"
            ]
        )
    )

    assessments.append(
        _assessment(
            key="post_action_verification",
            name="Post-action Verification",
            category="remediation",
            level=(
                CapabilityLevel.L3
                if (
                    verification_present
                    and _test_contains(
                        signals,
                        "Verification",
                        "passed",
                    )
                )
                else (
                    CapabilityLevel.L1
                    if verification_present
                    else CapabilityLevel.L0
                )
            ),
            evidence=[
                "Verification Runtime/Coordinator infrastructure is wired."
                if verification_present
                else "Verification runtime is absent.",
            ],
            gap=(
                "Verification quality is not currently part of the autonomous Investigation score."
            ),
            next_step=(
                "Later connect diagnosis/remediation quality metrics to verification outcomes."
            ),
            weight=0.8,
        )
    )

    rollback_present = (
        "rollback"
        in (
            runtime
            + signals.source_text[
                "action_runtime"
            ]
        ).lower()
    )

    assessments.append(
        _assessment(
            key="rollback_recovery",
            name="Rollback / Recovery",
            category="remediation",
            level=(
                CapabilityLevel.L2
                if rollback_present
                else CapabilityLevel.L1
            ),
            evidence=[
                (
                    "Rollback/recovery semantics are present in action/runtime code."
                    if rollback_present
                    else "Recovery infrastructure exists indirectly, but a scored rollback capability is not evidenced."
                ),
            ],
            gap=(
                "No capability benchmark currently proves safe end-to-end rollback after a bad remediation."
            ),
            next_step=(
                "Keep this behind governance; add a dedicated rollback exam after diagnosis baseline improves."
            ),
            weight=0.7,
        )
    )

    # Evaluation/data
    historical_present = (
        bool(
            signals.source_text[
                "historical_runner"
            ]
        )
        and bool(
            signals.source_text[
                "historical_replay"
            ]
        )
    )

    assessments.append(
        _assessment(
            key="historical_replay",
            name="Historical Incident Replay",
            category="evaluation",
            level=(
                CapabilityLevel.L3
                if (
                    historical_present
                    and _test_contains(
                        signals,
                        "HistoricalIncidentInvestigationRunner",
                        "replay",
                    )
                )
                else (
                    CapabilityLevel.L1
                    if historical_present
                    else CapabilityLevel.L0
                )
            ),
            evidence=[
                "Ground-truth-free historical investigation runner and causal replay exist."
                if historical_present
                else "Historical replay is absent.",
            ],
            gap=(
                "No real production incident corpus exists yet."
            ),
            next_step=(
                "Use synthetic/lab cases for capability scoring until real incidents naturally accumulate."
            ),
            weight=1.1,
        )
    )

    recorder_present = bool(
        signals.source_text[
            "recorder"
        ]
    )

    assessments.append(
        _assessment(
            key="incident_evidence_recorder",
            name="Incident Evidence Recorder",
            category="evaluation",
            level=(
                CapabilityLevel.L3
                if (
                    recorder_present
                    and _test_contains(
                        signals,
                        "ProductionIncidentEvidenceRecorder",
                        "production_signal",
                    )
                )
                else (
                    CapabilityLevel.L1
                    if recorder_present
                    else CapabilityLevel.L0
                )
            ),
            evidence=[
                "Recorder preserves replay-safe trusted evidence and is wired best-effort."
                if recorder_present
                else "Incident Recorder is absent.",
            ],
            gap=(
                "No production environment is available yet; live capture is intentionally disabled."
            ),
            next_step=(
                "Keep disabled in development; use Lab later for integration validation."
            ),
            weight=0.9,
        )
    )

    production_level = (
        CapabilityLevel.L5
        if signals.production_incident_validated
        else CapabilityLevel.L1
    )

    assessments.append(
        _assessment(
            key="production_incident_validation",
            name="Production Incident Validation",
            category="evaluation",
            level=production_level,
            evidence=[
                (
                    "A real production incident dataset is present."
                    if signals.production_incident_validated
                    else "No real production incident dataset is present; this is expected in the current development phase."
                )
            ],
            gap=(
                None
                if signals.production_incident_validated
                else "Production effectiveness is unproven."
            ),
            next_step=(
                None
                if signals.production_incident_validated
                else "Do not fabricate production evidence; wait for real incidents after deployment."
            ),
            weight=0.6,
        )
    )

    return assessments


NOW = datetime(
    2026,
    8,
    10,
    8,
    30,
    tzinfo=UTC,
)


class _ScriptedReasoner(
    BaseInvestigationReasoner
):
    def __init__(
        self,
        decisions,
    ) -> None:
        self.decisions = list(
            decisions
        )
        self.states = []

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

        if not self.decisions:
            raise RuntimeError(
                "No scripted decision remains"
            )

        decision = self.decisions.pop(
            0
        )

        if isinstance(
            decision,
            Exception,
        ):
            raise decision

        return decision


class _ScriptedProbeExecutor:
    def __init__(
        self,
        evidence_by_probe: dict[
            InvestigationProbe,
            EvidenceItem | Exception,
        ],
    ) -> None:
        self.evidence_by_probe = (
            evidence_by_probe
        )
        self.calls = []

    async def collect(
        self,
        context,
        scope,
        probe,
    ) -> EvidenceItem:
        self.calls.append(
            probe
        )

        value = self.evidence_by_probe[
            probe
        ]

        if isinstance(
            value,
            Exception,
        ):
            raise value

        return value.model_copy(
            deep=True
        )


def _context():
    return SimpleNamespace(
        event=SimpleNamespace(
            signal=SimpleNamespace(
                name="PodOOMKilled",
                message=(
                    "Container restarted"
                ),
            ),
            resources=[
                SimpleNamespace(
                    name="payment-api",
                    namespace="payment",
                    cluster="dev-lab",
                )
            ],
        ),
        metadata={},
        variables={},
    )


def _hypothesis(
    confidence: float,
    *,
    supporting: list[str] | None = None,
    conflicting: list[str] | None = None,
    missing: list[str] | None = None,
) -> IncidentHypothesis:
    return IncidentHypothesis(
        hypothesis_id="memory-pressure",
        cause=(
            "Container memory pressure may have caused termination"
        ),
        confidence=confidence,
        supporting_evidence_ids=(
            supporting
            or []
        ),
        conflicting_evidence_ids=(
            conflicting
            or []
        ),
        missing_evidence=(
            missing
            or []
        ),
    )


def _trusted_evidence(
    *,
    evidence_id: str,
    probe: InvestigationProbe,
    facts: dict[str, Any],
) -> EvidenceItem:
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


async def _exam_multi_step_replan() -> CapabilityExamResult:
    pod = _trusted_evidence(
        evidence_id="e-pod",
        probe=(
            InvestigationProbe.KUBERNETES_POD_STATE
        ),
        facts={
            "oom_killed": True,
            "max_restart_count": 4,
        },
    )

    working = _trusted_evidence(
        evidence_id="e-working",
        probe=(
            InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET
        ),
        facts={
            "value_sum": 500.0,
        },
    )

    reasoner = _ScriptedReasoner(
        [
            InvestigationDecision(
                hypotheses=[
                    _hypothesis(
                        0.35,
                        missing=[
                            "pod termination state"
                        ],
                    )
                ],
                rationale_summary=(
                    "Inspect pod state"
                ),
                next_probe=(
                    InvestigationProbe.KUBERNETES_POD_STATE
                ),
            ),
            InvestigationDecision(
                hypotheses=[
                    _hypothesis(
                        0.72,
                        supporting=[
                            "e-pod"
                        ],
                        missing=[
                            "memory working set"
                        ],
                    )
                ],
                rationale_summary=(
                    "OOM is supported; inspect memory usage"
                ),
                next_probe=(
                    InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET
                ),
            ),
            InvestigationDecision(
                hypotheses=[
                    _hypothesis(
                        0.93,
                        supporting=[
                            "e-pod",
                            "e-working",
                        ],
                    )
                ],
                rationale_summary=(
                    "Trusted evidence is sufficient"
                ),
                stop=True,
                stop_reason=(
                    InvestigationStopReason.SUFFICIENT_EVIDENCE
                ),
                conclusion=(
                    InvestigationConclusion(
                        root_cause=(
                            "Container memory pressure caused OOM termination"
                        ),
                        confidence=0.93,
                        evidence_ids=[
                            "e-pod",
                            "e-working",
                        ],
                    )
                ),
            ),
        ]
    )

    probes = _ScriptedProbeExecutor(
        {
            InvestigationProbe.KUBERNETES_POD_STATE: pod,
            InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET: working,
        }
    )

    coordinator = (
        EvidenceDrivenInvestigationCoordinator(
            reasoner=reasoner,
            probe_executor=probes,
            limits=InvestigationLimits(
                max_iterations=5,
                max_tool_calls=5,
                timeout_seconds=10,
            ),
            utc_clock=lambda: NOW,
        )
    )

    state = await coordinator.investigate(
        _context()
    )

    passed = (
        state.status
        == InvestigationStatus.CONCLUDED
        and state.stop_reason
        == InvestigationStopReason.SUFFICIENT_EVIDENCE
        and state.iteration_count
        == 3
        and state.tool_call_count
        == 2
        and state.attempted_probes
        == [
            InvestigationProbe.KUBERNETES_POD_STATE,
            InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,
        ]
        and state.conclusion
        is not None
        and state.conclusion.confidence
        == 0.93
    )

    return CapabilityExamResult(
        key="multi_step_replan",
        name="Multi-step Replan Exam",
        passed=passed,
        detail=(
            "Reason -> Pod probe -> evidence -> Metrics probe -> evidence -> trusted RCA."
        ),
    )


async def _exam_safe_abstention() -> CapabilityExamResult:
    reasoner = _ScriptedReasoner(
        [
            InvestigationDecision(
                hypotheses=[
                    _hypothesis(
                        0.3,
                        missing=[
                            "pod state"
                        ],
                    )
                ],
                rationale_summary=(
                    "Try one safe probe"
                ),
                next_probe=(
                    InvestigationProbe.KUBERNETES_POD_STATE
                ),
            ),
            InvestigationDecision(
                hypotheses=[
                    _hypothesis(
                        0.2,
                        missing=[
                            "trusted evidence"
                        ],
                    )
                ],
                rationale_summary=(
                    "Evidence is unavailable; abstain"
                ),
                stop=True,
                stop_reason=(
                    InvestigationStopReason.INSUFFICIENT_EVIDENCE
                ),
            ),
        ]
    )

    probes = _ScriptedProbeExecutor(
        {
            InvestigationProbe.KUBERNETES_POD_STATE: (
                RuntimeError(
                    "sensitive backend detail"
                )
            )
        }
    )

    coordinator = (
        EvidenceDrivenInvestigationCoordinator(
            reasoner=reasoner,
            probe_executor=probes,
            utc_clock=lambda: NOW,
        )
    )

    state = await coordinator.investigate(
        _context()
    )

    serialized = json.dumps(
        state.model_dump(
            mode="json"
        ),
        sort_keys=True,
    )

    passed = (
        state.status
        == InvestigationStatus.CONCLUDED
        and state.stop_reason
        == InvestigationStopReason.INSUFFICIENT_EVIDENCE
        and state.conclusion
        is None
        and len(
            state.evidence
        )
        == 1
        and state.evidence[
            0
        ].trusted
        is False
        and "sensitive backend detail"
        not in serialized
    )

    return CapabilityExamResult(
        key="safe_abstention",
        name="Safe Abstention Exam",
        passed=passed,
        detail=(
            "Failed/untrusted evidence must not produce a confident RCA or leak backend error text."
        ),
    )


async def _exam_invalid_evidence_reference() -> CapabilityExamResult:
    reasoner = _ScriptedReasoner(
        [
            InvestigationDecision(
                hypotheses=[
                    _hypothesis(
                        0.99,
                        supporting=[
                            "never-collected"
                        ],
                    )
                ],
                rationale_summary=(
                    "Attempt unsupported conclusion"
                ),
                stop=True,
                stop_reason=(
                    InvestigationStopReason.SUFFICIENT_EVIDENCE
                ),
                conclusion=(
                    InvestigationConclusion(
                        root_cause=(
                            "Unsupported conclusion"
                        ),
                        confidence=0.99,
                        evidence_ids=[
                            "never-collected"
                        ],
                    )
                ),
            )
        ]
    )

    probes = _ScriptedProbeExecutor(
        {}
    )

    coordinator = (
        EvidenceDrivenInvestigationCoordinator(
            reasoner=reasoner,
            probe_executor=probes,
            utc_clock=lambda: NOW,
        )
    )

    state = await coordinator.investigate(
        _context()
    )

    passed = (
        state.status
        == InvestigationStatus.FAILED
        and state.stop_reason
        == InvestigationStopReason.REASONER_ERROR
        and state.failure_code
        == "InvalidEvidenceReference"
        and probes.calls
        == []
    )

    return CapabilityExamResult(
        key="invalid_evidence_reference",
        name="Evidence Hallucination Guard Exam",
        passed=passed,
        detail=(
            "A model cannot cite evidence that the investigation never collected."
        ),
    )


async def _exam_duplicate_probe_guard() -> CapabilityExamResult:
    pod = _trusted_evidence(
        evidence_id="e-pod",
        probe=(
            InvestigationProbe.KUBERNETES_POD_STATE
        ),
        facts={
            "oom_killed": False,
        },
    )

    reasoner = _ScriptedReasoner(
        [
            InvestigationDecision(
                hypotheses=[
                    _hypothesis(
                        0.25,
                        missing=[
                            "pod state"
                        ],
                    )
                ],
                rationale_summary=(
                    "Inspect pod state"
                ),
                next_probe=(
                    InvestigationProbe.KUBERNETES_POD_STATE
                ),
            ),
            InvestigationDecision(
                hypotheses=[
                    _hypothesis(
                        0.25,
                        missing=[
                            "more pod state"
                        ],
                    )
                ],
                rationale_summary=(
                    "Incorrectly request duplicate probe"
                ),
                next_probe=(
                    InvestigationProbe.KUBERNETES_POD_STATE
                ),
            ),
        ]
    )

    probes = _ScriptedProbeExecutor(
        {
            InvestigationProbe.KUBERNETES_POD_STATE: pod,
        }
    )

    coordinator = (
        EvidenceDrivenInvestigationCoordinator(
            reasoner=reasoner,
            probe_executor=probes,
            utc_clock=lambda: NOW,
        )
    )

    state = await coordinator.investigate(
        _context()
    )

    passed = (
        state.status
        == InvestigationStatus.EXHAUSTED
        and state.stop_reason
        == InvestigationStopReason.DUPLICATE_PROBE
        and len(
            probes.calls
        )
        == 1
    )

    return CapabilityExamResult(
        key="duplicate_probe_guard",
        name="Duplicate Probe Guard Exam",
        passed=passed,
        detail=(
            "The coordinator blocks repeated evidence collection loops."
        ),
    )


async def run_behavioral_exams() -> list[
    CapabilityExamResult
]:
    exams = [
        _exam_multi_step_replan,
        _exam_safe_abstention,
        _exam_invalid_evidence_reference,
        _exam_duplicate_probe_guard,
    ]

    results = []

    for exam in exams:
        try:
            result = await exam()

        except Exception as exc:
            result = CapabilityExamResult(
                key=exam.__name__,
                name=exam.__name__,
                passed=False,
                detail=(
                    f"Exam raised {type(exc).__name__}"
                ),
            )

        results.append(
            result
        )

    return results


def _category_scores(
    assessments: list[
        CapabilityAssessment
    ],
) -> list[
    CapabilityCategoryScore
]:
    categories = sorted(
        {
            item.category
            for item in assessments
        }
    )

    results = []

    for category in categories:
        items = [
            item
            for item in assessments
            if item.category
            == category
        ]

        total_weight = sum(
            item.weight
            for item in items
        )

        score = sum(
            item.score
            * item.weight
            for item in items
        ) / total_weight

        results.append(
            CapabilityCategoryScore(
                category=category,
                score=round(
                    score,
                    1,
                ),
                capability_count=len(
                    items
                ),
            )
        )

    return results


def _overall_level(
    score: float,
) -> str:
    if score < 20:
        return "L0.x - framework infancy"

    if score < 40:
        return "L1.x - componentized assistant"

    if score < 60:
        return "L2.x - bounded SRE agent"

    if score < 80:
        return "L3.x - behaviorally capable SRE agent"

    if score < 95:
        return "L4.x - lab/real-model validated SRE agent"

    return "L5 - production-validated autonomous SRE"


def build_report(
    root: Path,
) -> SREAgentCapabilityReport:
    signals = (
        collect_repository_signals(
            root
        )
    )

    assessments = (
        build_capability_assessments(
            signals
        )
    )

    exams = asyncio.run(
        run_behavioral_exams()
    )

    total_weight = sum(
        item.weight
        for item in assessments
    )

    base_score = sum(
        item.score
        * item.weight
        for item in assessments
    ) / total_weight

    exam_pass_rate = (
        sum(
            1
            for exam in exams
            if exam.passed
        )
        / len(
            exams
        )
    )

    # Behavioral exams affect only a modest portion of the score because
    # they validate the control loop, not the breadth of SRE knowledge.
    overall_score = (
        base_score
        * 0.9
        + exam_pass_rate
        * 100.0
        * 0.1
    )

    categories = (
        _category_scores(
            assessments
        )
    )

    gap_candidates = sorted(
        (
            item
            for item in assessments
            if item.gap
            is not None
        ),
        key=lambda item: (
            item.score,
            -item.weight,
            item.name,
        ),
    )

    top_gaps = [
        (
            f"{item.name}: "
            f"{item.gap}"
        )
        for item in gap_candidates[
            :8
        ]
    ]

    recommended_order = [
        "1. Benchmark real Investigation Brain quality with labeled synthetic/lab scenarios.",
        "2. Add Logs Investigation capability.",
        "3. Add Change / Deployment evidence capability.",
        "4. Add richer Metrics capability and contradiction/replanning exams.",
        "5. Add RAG for runbooks, architecture and historical incidents.",
        "6. Build Local SRE Lab only when tool realism is needed.",
        "7. Preserve Approval/Verification gates; do not expand autonomous production writes yet.",
    ]

    return SREAgentCapabilityReport(
        generated_at=datetime.now(
            UTC
        ),
        overall_score=round(
            overall_score,
            1,
        ),
        overall_level=_overall_level(
            overall_score
        ),
        production_validated=(
            signals.production_incident_validated
        ),
        assessments=assessments,
        behavioral_exams=exams,
        categories=categories,
        top_gaps=top_gaps,
        recommended_order=(
            recommended_order
        ),
    )


def render_text_report(
    report: SREAgentCapabilityReport,
) -> str:
    lines = [
        "=" * 88,
        "SRE AGENT CAPABILITY BASELINE v1",
        "=" * 88,
        "",
        f"GeneratedAt: {report.generated_at.isoformat()}",
        f"OverallScore: {report.overall_score:.1f}/100",
        f"OverallLevel: {report.overall_level}",
        (
            "ProductionValidated: "
            + str(
                report.production_validated
            )
        ),
        "",
        "Important:",
        "- L3 means behavior-tested, not production-proven.",
        "- L4 means real-model or Lab integration evidence exists.",
        "- L5 is reserved for real production incident validation.",
        "",
        "CATEGORY SCORES",
        "-" * 88,
    ]

    for category in report.categories:
        lines.append(
            (
                f"{category.category:<14}"
                f"{category.score:>6.1f}/100 "
                f"({category.capability_count} capabilities)"
            )
        )

    lines.extend(
        [
            "",
            "CAPABILITY MATRIX",
            "-" * 88,
        ]
    )

    for item in report.assessments:
        lines.append(
            (
                f"[{item.level.name}] "
                f"{item.name:<38} "
                f"{item.score:>3}/100 "
                f"{item.status}"
            )
        )

        for evidence in item.evidence:
            lines.append(
                f"    evidence: {evidence}"
            )

        if item.gap:
            lines.append(
                f"    gap:      {item.gap}"
            )

        if item.next_step:
            lines.append(
                f"    next:     {item.next_step}"
            )

    lines.extend(
        [
            "",
            "BEHAVIORAL EXAMS",
            "-" * 88,
        ]
    )

    for exam in report.behavioral_exams:
        lines.append(
            (
                f"[{'PASS' if exam.passed else 'FAIL'}] "
                f"{exam.name}"
            )
        )
        lines.append(
            f"    {exam.detail}"
        )

    lines.extend(
        [
            "",
            "TOP GAPS",
            "-" * 88,
        ]
    )

    for gap in report.top_gaps:
        lines.append(
            f"- {gap}"
        )

    lines.extend(
        [
            "",
            "RECOMMENDED DEVELOPMENT ORDER",
            "-" * 88,
            *report.recommended_order,
            "",
            "=" * 88,
        ]
    )

    return "\n".join(
        lines
    ) + "\n"


__all__ = [
    "CapabilityAssessment",
    "CapabilityCategoryScore",
    "CapabilityExamResult",
    "CapabilityLevel",
    "SREAgentCapabilityReport",
    "build_capability_assessments",
    "build_report",
    "collect_repository_signals",
    "render_text_report",
    "run_behavioral_exams",
]
