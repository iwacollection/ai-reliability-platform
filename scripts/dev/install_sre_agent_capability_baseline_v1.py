from __future__ import annotations

import shutil
import subprocess
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


VERSION = "sre-agent-capability-baseline-v1"

AFTER_NAME = (
    "sre_agent_capability_baseline_v1_after.txt"
)

ERROR_NAME = (
    "sre_agent_capability_baseline_v1_install_error.txt"
)

BASELINE_SOURCE = 'from __future__ import annotations\n\nimport asyncio\nimport json\nfrom dataclasses import dataclass\nfrom datetime import UTC, datetime\nfrom enum import IntEnum\nfrom pathlib import Path\nfrom types import SimpleNamespace\nfrom typing import Any\n\nfrom pydantic import BaseModel, ConfigDict, Field\n\nfrom services.agent_runtime.app.investigation.coordinator import (\n    EvidenceDrivenInvestigationCoordinator,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    EvidenceItem,\n    IncidentHypothesis,\n    InvestigationConclusion,\n    InvestigationDecision,\n    InvestigationLimits,\n    InvestigationProbe,\n    InvestigationStatus,\n    InvestigationStopReason,\n)\nfrom services.agent_runtime.app.investigation.reasoner import (\n    BaseInvestigationReasoner,\n)\n\n\nclass CapabilityLevel(IntEnum):\n    """\n    Evidence level for one SRE Agent capability.\n\n    L0: absent from the current Agent path\n    L1: structural component exists\n    L2: contract/schema/guardrail is tested\n    L3: closed-loop behavior is tested\n    L4: real model or real Lab integration is validated\n    L5: real production incidents are validated\n    """\n\n    L0 = 0\n    L1 = 1\n    L2 = 2\n    L3 = 3\n    L4 = 4\n    L5 = 5\n\n\nLEVEL_SCORE = {\n    CapabilityLevel.L0: 0,\n    CapabilityLevel.L1: 25,\n    CapabilityLevel.L2: 50,\n    CapabilityLevel.L3: 75,\n    CapabilityLevel.L4: 90,\n    CapabilityLevel.L5: 100,\n}\n\n\nclass CapabilityAssessment(BaseModel):\n    model_config = ConfigDict(\n        frozen=True,\n        extra="forbid",\n    )\n\n    key: str\n    name: str\n    category: str\n    level: CapabilityLevel\n    score: int = Field(\n        ge=0,\n        le=100,\n    )\n    status: str\n    evidence: list[str] = Field(\n        default_factory=list,\n    )\n    gap: str | None = None\n    next_step: str | None = None\n    weight: float = Field(\n        default=1.0,\n        gt=0,\n    )\n\n\nclass CapabilityExamResult(BaseModel):\n    model_config = ConfigDict(\n        frozen=True,\n        extra="forbid",\n    )\n\n    key: str\n    name: str\n    passed: bool\n    detail: str\n\n\nclass CapabilityCategoryScore(BaseModel):\n    model_config = ConfigDict(\n        frozen=True,\n        extra="forbid",\n    )\n\n    category: str\n    score: float = Field(\n        ge=0.0,\n        le=100.0,\n    )\n    capability_count: int = Field(\n        ge=1,\n    )\n\n\nclass SREAgentCapabilityReport(BaseModel):\n    model_config = ConfigDict(\n        frozen=True,\n        extra="forbid",\n    )\n\n    schema_version: str = "v1"\n    generated_at: datetime\n    overall_score: float = Field(\n        ge=0.0,\n        le=100.0,\n    )\n    overall_level: str\n    production_validated: bool\n    assessments: list[\n        CapabilityAssessment\n    ]\n    behavioral_exams: list[\n        CapabilityExamResult\n    ]\n    categories: list[\n        CapabilityCategoryScore\n    ]\n    top_gaps: list[str]\n    recommended_order: list[str]\n\n\n@dataclass(\n    frozen=True,\n    slots=True,\n)\nclass RepositorySignals:\n    root: Path\n    source_text: dict[str, str]\n    test_text: str\n    real_bailian_decision_validated: bool\n    real_bailian_full_rca_validated: bool\n    production_incident_validated: bool\n\n\ndef _read_if_exists(\n    path: Path,\n) -> str:\n    if not path.exists():\n        return ""\n\n    try:\n        return path.read_text(\n            encoding="utf-8-sig",\n            errors="replace",\n        )\n\n    except OSError:\n        return ""\n\n\ndef _all_tests_text(\n    root: Path,\n) -> str:\n    tests_root = (\n        root\n        / "services"\n        / "agent_runtime"\n        / "tests"\n    )\n\n    chunks = []\n\n    if not tests_root.exists():\n        return ""\n\n    for path in tests_root.glob(\n        "test_*.py"\n    ):\n        chunks.append(\n            _read_if_exists(\n                path\n            )\n        )\n\n    return "\\n".join(\n        chunks\n    )\n\n\ndef collect_repository_signals(\n    root: Path,\n) -> RepositorySignals:\n    files = {\n        "investigation_models": (\n            root\n            / "services"\n            / "agent_runtime"\n            / "app"\n            / "investigation"\n            / "models.py"\n        ),\n        "investigation_coordinator": (\n            root\n            / "services"\n            / "agent_runtime"\n            / "app"\n            / "investigation"\n            / "coordinator.py"\n        ),\n        "investigation_reasoner": (\n            root\n            / "services"\n            / "agent_runtime"\n            / "app"\n            / "investigation"\n            / "reasoner.py"\n        ),\n        "investigation_probes": (\n            root\n            / "services"\n            / "agent_runtime"\n            / "app"\n            / "investigation"\n            / "probes.py"\n        ),\n        "runtime": (\n            root\n            / "services"\n            / "agent_runtime"\n            / "app"\n            / "runtime"\n            / "runtime.py"\n        ),\n        "registry_factory": (\n            root\n            / "services"\n            / "agent_runtime"\n            / "app"\n            / "registry"\n            / "factory.py"\n        ),\n        "skill_factory": (\n            root\n            / "services"\n            / "agent_runtime"\n            / "app"\n            / "skills"\n            / "factory.py"\n        ),\n        "tool_factory": (\n            root\n            / "services"\n            / "agent_runtime"\n            / "app"\n            / "tools"\n            / "factory.py"\n        ),\n        "historical_runner": (\n            root\n            / "services"\n            / "agent_runtime"\n            / "app"\n            / "evaluation"\n            / "real_incident"\n            / "investigation_runner.py"\n        ),\n        "historical_replay": (\n            root\n            / "services"\n            / "agent_runtime"\n            / "app"\n            / "evaluation"\n            / "real_incident"\n            / "historical_replay.py"\n        ),\n        "real_incident_models": (\n            root\n            / "services"\n            / "agent_runtime"\n            / "app"\n            / "evaluation"\n            / "real_incident"\n            / "models.py"\n        ),\n        "recorder": (\n            root\n            / "services"\n            / "agent_runtime"\n            / "app"\n            / "incident_evidence"\n            / "recorder.py"\n        ),\n        "action_runtime": (\n            root\n            / "services"\n            / "agent_runtime"\n            / "app"\n            / "runtime"\n            / "action_runtime.py"\n        ),\n        "verification_runtime": (\n            root\n            / "services"\n            / "agent_runtime"\n            / "app"\n            / "runtime"\n            / "verification_runtime.py"\n        ),\n    }\n\n    source_text = {\n        key: _read_if_exists(\n            path\n        )\n        for key, path\n        in files.items()\n    }\n\n    bailian_report = _read_if_exists(\n        root\n        / "bailian_connectivity_preflight_v1_after.txt"\n    )\n\n    real_bailian_decision_validated = (\n        "LIVE_REQUEST=PASSED"\n        in bailian_report\n        and "CONTRACT=PASSED"\n        in bailian_report\n    )\n\n    # v1 intentionally does not call a real model. If a later full\n    # historical RCA report exists, the baseline can automatically promote\n    # the RCA capability.\n    historical_live_reports = [\n        _read_if_exists(\n            root\n            / "historical_llm_investigation_result.json"\n        ),\n        _read_if_exists(\n            root\n            / "incident_001_agent_result.json"\n        ),\n    ]\n\n    real_bailian_full_rca_validated = any(\n        (\n            \'"conclusion"\' in text\n            and \'"root_cause"\' in text\n        )\n        for text in historical_live_reports\n        if text\n    )\n\n    real_capture_dir = (\n        root\n        / "evaluation_data"\n        / "real_incidents"\n    )\n\n    production_incident_validated = bool(\n        real_capture_dir.exists()\n        and any(\n            real_capture_dir.glob(\n                "*.json"\n            )\n        )\n    )\n\n    return RepositorySignals(\n        root=root,\n        source_text=source_text,\n        test_text=_all_tests_text(\n            root\n        ),\n        real_bailian_decision_validated=(\n            real_bailian_decision_validated\n        ),\n        real_bailian_full_rca_validated=(\n            real_bailian_full_rca_validated\n        ),\n        production_incident_validated=(\n            production_incident_validated\n        ),\n    )\n\n\ndef _contains(\n    signals: RepositorySignals,\n    source_key: str,\n    *tokens: str,\n) -> bool:\n    text = signals.source_text.get(\n        source_key,\n        "",\n    )\n\n    return all(\n        token in text\n        for token in tokens\n    )\n\n\ndef _test_contains(\n    signals: RepositorySignals,\n    *tokens: str,\n) -> bool:\n    return all(\n        token in signals.test_text\n        for token in tokens\n    )\n\n\ndef _assessment(\n    *,\n    key: str,\n    name: str,\n    category: str,\n    level: CapabilityLevel,\n    evidence: list[str],\n    gap: str | None,\n    next_step: str | None,\n    weight: float = 1.0,\n) -> CapabilityAssessment:\n    status = {\n        CapabilityLevel.L0: "missing",\n        CapabilityLevel.L1: "structural",\n        CapabilityLevel.L2: "contract_verified",\n        CapabilityLevel.L3: "behavior_verified",\n        CapabilityLevel.L4: "real_model_or_lab_verified",\n        CapabilityLevel.L5: "production_verified",\n    }[\n        level\n    ]\n\n    return CapabilityAssessment(\n        key=key,\n        name=name,\n        category=category,\n        level=level,\n        score=LEVEL_SCORE[\n            level\n        ],\n        status=status,\n        evidence=evidence,\n        gap=gap,\n        next_step=next_step,\n        weight=weight,\n    )\n\n\ndef build_capability_assessments(\n    signals: RepositorySignals,\n) -> list[\n    CapabilityAssessment\n]:\n    assessments: list[\n        CapabilityAssessment\n    ] = []\n\n    models = signals.source_text[\n        "investigation_models"\n    ]\n\n    coordinator = signals.source_text[\n        "investigation_coordinator"\n    ]\n\n    reasoner = signals.source_text[\n        "investigation_reasoner"\n    ]\n\n    probes = signals.source_text[\n        "investigation_probes"\n    ]\n\n    runtime = signals.source_text[\n        "runtime"\n    ]\n\n    registry = signals.source_text[\n        "registry_factory"\n    ]\n\n    skill_factory = signals.source_text[\n        "skill_factory"\n    ]\n\n    # Intelligence / reasoning\n    event_level = (\n        CapabilityLevel.L3\n        if (\n            "InvestigationScope"\n            in models\n            and "_scope_from_context"\n            in coordinator\n            and _test_contains(\n                signals,\n                "PodOOMKilled",\n                "InvestigationScope",\n            )\n        )\n        else CapabilityLevel.L1\n    )\n\n    assessments.append(\n        _assessment(\n            key="event_understanding",\n            name="Event / Scope Understanding",\n            category="brain",\n            level=event_level,\n            evidence=[\n                "StandardEvent is converted into a trusted InvestigationScope.",\n                "Scope is derived from event/resource fields rather than model output.",\n            ],\n            gap=(\n                "Current scope is primarily one-resource oriented; topology/dependency context is not yet first-class."\n            ),\n            next_step=(\n                "Add service/dependency context only when a real investigation capability needs it."\n            ),\n            weight=1.1,\n        )\n    )\n\n    hypothesis_level = (\n        CapabilityLevel.L4\n        if (\n            signals.real_bailian_decision_validated\n            and "IncidentHypothesis"\n            in models\n        )\n        else (\n            CapabilityLevel.L3\n            if (\n                "IncidentHypothesis"\n                in models\n                and "hypotheses"\n                in reasoner\n            )\n            else CapabilityLevel.L1\n        )\n    )\n\n    assessments.append(\n        _assessment(\n            key="hypothesis_generation",\n            name="Hypothesis Generation",\n            category="brain",\n            level=hypothesis_level,\n            evidence=[\n                "InvestigationDecision requires one or more bounded hypotheses.",\n                (\n                    "A real Bailian/Qwen InvestigationDecision has been validated."\n                    if signals.real_bailian_decision_validated\n                    else "Only deterministic/local behavior is currently evidenced."\n                ),\n            ],\n            gap=(\n                "No benchmark yet measures hypothesis diversity, ranking quality, or false-hypothesis rate."\n            ),\n            next_step=(\n                "Add scenario exams that compare hypothesis sets against hidden labels."\n            ),\n            weight=1.4,\n        )\n    )\n\n    probe_level = (\n        CapabilityLevel.L4\n        if (\n            signals.real_bailian_decision_validated\n            and "next_probe"\n            in models\n        )\n        else CapabilityLevel.L3\n    )\n\n    assessments.append(\n        _assessment(\n            key="probe_selection",\n            name="Autonomous Probe Selection",\n            category="brain",\n            level=probe_level,\n            evidence=[\n                "Continuing decisions must select exactly one symbolic read-only InvestigationProbe.",\n                "The model cannot choose raw Kubernetes verbs, URLs, credentials or PromQL.",\n                (\n                    "Real Qwen selected a valid next probe."\n                    if signals.real_bailian_decision_validated\n                    else "Probe selection is currently behavior-tested locally."\n                ),\n            ],\n            gap=(\n                "The allowed Probe vocabulary is still narrow."\n            ),\n            next_step=(\n                "Expand Probe/Skill vocabulary based on measured capability gaps."\n            ),\n            weight=1.5,\n        )\n    )\n\n    iterative_level = (\n        CapabilityLevel.L3\n        if (\n            "while state.status"\n            in coordinator\n            and "iteration_count"\n            in coordinator\n            and "tool_call_count"\n            in coordinator\n        )\n        else CapabilityLevel.L1\n    )\n\n    assessments.append(\n        _assessment(\n            key="iterative_investigation",\n            name="Iterative Investigation Loop",\n            category="brain",\n            level=iterative_level,\n            evidence=[\n                "Coordinator loops Reason -> Probe -> Evidence -> Reason under hard limits.",\n                "Iterations, tool calls, timeout and attempted probes are bounded.",\n            ],\n            gap=(\n                "The loop is implemented, but quality under diverse incidents is not benchmarked."\n            ),\n            next_step=(\n                "Build multi-scenario exams covering direction changes, dead ends and partial evidence."\n            ),\n            weight=1.5,\n        )\n    )\n\n    evidence_reasoning_level = (\n        CapabilityLevel.L3\n        if (\n            "supporting_evidence_ids"\n            in models\n            and "conflicting_evidence_ids"\n            in models\n            and "_evidence_references_are_valid"\n            in coordinator\n        )\n        else CapabilityLevel.L1\n    )\n\n    assessments.append(\n        _assessment(\n            key="evidence_reasoning",\n            name="Evidence Support / Conflict Reasoning",\n            category="brain",\n            level=evidence_reasoning_level,\n            evidence=[\n                "Hypotheses carry supporting, conflicting and missing evidence.",\n                "Unknown evidence references fail closed.",\n                "Conclusions must reference collected trusted evidence.",\n            ],\n            gap=(\n                "No quantitative measure yet scores whether confidence moves correctly when evidence supports or contradicts a hypothesis."\n            ),\n            next_step=(\n                "Add confidence-update and contradiction scenario exams."\n            ),\n            weight=1.5,\n        )\n    )\n\n    replanning_level = (\n        CapabilityLevel.L3\n        if (\n            "attempted_probes"\n            in models\n            and "DUPLICATE_PROBE"\n            in models\n            and "probe in state.attempted_probes"\n            in coordinator\n        )\n        else CapabilityLevel.L1\n    )\n\n    assessments.append(\n        _assessment(\n            key="replanning",\n            name="Replanning / Direction Change",\n            category="brain",\n            level=replanning_level,\n            evidence=[\n                "Each reasoner decision receives the full bounded state and prior evidence.",\n                "Different probes can be selected across iterations; duplicate probes are blocked.",\n            ],\n            gap=(\n                "There is no dedicated benchmark proving the real model abandons a wrong hypothesis when conflicting evidence arrives."\n            ),\n            next_step=(\n                "Add adversarial scenario exams with misleading first evidence."\n            ),\n            weight=1.4,\n        )\n    )\n\n    stop_level = (\n        CapabilityLevel.L3\n        if (\n            "INSUFFICIENT_EVIDENCE"\n            in models\n            and "NO_SAFE_PROBE"\n            in models\n            and "SUFFICIENT_EVIDENCE"\n            in models\n        )\n        else CapabilityLevel.L1\n    )\n\n    assessments.append(\n        _assessment(\n            key="stop_and_abstain",\n            name="Stop / Abstain Decision",\n            category="brain",\n            level=stop_level,\n            evidence=[\n                "Reasoner may stop for sufficient evidence, insufficient evidence or no safe probe.",\n                "Internal timeout/tool-limit stop reasons cannot be claimed by the model.",\n            ],\n            gap=(\n                "Abstention quality and overconfidence rate are not calibrated against a benchmark set."\n            ),\n            next_step=(\n                "Measure false-positive RCA vs correct abstention on underdetermined incidents."\n            ),\n            weight=1.3,\n        )\n    )\n\n    rca_level = (\n        CapabilityLevel.L4\n        if signals.real_bailian_full_rca_validated\n        else (\n            CapabilityLevel.L3\n            if (\n                "InvestigationConclusion"\n                in models\n                and "root_cause"\n                in models\n            )\n            else CapabilityLevel.L1\n        )\n    )\n\n    assessments.append(\n        _assessment(\n            key="rca_conclusion",\n            name="Structured RCA Conclusion",\n            category="brain",\n            level=rca_level,\n            evidence=[\n                "Sufficient-evidence stops require a structured InvestigationConclusion.",\n                "Conclusion carries root cause, confidence, evidence IDs and remaining uncertainties.",\n                (\n                    "A real-model full RCA artifact is present."\n                    if signals.real_bailian_full_rca_validated\n                    else "Full real-model historical RCA is not yet evidenced."\n                ),\n            ],\n            gap=(\n                "No real/lab full incident has yet established RCA accuracy."\n            ),\n            next_step=(\n                "After capability baseline, run scored Lab scenarios or curated historical cases."\n            ),\n            weight=1.7,\n        )\n    )\n\n    calibration_level = (\n        CapabilityLevel.L2\n        if (\n            "confidence"\n            in models\n            and "ge=0.0"\n            in models\n            and "le=1.0"\n            in models\n        )\n        else CapabilityLevel.L0\n    )\n\n    assessments.append(\n        _assessment(\n            key="confidence_calibration",\n            name="Confidence Calibration",\n            category="brain",\n            level=calibration_level,\n            evidence=[\n                "Hypothesis and conclusion confidence are structurally bounded to [0,1].",\n            ],\n            gap=(\n                "Bounded confidence is not the same as calibrated confidence; no reliability curve/Brier-style evaluation exists."\n            ),\n            next_step=(\n                "Add confidence calibration metrics after scenario labels exist."\n            ),\n            weight=1.1,\n        )\n    )\n\n    # Evidence / tools\n    kubernetes_probe_present = (\n        "KUBERNETES_POD_STATE"\n        in models\n        and "kubernetes"\n        in probes\n    )\n\n    kubernetes_level = (\n        CapabilityLevel.L3\n        if (\n            kubernetes_probe_present\n            and _test_contains(\n                signals,\n                "KUBERNETES_POD_STATE",\n                "production_signal",\n            )\n        )\n        else (\n            CapabilityLevel.L1\n            if kubernetes_probe_present\n            else CapabilityLevel.L0\n        )\n    )\n\n    assessments.append(\n        _assessment(\n            key="kubernetes_investigation",\n            name="Kubernetes Investigation",\n            category="evidence",\n            level=kubernetes_level,\n            evidence=[\n                "Current autonomous probe vocabulary includes kubernetes_pod_state.",\n                "Read-only production-signal trust boundaries are tested.",\n            ],\n            gap=(\n                "Coverage is narrow: no Events, rollout history, controller state, node pressure, scheduling path or network state in the autonomous loop."\n            ),\n            next_step=(\n                "Do not expand blindly; add the next Kubernetes probe when scenario exams expose the need."\n            ),\n            weight=1.2,\n        )\n    )\n\n    metric_probe_count = sum(\n        token in models\n        for token in (\n            "PROMETHEUS_MEMORY_WORKING_SET",\n            "PROMETHEUS_MEMORY_LIMIT",\n            "PROMETHEUS_RESTART_COUNT",\n        )\n    )\n\n    metrics_level = (\n        CapabilityLevel.L3\n        if metric_probe_count == 3\n        else (\n            CapabilityLevel.L1\n            if metric_probe_count\n            else CapabilityLevel.L0\n        )\n    )\n\n    assessments.append(\n        _assessment(\n            key="metrics_investigation",\n            name="Metrics Investigation",\n            category="evidence",\n            level=metrics_level,\n            evidence=[\n                f"{metric_probe_count}/3 current Prometheus memory/restart probes are present.",\n                "The reasoner selects symbolic probes; query templates remain outside model control.",\n            ],\n            gap=(\n                "No CPU, latency, error-rate, saturation, range-query, correlation or arbitrary metric discovery capability."\n            ),\n            next_step=(\n                "Build a bounded Metrics Skill/Probe expansion driven by scenario gaps."\n            ),\n            weight=1.2,\n        )\n    )\n\n    logs_present = (\n        "LOG"\n        in models.upper()\n        and "InvestigationProbe"\n        in models\n    )\n\n    assessments.append(\n        _assessment(\n            key="logs_investigation",\n            name="Logs Investigation",\n            category="evidence",\n            level=(\n                CapabilityLevel.L1\n                if logs_present\n                else CapabilityLevel.L0\n            ),\n            evidence=(\n                [\n                    "A log-related Investigation capability token was detected."\n                ]\n                if logs_present\n                else [\n                    "No log probe is present in the current InvestigationProbe contract."\n                ]\n            ),\n            gap=(\n                "The autonomous Investigation loop cannot currently request bounded application/container logs."\n            ),\n            next_step=(\n                "High priority after baseline: design a bounded Log Skill with time window, resource scope and secret redaction."\n            ),\n            weight=1.5,\n        )\n    )\n\n    change_agent_present = (\n        "ChangeAgent"\n        in registry\n        or "change"\n        in registry.lower()\n    )\n\n    change_probe_present = (\n        "CHANGE"\n        in models.upper()\n        and "InvestigationProbe"\n        in models\n    )\n\n    change_level = (\n        CapabilityLevel.L2\n        if change_probe_present\n        else (\n            CapabilityLevel.L1\n            if change_agent_present\n            else CapabilityLevel.L0\n        )\n    )\n\n    assessments.append(\n        _assessment(\n            key="change_investigation",\n            name="Change / Deployment Correlation",\n            category="evidence",\n            level=change_level,\n            evidence=[\n                (\n                    "A Change-related structural component exists."\n                    if change_agent_present\n                    else "No Change component was detected in the current registry source."\n                ),\n                (\n                    "Change is available to the autonomous Investigation loop."\n                    if change_probe_present\n                    else "Change is not a current InvestigationProbe."\n                ),\n            ],\n            gap=(\n                "Even if a ChangeAgent exists in Pipeline, autonomous Investigation cannot yet ask for deployment/config/image change evidence."\n            ),\n            next_step=(\n                "Add a bounded Change Evidence Skill after Logs or when baseline scenarios show change-correlation failures."\n            ),\n            weight=1.4,\n        )\n    )\n\n    dependency_present = any(\n        token in (\n            models\n            + reasoner\n            + probes\n        ).lower()\n        for token in (\n            "dependency_graph",\n            "service_topology",\n            "upstream",\n            "downstream",\n        )\n    )\n\n    assessments.append(\n        _assessment(\n            key="dependency_reasoning",\n            name="Dependency / Topology Reasoning",\n            category="knowledge",\n            level=(\n                CapabilityLevel.L1\n                if dependency_present\n                else CapabilityLevel.L0\n            ),\n            evidence=[\n                (\n                    "A topology/dependency token exists in the Investigation path."\n                    if dependency_present\n                    else "No first-class service topology/dependency contract is present in the Investigation path."\n                ),\n            ],\n            gap=(\n                "The Agent cannot yet reason over upstream/downstream service topology as trusted evidence."\n            ),\n            next_step=(\n                "Introduce topology/CMDB evidence only after a scenario requires cross-service reasoning."\n            ),\n            weight=1.1,\n        )\n    )\n\n    temporal_level = (\n        CapabilityLevel.L2\n        if (\n            "event_occurred_at"\n            in models\n            and (\n                signals.root\n                / "services"\n                / "agent_runtime"\n                / "app"\n                / "investigation"\n                / "evidence_time.py"\n            ).exists()\n        )\n        else CapabilityLevel.L1\n    )\n\n    assessments.append(\n        _assessment(\n            key="temporal_reasoning",\n            name="Causal / Temporal Evidence Handling",\n            category="knowledge",\n            level=temporal_level,\n            evidence=[\n                "Incident-time-aware evidence policy exists for Prometheus probes.",\n                "Historical Replay enforces causal time cutoffs.",\n            ],\n            gap=(\n                "This is causal evidence timing, not full timeline reasoning over deploys, logs and human/system events."\n            ),\n            next_step=(\n                "Add multi-source timeline reasoning when Change/Logs evidence enters the loop."\n            ),\n            weight=1.0,\n        )\n    )\n\n    rag_present = any(\n        (\n            signals.root\n            / "services"\n            / "agent_runtime"\n            / "app"\n            / name\n        ).exists()\n        for name in (\n            "rag",\n            "knowledge",\n            "retrieval",\n        )\n    )\n\n    assessments.append(\n        _assessment(\n            key="rag_knowledge",\n            name="RAG / SRE Knowledge Retrieval",\n            category="knowledge",\n            level=(\n                CapabilityLevel.L1\n                if rag_present\n                else CapabilityLevel.L0\n            ),\n            evidence=[\n                (\n                    "A knowledge/retrieval package exists."\n                    if rag_present\n                    else "No dedicated RAG/knowledge/retrieval package is present in the current Agent path."\n                ),\n            ],\n            gap=(\n                "Runbooks, architecture docs, historical incidents and service knowledge are not yet retrievable by Investigation."\n            ),\n            next_step=(\n                "Build industrial RAG after Logs/Change baseline gaps are quantified."\n            ),\n            weight=1.4,\n        )\n    )\n\n    memory_level = (\n        CapabilityLevel.L1\n        if "MemoryStore"\n        in runtime\n        else CapabilityLevel.L0\n    )\n\n    assessments.append(\n        _assessment(\n            key="long_term_memory",\n            name="Operational Memory / Experience",\n            category="knowledge",\n            level=memory_level,\n            evidence=[\n                (\n                    "Runtime owns a MemoryStore."\n                    if memory_level\n                    else "No Runtime MemoryStore was detected."\n                ),\n                "InvestigationState currently carries incident-local state independently.",\n            ],\n            gap=(\n                "No evidence that Investigation retrieves and applies long-term incident experience."\n            ),\n            next_step=(\n                "Do not expand memory generically; connect historical incident knowledge through explicit retrieval semantics."\n            ),\n            weight=0.9,\n        )\n    )\n\n    skill_level = (\n        CapabilityLevel.L1\n        if (\n            "create_skill_registry"\n            in runtime\n            or "SkillRegistry"\n            in skill_factory\n        )\n        else CapabilityLevel.L0\n    )\n\n    assessments.append(\n        _assessment(\n            key="skill_selection",\n            name="Skill Registry / Intelligent Skill Selection",\n            category="knowledge",\n            level=skill_level,\n            evidence=[\n                "A Skill registry is wired into Runtime."\n                if skill_level\n                else "No Skill registry wiring was detected.",\n                "The current autonomous Investigation brain selects InvestigationProbe values, not arbitrary Skills.",\n            ],\n            gap=(\n                "Skill infrastructure exists, but intelligent Skill discovery/selection is not yet the Investigation control plane."\n            ),\n            next_step=(\n                "Evolve Probe selection toward capability/Skill selection only after the bounded contracts are preserved."\n            ),\n            weight=1.2,\n        )\n    )\n\n    # Remediation / governance\n    action_present = (\n        "ActionRuntime"\n        in runtime\n        and bool(\n            signals.source_text[\n                "action_runtime"\n            ]\n        )\n    )\n\n    assessments.append(\n        _assessment(\n            key="remediation_planning_execution",\n            name="Remediation / Action Runtime",\n            category="remediation",\n            level=(\n                CapabilityLevel.L3\n                if (\n                    action_present\n                    and _test_contains(\n                        signals,\n                        "ActionRuntime",\n                        "approval",\n                    )\n                )\n                else (\n                    CapabilityLevel.L1\n                    if action_present\n                    else CapabilityLevel.L0\n                )\n            ),\n            evidence=[\n                "Runtime owns ActionRuntime and approval-linked action execution."\n                if action_present\n                else "ActionRuntime is not wired.",\n            ],\n            gap=(\n                "Current capability baseline does not claim autonomous production remediation; production execution remains gated."\n            ),\n            next_step=(\n                "Keep execution gated; first improve diagnosis quality before expanding autonomous remediation."\n            ),\n            weight=0.9,\n        )\n    )\n\n    approval_present = (\n        "ApprovalService"\n        in runtime\n    )\n\n    assessments.append(\n        _assessment(\n            key="approval_governance",\n            name="Approval / Human Governance",\n            category="remediation",\n            level=(\n                CapabilityLevel.L3\n                if (\n                    approval_present\n                    and _test_contains(\n                        signals,\n                        "approved",\n                        "rejected",\n                    )\n                )\n                else (\n                    CapabilityLevel.L1\n                    if approval_present\n                    else CapabilityLevel.L0\n                )\n            ),\n            evidence=[\n                "Explicit ApprovalService is shared by Runtime."\n                if approval_present\n                else "Approval service is absent.",\n            ],\n            gap=None,\n            next_step=(\n                "Preserve human governance while intelligence quality is being raised."\n            ),\n            weight=0.7,\n        )\n    )\n\n    verification_present = (\n        "VerificationRuntime"\n        in runtime\n        and bool(\n            signals.source_text[\n                "verification_runtime"\n            ]\n        )\n    )\n\n    assessments.append(\n        _assessment(\n            key="post_action_verification",\n            name="Post-action Verification",\n            category="remediation",\n            level=(\n                CapabilityLevel.L3\n                if (\n                    verification_present\n                    and _test_contains(\n                        signals,\n                        "Verification",\n                        "passed",\n                    )\n                )\n                else (\n                    CapabilityLevel.L1\n                    if verification_present\n                    else CapabilityLevel.L0\n                )\n            ),\n            evidence=[\n                "Verification Runtime/Coordinator infrastructure is wired."\n                if verification_present\n                else "Verification runtime is absent.",\n            ],\n            gap=(\n                "Verification quality is not currently part of the autonomous Investigation score."\n            ),\n            next_step=(\n                "Later connect diagnosis/remediation quality metrics to verification outcomes."\n            ),\n            weight=0.8,\n        )\n    )\n\n    rollback_present = (\n        "rollback"\n        in (\n            runtime\n            + signals.source_text[\n                "action_runtime"\n            ]\n        ).lower()\n    )\n\n    assessments.append(\n        _assessment(\n            key="rollback_recovery",\n            name="Rollback / Recovery",\n            category="remediation",\n            level=(\n                CapabilityLevel.L2\n                if rollback_present\n                else CapabilityLevel.L1\n            ),\n            evidence=[\n                (\n                    "Rollback/recovery semantics are present in action/runtime code."\n                    if rollback_present\n                    else "Recovery infrastructure exists indirectly, but a scored rollback capability is not evidenced."\n                ),\n            ],\n            gap=(\n                "No capability benchmark currently proves safe end-to-end rollback after a bad remediation."\n            ),\n            next_step=(\n                "Keep this behind governance; add a dedicated rollback exam after diagnosis baseline improves."\n            ),\n            weight=0.7,\n        )\n    )\n\n    # Evaluation/data\n    historical_present = (\n        bool(\n            signals.source_text[\n                "historical_runner"\n            ]\n        )\n        and bool(\n            signals.source_text[\n                "historical_replay"\n            ]\n        )\n    )\n\n    assessments.append(\n        _assessment(\n            key="historical_replay",\n            name="Historical Incident Replay",\n            category="evaluation",\n            level=(\n                CapabilityLevel.L3\n                if (\n                    historical_present\n                    and _test_contains(\n                        signals,\n                        "HistoricalIncidentInvestigationRunner",\n                        "replay",\n                    )\n                )\n                else (\n                    CapabilityLevel.L1\n                    if historical_present\n                    else CapabilityLevel.L0\n                )\n            ),\n            evidence=[\n                "Ground-truth-free historical investigation runner and causal replay exist."\n                if historical_present\n                else "Historical replay is absent.",\n            ],\n            gap=(\n                "No real production incident corpus exists yet."\n            ),\n            next_step=(\n                "Use synthetic/lab cases for capability scoring until real incidents naturally accumulate."\n            ),\n            weight=1.1,\n        )\n    )\n\n    recorder_present = bool(\n        signals.source_text[\n            "recorder"\n        ]\n    )\n\n    assessments.append(\n        _assessment(\n            key="incident_evidence_recorder",\n            name="Incident Evidence Recorder",\n            category="evaluation",\n            level=(\n                CapabilityLevel.L3\n                if (\n                    recorder_present\n                    and _test_contains(\n                        signals,\n                        "ProductionIncidentEvidenceRecorder",\n                        "production_signal",\n                    )\n                )\n                else (\n                    CapabilityLevel.L1\n                    if recorder_present\n                    else CapabilityLevel.L0\n                )\n            ),\n            evidence=[\n                "Recorder preserves replay-safe trusted evidence and is wired best-effort."\n                if recorder_present\n                else "Incident Recorder is absent.",\n            ],\n            gap=(\n                "No production environment is available yet; live capture is intentionally disabled."\n            ),\n            next_step=(\n                "Keep disabled in development; use Lab later for integration validation."\n            ),\n            weight=0.9,\n        )\n    )\n\n    production_level = (\n        CapabilityLevel.L5\n        if signals.production_incident_validated\n        else CapabilityLevel.L1\n    )\n\n    assessments.append(\n        _assessment(\n            key="production_incident_validation",\n            name="Production Incident Validation",\n            category="evaluation",\n            level=production_level,\n            evidence=[\n                (\n                    "A real production incident dataset is present."\n                    if signals.production_incident_validated\n                    else "No real production incident dataset is present; this is expected in the current development phase."\n                )\n            ],\n            gap=(\n                None\n                if signals.production_incident_validated\n                else "Production effectiveness is unproven."\n            ),\n            next_step=(\n                None\n                if signals.production_incident_validated\n                else "Do not fabricate production evidence; wait for real incidents after deployment."\n            ),\n            weight=0.6,\n        )\n    )\n\n    return assessments\n\n\nNOW = datetime(\n    2026,\n    8,\n    10,\n    8,\n    30,\n    tzinfo=UTC,\n)\n\n\nclass _ScriptedReasoner(\n    BaseInvestigationReasoner\n):\n    def __init__(\n        self,\n        decisions,\n    ) -> None:\n        self.decisions = list(\n            decisions\n        )\n        self.states = []\n\n    async def decide(\n        self,\n        scope,\n        state,\n    ) -> InvestigationDecision:\n        self.states.append(\n            state.model_copy(\n                deep=True\n            )\n        )\n\n        if not self.decisions:\n            raise RuntimeError(\n                "No scripted decision remains"\n            )\n\n        decision = self.decisions.pop(\n            0\n        )\n\n        if isinstance(\n            decision,\n            Exception,\n        ):\n            raise decision\n\n        return decision\n\n\nclass _ScriptedProbeExecutor:\n    def __init__(\n        self,\n        evidence_by_probe: dict[\n            InvestigationProbe,\n            EvidenceItem | Exception,\n        ],\n    ) -> None:\n        self.evidence_by_probe = (\n            evidence_by_probe\n        )\n        self.calls = []\n\n    async def collect(\n        self,\n        context,\n        scope,\n        probe,\n    ) -> EvidenceItem:\n        self.calls.append(\n            probe\n        )\n\n        value = self.evidence_by_probe[\n            probe\n        ]\n\n        if isinstance(\n            value,\n            Exception,\n        ):\n            raise value\n\n        return value.model_copy(\n            deep=True\n        )\n\n\ndef _context():\n    return SimpleNamespace(\n        event=SimpleNamespace(\n            signal=SimpleNamespace(\n                name="PodOOMKilled",\n                message=(\n                    "Container restarted"\n                ),\n            ),\n            resources=[\n                SimpleNamespace(\n                    name="payment-api",\n                    namespace="payment",\n                    cluster="dev-lab",\n                )\n            ],\n        ),\n        metadata={},\n        variables={},\n    )\n\n\ndef _hypothesis(\n    confidence: float,\n    *,\n    supporting: list[str] | None = None,\n    conflicting: list[str] | None = None,\n    missing: list[str] | None = None,\n) -> IncidentHypothesis:\n    return IncidentHypothesis(\n        hypothesis_id="memory-pressure",\n        cause=(\n            "Container memory pressure may have caused termination"\n        ),\n        confidence=confidence,\n        supporting_evidence_ids=(\n            supporting\n            or []\n        ),\n        conflicting_evidence_ids=(\n            conflicting\n            or []\n        ),\n        missing_evidence=(\n            missing\n            or []\n        ),\n    )\n\n\ndef _trusted_evidence(\n    *,\n    evidence_id: str,\n    probe: InvestigationProbe,\n    facts: dict[str, Any],\n) -> EvidenceItem:\n    return EvidenceItem(\n        evidence_id=evidence_id,\n        probe=probe,\n        source=(\n            "kubernetes"\n            if probe\n            == InvestigationProbe.KUBERNETES_POD_STATE\n            else "prometheus"\n        ),\n        success=True,\n        trusted=True,\n        production_signal=True,\n        reliability=1.0,\n        observed_at=NOW,\n        facts=facts,\n    )\n\n\nasync def _exam_multi_step_replan() -> CapabilityExamResult:\n    pod = _trusted_evidence(\n        evidence_id="e-pod",\n        probe=(\n            InvestigationProbe.KUBERNETES_POD_STATE\n        ),\n        facts={\n            "oom_killed": True,\n            "max_restart_count": 4,\n        },\n    )\n\n    working = _trusted_evidence(\n        evidence_id="e-working",\n        probe=(\n            InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET\n        ),\n        facts={\n            "value_sum": 500.0,\n        },\n    )\n\n    reasoner = _ScriptedReasoner(\n        [\n            InvestigationDecision(\n                hypotheses=[\n                    _hypothesis(\n                        0.35,\n                        missing=[\n                            "pod termination state"\n                        ],\n                    )\n                ],\n                rationale_summary=(\n                    "Inspect pod state"\n                ),\n                next_probe=(\n                    InvestigationProbe.KUBERNETES_POD_STATE\n                ),\n            ),\n            InvestigationDecision(\n                hypotheses=[\n                    _hypothesis(\n                        0.72,\n                        supporting=[\n                            "e-pod"\n                        ],\n                        missing=[\n                            "memory working set"\n                        ],\n                    )\n                ],\n                rationale_summary=(\n                    "OOM is supported; inspect memory usage"\n                ),\n                next_probe=(\n                    InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET\n                ),\n            ),\n            InvestigationDecision(\n                hypotheses=[\n                    _hypothesis(\n                        0.93,\n                        supporting=[\n                            "e-pod",\n                            "e-working",\n                        ],\n                    )\n                ],\n                rationale_summary=(\n                    "Trusted evidence is sufficient"\n                ),\n                stop=True,\n                stop_reason=(\n                    InvestigationStopReason.SUFFICIENT_EVIDENCE\n                ),\n                conclusion=(\n                    InvestigationConclusion(\n                        root_cause=(\n                            "Container memory pressure caused OOM termination"\n                        ),\n                        confidence=0.93,\n                        evidence_ids=[\n                            "e-pod",\n                            "e-working",\n                        ],\n                    )\n                ),\n            ),\n        ]\n    )\n\n    probes = _ScriptedProbeExecutor(\n        {\n            InvestigationProbe.KUBERNETES_POD_STATE: pod,\n            InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET: working,\n        }\n    )\n\n    coordinator = (\n        EvidenceDrivenInvestigationCoordinator(\n            reasoner=reasoner,\n            probe_executor=probes,\n            limits=InvestigationLimits(\n                max_iterations=5,\n                max_tool_calls=5,\n                timeout_seconds=10,\n            ),\n            utc_clock=lambda: NOW,\n        )\n    )\n\n    state = await coordinator.investigate(\n        _context()\n    )\n\n    passed = (\n        state.status\n        == InvestigationStatus.CONCLUDED\n        and state.stop_reason\n        == InvestigationStopReason.SUFFICIENT_EVIDENCE\n        and state.iteration_count\n        == 3\n        and state.tool_call_count\n        == 2\n        and state.attempted_probes\n        == [\n            InvestigationProbe.KUBERNETES_POD_STATE,\n            InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,\n        ]\n        and state.conclusion\n        is not None\n        and state.conclusion.confidence\n        == 0.93\n    )\n\n    return CapabilityExamResult(\n        key="multi_step_replan",\n        name="Multi-step Replan Exam",\n        passed=passed,\n        detail=(\n            "Reason -> Pod probe -> evidence -> Metrics probe -> evidence -> trusted RCA."\n        ),\n    )\n\n\nasync def _exam_safe_abstention() -> CapabilityExamResult:\n    reasoner = _ScriptedReasoner(\n        [\n            InvestigationDecision(\n                hypotheses=[\n                    _hypothesis(\n                        0.3,\n                        missing=[\n                            "pod state"\n                        ],\n                    )\n                ],\n                rationale_summary=(\n                    "Try one safe probe"\n                ),\n                next_probe=(\n                    InvestigationProbe.KUBERNETES_POD_STATE\n                ),\n            ),\n            InvestigationDecision(\n                hypotheses=[\n                    _hypothesis(\n                        0.2,\n                        missing=[\n                            "trusted evidence"\n                        ],\n                    )\n                ],\n                rationale_summary=(\n                    "Evidence is unavailable; abstain"\n                ),\n                stop=True,\n                stop_reason=(\n                    InvestigationStopReason.INSUFFICIENT_EVIDENCE\n                ),\n            ),\n        ]\n    )\n\n    probes = _ScriptedProbeExecutor(\n        {\n            InvestigationProbe.KUBERNETES_POD_STATE: (\n                RuntimeError(\n                    "sensitive backend detail"\n                )\n            )\n        }\n    )\n\n    coordinator = (\n        EvidenceDrivenInvestigationCoordinator(\n            reasoner=reasoner,\n            probe_executor=probes,\n            utc_clock=lambda: NOW,\n        )\n    )\n\n    state = await coordinator.investigate(\n        _context()\n    )\n\n    serialized = json.dumps(\n        state.model_dump(\n            mode="json"\n        ),\n        sort_keys=True,\n    )\n\n    passed = (\n        state.status\n        == InvestigationStatus.CONCLUDED\n        and state.stop_reason\n        == InvestigationStopReason.INSUFFICIENT_EVIDENCE\n        and state.conclusion\n        is None\n        and len(\n            state.evidence\n        )\n        == 1\n        and state.evidence[\n            0\n        ].trusted\n        is False\n        and "sensitive backend detail"\n        not in serialized\n    )\n\n    return CapabilityExamResult(\n        key="safe_abstention",\n        name="Safe Abstention Exam",\n        passed=passed,\n        detail=(\n            "Failed/untrusted evidence must not produce a confident RCA or leak backend error text."\n        ),\n    )\n\n\nasync def _exam_invalid_evidence_reference() -> CapabilityExamResult:\n    reasoner = _ScriptedReasoner(\n        [\n            InvestigationDecision(\n                hypotheses=[\n                    _hypothesis(\n                        0.99,\n                        supporting=[\n                            "never-collected"\n                        ],\n                    )\n                ],\n                rationale_summary=(\n                    "Attempt unsupported conclusion"\n                ),\n                stop=True,\n                stop_reason=(\n                    InvestigationStopReason.SUFFICIENT_EVIDENCE\n                ),\n                conclusion=(\n                    InvestigationConclusion(\n                        root_cause=(\n                            "Unsupported conclusion"\n                        ),\n                        confidence=0.99,\n                        evidence_ids=[\n                            "never-collected"\n                        ],\n                    )\n                ),\n            )\n        ]\n    )\n\n    probes = _ScriptedProbeExecutor(\n        {}\n    )\n\n    coordinator = (\n        EvidenceDrivenInvestigationCoordinator(\n            reasoner=reasoner,\n            probe_executor=probes,\n            utc_clock=lambda: NOW,\n        )\n    )\n\n    state = await coordinator.investigate(\n        _context()\n    )\n\n    passed = (\n        state.status\n        == InvestigationStatus.FAILED\n        and state.stop_reason\n        == InvestigationStopReason.REASONER_ERROR\n        and state.failure_code\n        == "InvalidEvidenceReference"\n        and probes.calls\n        == []\n    )\n\n    return CapabilityExamResult(\n        key="invalid_evidence_reference",\n        name="Evidence Hallucination Guard Exam",\n        passed=passed,\n        detail=(\n            "A model cannot cite evidence that the investigation never collected."\n        ),\n    )\n\n\nasync def _exam_duplicate_probe_guard() -> CapabilityExamResult:\n    pod = _trusted_evidence(\n        evidence_id="e-pod",\n        probe=(\n            InvestigationProbe.KUBERNETES_POD_STATE\n        ),\n        facts={\n            "oom_killed": False,\n        },\n    )\n\n    reasoner = _ScriptedReasoner(\n        [\n            InvestigationDecision(\n                hypotheses=[\n                    _hypothesis(\n                        0.25,\n                        missing=[\n                            "pod state"\n                        ],\n                    )\n                ],\n                rationale_summary=(\n                    "Inspect pod state"\n                ),\n                next_probe=(\n                    InvestigationProbe.KUBERNETES_POD_STATE\n                ),\n            ),\n            InvestigationDecision(\n                hypotheses=[\n                    _hypothesis(\n                        0.25,\n                        missing=[\n                            "more pod state"\n                        ],\n                    )\n                ],\n                rationale_summary=(\n                    "Incorrectly request duplicate probe"\n                ),\n                next_probe=(\n                    InvestigationProbe.KUBERNETES_POD_STATE\n                ),\n            ),\n        ]\n    )\n\n    probes = _ScriptedProbeExecutor(\n        {\n            InvestigationProbe.KUBERNETES_POD_STATE: pod,\n        }\n    )\n\n    coordinator = (\n        EvidenceDrivenInvestigationCoordinator(\n            reasoner=reasoner,\n            probe_executor=probes,\n            utc_clock=lambda: NOW,\n        )\n    )\n\n    state = await coordinator.investigate(\n        _context()\n    )\n\n    passed = (\n        state.status\n        == InvestigationStatus.EXHAUSTED\n        and state.stop_reason\n        == InvestigationStopReason.DUPLICATE_PROBE\n        and len(\n            probes.calls\n        )\n        == 1\n    )\n\n    return CapabilityExamResult(\n        key="duplicate_probe_guard",\n        name="Duplicate Probe Guard Exam",\n        passed=passed,\n        detail=(\n            "The coordinator blocks repeated evidence collection loops."\n        ),\n    )\n\n\nasync def run_behavioral_exams() -> list[\n    CapabilityExamResult\n]:\n    exams = [\n        _exam_multi_step_replan,\n        _exam_safe_abstention,\n        _exam_invalid_evidence_reference,\n        _exam_duplicate_probe_guard,\n    ]\n\n    results = []\n\n    for exam in exams:\n        try:\n            result = await exam()\n\n        except Exception as exc:\n            result = CapabilityExamResult(\n                key=exam.__name__,\n                name=exam.__name__,\n                passed=False,\n                detail=(\n                    f"Exam raised {type(exc).__name__}"\n                ),\n            )\n\n        results.append(\n            result\n        )\n\n    return results\n\n\ndef _category_scores(\n    assessments: list[\n        CapabilityAssessment\n    ],\n) -> list[\n    CapabilityCategoryScore\n]:\n    categories = sorted(\n        {\n            item.category\n            for item in assessments\n        }\n    )\n\n    results = []\n\n    for category in categories:\n        items = [\n            item\n            for item in assessments\n            if item.category\n            == category\n        ]\n\n        total_weight = sum(\n            item.weight\n            for item in items\n        )\n\n        score = sum(\n            item.score\n            * item.weight\n            for item in items\n        ) / total_weight\n\n        results.append(\n            CapabilityCategoryScore(\n                category=category,\n                score=round(\n                    score,\n                    1,\n                ),\n                capability_count=len(\n                    items\n                ),\n            )\n        )\n\n    return results\n\n\ndef _overall_level(\n    score: float,\n) -> str:\n    if score < 20:\n        return "L0.x - framework infancy"\n\n    if score < 40:\n        return "L1.x - componentized assistant"\n\n    if score < 60:\n        return "L2.x - bounded SRE agent"\n\n    if score < 80:\n        return "L3.x - behaviorally capable SRE agent"\n\n    if score < 95:\n        return "L4.x - lab/real-model validated SRE agent"\n\n    return "L5 - production-validated autonomous SRE"\n\n\ndef build_report(\n    root: Path,\n) -> SREAgentCapabilityReport:\n    signals = (\n        collect_repository_signals(\n            root\n        )\n    )\n\n    assessments = (\n        build_capability_assessments(\n            signals\n        )\n    )\n\n    exams = asyncio.run(\n        run_behavioral_exams()\n    )\n\n    total_weight = sum(\n        item.weight\n        for item in assessments\n    )\n\n    base_score = sum(\n        item.score\n        * item.weight\n        for item in assessments\n    ) / total_weight\n\n    exam_pass_rate = (\n        sum(\n            1\n            for exam in exams\n            if exam.passed\n        )\n        / len(\n            exams\n        )\n    )\n\n    # Behavioral exams affect only a modest portion of the score because\n    # they validate the control loop, not the breadth of SRE knowledge.\n    overall_score = (\n        base_score\n        * 0.9\n        + exam_pass_rate\n        * 100.0\n        * 0.1\n    )\n\n    categories = (\n        _category_scores(\n            assessments\n        )\n    )\n\n    gap_candidates = sorted(\n        (\n            item\n            for item in assessments\n            if item.gap\n            is not None\n        ),\n        key=lambda item: (\n            item.score,\n            -item.weight,\n            item.name,\n        ),\n    )\n\n    top_gaps = [\n        (\n            f"{item.name}: "\n            f"{item.gap}"\n        )\n        for item in gap_candidates[\n            :8\n        ]\n    ]\n\n    recommended_order = [\n        "1. Benchmark real Investigation Brain quality with labeled synthetic/lab scenarios.",\n        "2. Add Logs Investigation capability.",\n        "3. Add Change / Deployment evidence capability.",\n        "4. Add richer Metrics capability and contradiction/replanning exams.",\n        "5. Add RAG for runbooks, architecture and historical incidents.",\n        "6. Build Local SRE Lab only when tool realism is needed.",\n        "7. Preserve Approval/Verification gates; do not expand autonomous production writes yet.",\n    ]\n\n    return SREAgentCapabilityReport(\n        generated_at=datetime.now(\n            UTC\n        ),\n        overall_score=round(\n            overall_score,\n            1,\n        ),\n        overall_level=_overall_level(\n            overall_score\n        ),\n        production_validated=(\n            signals.production_incident_validated\n        ),\n        assessments=assessments,\n        behavioral_exams=exams,\n        categories=categories,\n        top_gaps=top_gaps,\n        recommended_order=(\n            recommended_order\n        ),\n    )\n\n\ndef render_text_report(\n    report: SREAgentCapabilityReport,\n) -> str:\n    lines = [\n        "=" * 88,\n        "SRE AGENT CAPABILITY BASELINE v1",\n        "=" * 88,\n        "",\n        f"GeneratedAt: {report.generated_at.isoformat()}",\n        f"OverallScore: {report.overall_score:.1f}/100",\n        f"OverallLevel: {report.overall_level}",\n        (\n            "ProductionValidated: "\n            + str(\n                report.production_validated\n            )\n        ),\n        "",\n        "Important:",\n        "- L3 means behavior-tested, not production-proven.",\n        "- L4 means real-model or Lab integration evidence exists.",\n        "- L5 is reserved for real production incident validation.",\n        "",\n        "CATEGORY SCORES",\n        "-" * 88,\n    ]\n\n    for category in report.categories:\n        lines.append(\n            (\n                f"{category.category:<14}"\n                f"{category.score:>6.1f}/100 "\n                f"({category.capability_count} capabilities)"\n            )\n        )\n\n    lines.extend(\n        [\n            "",\n            "CAPABILITY MATRIX",\n            "-" * 88,\n        ]\n    )\n\n    for item in report.assessments:\n        lines.append(\n            (\n                f"[{item.level.name}] "\n                f"{item.name:<38} "\n                f"{item.score:>3}/100 "\n                f"{item.status}"\n            )\n        )\n\n        for evidence in item.evidence:\n            lines.append(\n                f"    evidence: {evidence}"\n            )\n\n        if item.gap:\n            lines.append(\n                f"    gap:      {item.gap}"\n            )\n\n        if item.next_step:\n            lines.append(\n                f"    next:     {item.next_step}"\n            )\n\n    lines.extend(\n        [\n            "",\n            "BEHAVIORAL EXAMS",\n            "-" * 88,\n        ]\n    )\n\n    for exam in report.behavioral_exams:\n        lines.append(\n            (\n                f"[{\'PASS\' if exam.passed else \'FAIL\'}] "\n                f"{exam.name}"\n            )\n        )\n        lines.append(\n            f"    {exam.detail}"\n        )\n\n    lines.extend(\n        [\n            "",\n            "TOP GAPS",\n            "-" * 88,\n        ]\n    )\n\n    for gap in report.top_gaps:\n        lines.append(\n            f"- {gap}"\n        )\n\n    lines.extend(\n        [\n            "",\n            "RECOMMENDED DEVELOPMENT ORDER",\n            "-" * 88,\n            *report.recommended_order,\n            "",\n            "=" * 88,\n        ]\n    )\n\n    return "\\n".join(\n        lines\n    ) + "\\n"\n\n\n__all__ = [\n    "CapabilityAssessment",\n    "CapabilityCategoryScore",\n    "CapabilityExamResult",\n    "CapabilityLevel",\n    "SREAgentCapabilityReport",\n    "build_capability_assessments",\n    "build_report",\n    "collect_repository_signals",\n    "render_text_report",\n    "run_behavioral_exams",\n]\n'
INIT_SOURCE = 'from services.agent_runtime.app.evaluation.capability.baseline import (\n    CapabilityAssessment,\n    CapabilityCategoryScore,\n    CapabilityExamResult,\n    CapabilityLevel,\n    SREAgentCapabilityReport,\n    build_report,\n    render_text_report,\n    run_behavioral_exams,\n)\n\n\n__all__ = [\n    "CapabilityAssessment",\n    "CapabilityCategoryScore",\n    "CapabilityExamResult",\n    "CapabilityLevel",\n    "SREAgentCapabilityReport",\n    "build_report",\n    "render_text_report",\n    "run_behavioral_exams",\n]\n'
RUNNER_SOURCE = 'from __future__ import annotations\n\nimport json\nimport sys\nimport traceback\nfrom pathlib import Path\n\n\nTEXT_REPORT = (\n    "sre_agent_capability_baseline_v1_report.txt"\n)\n\nJSON_REPORT = (\n    "sre_agent_capability_baseline_v1_report.json"\n)\n\nERROR_REPORT = (\n    "sre_agent_capability_baseline_v1_error.txt"\n)\n\n\ndef find_repo_root(\n    start: Path,\n) -> Path:\n    for candidate in (\n        start,\n        *start.parents,\n    ):\n        if (\n            (candidate / "pyproject.toml").exists()\n            and (candidate / "services").exists()\n            and (candidate / "packages").exists()\n        ):\n            return candidate\n\n    raise RuntimeError(\n        "Repository root not found."\n    )\n\n\ndef install_import_paths(\n    root: Path,\n) -> None:\n    for candidate in reversed(\n        [\n            root,\n            root / "packages" / "common" / "src",\n        ]\n    ):\n        value = str(\n            candidate\n        )\n\n        if value not in sys.path:\n            sys.path.insert(\n                0,\n                value,\n            )\n\n\ndef main() -> int:\n    root = find_repo_root(\n        Path.cwd().resolve()\n    )\n\n    install_import_paths(\n        root\n    )\n\n    text_path = (\n        root\n        / TEXT_REPORT\n    )\n\n    json_path = (\n        root\n        / JSON_REPORT\n    )\n\n    error_path = (\n        root\n        / ERROR_REPORT\n    )\n\n    for path in (\n        text_path,\n        json_path,\n        error_path,\n    ):\n        try:\n            path.unlink()\n        except FileNotFoundError:\n            pass\n\n    try:\n        from services.agent_runtime.app.evaluation.capability import (\n            build_report,\n            render_text_report,\n        )\n\n        report = build_report(\n            root\n        )\n\n        text_path.write_text(\n            render_text_report(\n                report\n            ),\n            encoding="utf-8",\n            newline="\\n",\n        )\n\n        json_path.write_text(\n            json.dumps(\n                report.model_dump(\n                    mode="json"\n                ),\n                ensure_ascii=False,\n                indent=2,\n                sort_keys=True,\n            )\n            + "\\n",\n            encoding="utf-8",\n            newline="\\n",\n        )\n\n        passed_exams = sum(\n            1\n            for item in report.behavioral_exams\n            if item.passed\n        )\n\n        print("=" * 72)\n        print(\n            "SRE AGENT CAPABILITY BASELINE V1 COMPLETED"\n        )\n        print("=" * 72)\n        print("")\n        print(\n            f"Overall: {report.overall_score:.1f}/100"\n        )\n        print(\n            f"Level: {report.overall_level}"\n        )\n        print(\n            "Behavioral exams: "\n            f"{passed_exams}/{len(report.behavioral_exams)} passed"\n        )\n        print("")\n        print("Upload BOTH:")\n        print(text_path)\n        print(json_path)\n\n        return 0\n\n    except Exception as exc:\n        error_path.write_text(\n            (\n                "SRE Agent Capability Baseline v1 FAILED\\n\\n"\n                f"{type(exc).__name__}: {exc}\\n\\n"\n                + traceback.format_exc()\n            ),\n            encoding="utf-8",\n            newline="\\n",\n        )\n\n        print("=" * 72)\n        print(\n            "SRE AGENT CAPABILITY BASELINE V1 FAILED"\n        )\n        print("=" * 72)\n        print("")\n        print("Upload:")\n        print(error_path)\n\n        return 1\n\n\nif __name__ == "__main__":\n    raise SystemExit(\n        main()\n    )\n'
TEST_SOURCE = 'from __future__ import annotations\n\nimport asyncio\nfrom pathlib import Path\n\nfrom services.agent_runtime.app.evaluation.capability.baseline import (\n    CapabilityLevel,\n    build_capability_assessments,\n    build_report,\n    collect_repository_signals,\n    run_behavioral_exams,\n)\n\n\ndef repo_root() -> Path:\n    return (\n        Path(\n            __file__\n        )\n        .resolve()\n        .parents[3]\n    )\n\n\ndef test_behavioral_exams_all_pass():\n    results = asyncio.run(\n        run_behavioral_exams()\n    )\n\n    assert len(\n        results\n    ) == 4\n\n    assert all(\n        result.passed\n        for result in results\n    )\n\n\ndef test_current_investigation_capabilities_are_not_scored_as_production():\n    root = repo_root()\n\n    signals = (\n        collect_repository_signals(\n            root\n        )\n    )\n\n    assessments = (\n        build_capability_assessments(\n            signals\n        )\n    )\n\n    by_key = {\n        item.key: item\n        for item in assessments\n    }\n\n    assert (\n        by_key[\n            "iterative_investigation"\n        ].level\n        >= CapabilityLevel.L3\n    )\n\n    assert (\n        by_key[\n            "evidence_reasoning"\n        ].level\n        >= CapabilityLevel.L3\n    )\n\n    assert (\n        by_key[\n            "stop_and_abstain"\n        ].level\n        >= CapabilityLevel.L3\n    )\n\n    assert (\n        by_key[\n            "production_incident_validation"\n        ].level\n        < CapabilityLevel.L5\n    )\n\n\ndef test_missing_autonomous_capabilities_are_visible():\n    root = repo_root()\n\n    report = build_report(\n        root\n    )\n\n    by_key = {\n        item.key: item\n        for item in report.assessments\n    }\n\n    assert (\n        by_key[\n            "logs_investigation"\n        ].level\n        <= CapabilityLevel.L1\n    )\n\n    assert (\n        by_key[\n            "rag_knowledge"\n        ].level\n        <= CapabilityLevel.L1\n    )\n\n    assert (\n        by_key[\n            "dependency_reasoning"\n        ].level\n        <= CapabilityLevel.L1\n    )\n\n\ndef test_report_has_category_scores_and_recommendations():\n    root = repo_root()\n\n    report = build_report(\n        root\n    )\n\n    categories = {\n        item.category\n        for item in report.categories\n    }\n\n    assert {\n        "brain",\n        "evidence",\n        "knowledge",\n        "remediation",\n        "evaluation",\n    }.issubset(\n        categories\n    )\n\n    assert (\n        0\n        <= report.overall_score\n        <= 100\n    )\n\n    assert report.top_gaps\n    assert report.recommended_order\n\n\ndef test_l5_is_reserved_for_production_validation():\n    root = repo_root()\n\n    report = build_report(\n        root\n    )\n\n    if not report.production_validated:\n        assert all(\n            item.level\n            < CapabilityLevel.L5\n            for item in report.assessments\n        )\n'


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
        / "capability"
    )

    baseline_file = (
        package_dir
        / "baseline.py"
    )

    init_file = (
        package_dir
        / "__init__.py"
    )

    runner_file = (
        root
        / "scripts"
        / "dev"
        / "run_sre_agent_capability_baseline_v1.py"
    )

    test_file = (
        root
        / "services"
        / "agent_runtime"
        / "tests"
        / "test_sre_agent_capability_baseline.py"
    )

    targets = [
        baseline_file,
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
        "SRE Agent Capability Baseline v1 Installer",
        f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
        "",
        "Purpose:",
        "- make current SRE Agent capability visible",
        "- separate structural, tested, real-model/lab and production evidence",
        "- run deterministic behavioral exams against the real Investigation coordinator",
        "- produce text + JSON capability reports",
        "- modify no existing Runtime/Investigation production file",
        "- send no network request",
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
            baseline_file,
            BASELINE_SOURCE,
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
                    baseline_file.relative_to(
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

        focused = run_command(
            root=root,
            name="Capability Baseline focused tests",
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
                    "test_investigation_models.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_coordinator.py"
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
                "Capability Baseline focused tests failed"
            )

        run_baseline = run_command(
            root=root,
            name="Generate current capability report",
            command=[
                "uv",
                "run",
                "python",
                str(
                    runner_file.relative_to(
                        root
                    )
                ),
            ],
        )

        add_command(
            report,
            run_baseline,
        )

        if run_baseline.returncode != 0:
            raise RuntimeError(
                "Capability report generation failed"
            )

        report_file = (
            root
            / "sre_agent_capability_baseline_v1_report.txt"
        )

        json_file = (
            root
            / "sre_agent_capability_baseline_v1_report.json"
        )

        if (
            not report_file.exists()
            or not json_file.exists()
        ):
            raise RuntimeError(
                "Capability report artifacts were not generated"
            )

        report_text = (
            report_file
            .read_text(
                encoding="utf-8-sig",
                errors="replace",
            )
        )

        section(
            report,
            "CAPABILITY REPORT SUMMARY",
        )

        for line in report_text.splitlines():
            if (
                line.startswith(
                    "OverallScore:"
                )
                or line.startswith(
                    "OverallLevel:"
                )
                or line.startswith(
                    "ProductionValidated:"
                )
                or line.startswith(
                    "[PASS]"
                )
                or line.startswith(
                    "[FAIL]"
                )
            ):
                report.append(
                    line
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
            "RESULT",
        )

        report.extend(
            [
                "PASSED",
                "",
                "Installed:",
                str(
                    baseline_file.relative_to(
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
                "",
                "Generated:",
                "sre_agent_capability_baseline_v1_report.txt",
                "sre_agent_capability_baseline_v1_report.json",
                "",
                "No LLM/Kubernetes/Prometheus network request was sent.",
                "No existing production Runtime/Investigation file was modified.",
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
            "SRE AGENT CAPABILITY BASELINE V1 PASSED"
        )
        print("=" * 72)
        print("")
        print("Upload THREE files:")
        print(after)
        print(report_file)
        print(json_file)

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
            "SRE Agent Capability Baseline v1 Installer FAILED",
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
            "SRE AGENT CAPABILITY BASELINE V1 FAILED"
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
