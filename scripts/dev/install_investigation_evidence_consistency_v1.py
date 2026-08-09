from __future__ import annotations

import hashlib
import shutil
import subprocess
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


VERSION = "investigation-evidence-consistency-v1"

AFTER_NAME = (
    "investigation_evidence_consistency_v1_after.txt"
)

ERROR_NAME = (
    "investigation_evidence_consistency_v1_error.txt"
)

EXPECTED_HASHES = {'services/agent_runtime/app/investigation/epistemic_guard.py': 'bf12e0097d025aebe7e50f706e9d1f32b355067182e5ddb54d8a561b1148dd3f', 'services/agent_runtime/app/evaluation/intelligence_benchmark/scenarios.py': '0f756c261ef6fe82f1ebc11afdb5e6b2901fddf813d8ddf497f8a08e9bba2603'}

GUARD_SOURCE = 'from __future__ import annotations\n\nfrom dataclasses import dataclass\nimport re\n\nfrom services.agent_runtime.app.investigation.models import (\n    InvestigationDecision,\n    InvestigationState,\n    InvestigationStopReason,\n)\n\n\n@dataclass(\n    frozen=True,\n    slots=True,\n)\nclass EpistemicGuardResult:\n    """\n    Result of one structural conclusion-admissibility check.\n\n    The guard does not invent, rewrite or semantically classify a root cause.\n    It only checks whether a sufficient-evidence conclusion is backed by\n    positive support declared on at least one current hypothesis.\n    """\n\n    allowed: bool\n    code: str | None = None\n\n\nclass EpistemicConclusionGuard:\n    """\n    Fail-safe evidence-discipline guard for terminal RCA decisions.\n\n    This guard intentionally does NOT:\n    - infer a root cause;\n    - inspect domain-specific keywords;\n    - decide whether an alert is a false positive;\n    - replace the Investigation reasoner.\n\n    It only enforces generic epistemic invariants for\n    stop_reason=sufficient_evidence:\n\n    1. at least one current hypothesis has positive supporting evidence;\n    2. every conclusion evidence ID is positive support for one hypothesis;\n    3. conclusion evidence is not conflicting evidence for that hypothesis;\n    4. the supporting hypothesis has a minimum confidence;\n    5. the positively supported hypothesis used for the conclusion has no\n       unresolved root-cause-critical missing_evidence;\n       optional_evidence is explicitly non-blocking corroboration;\n    6. conclusion confidence may not materially exceed that hypothesis.\n\n    If these invariants are not met, the Coordinator may safely downgrade the\n    decision to insufficient_evidence instead of accepting an unsupported RCA.\n    """\n\n    def __init__(\n        self,\n        *,\n        min_supported_confidence: float = 0.5,\n        max_conclusion_confidence_delta: float = 0.05,\n        min_memory_limit_pressure_ratio: float = 0.90,\n    ) -> None:\n        if not (\n            0.0\n            <= min_supported_confidence\n            <= 1.0\n        ):\n            raise ValueError(\n                "min_supported_confidence must be within [0,1]"\n            )\n\n        if not (\n            0.0\n            <= max_conclusion_confidence_delta\n            <= 1.0\n        ):\n            raise ValueError(\n                "max_conclusion_confidence_delta must be within [0,1]"\n            )\n\n        if not (\n            0.0\n            <= min_memory_limit_pressure_ratio\n            <= 1.0\n        ):\n            raise ValueError(\n                "min_memory_limit_pressure_ratio must be within [0,1]"\n            )\n\n        self.min_supported_confidence = (\n            min_supported_confidence\n        )\n\n        self.max_conclusion_confidence_delta = (\n            max_conclusion_confidence_delta\n        )\n\n        self.min_memory_limit_pressure_ratio = (\n            min_memory_limit_pressure_ratio\n        )\n\n    def evaluate(\n        self,\n        *,\n        decision: InvestigationDecision,\n        state: InvestigationState,\n    ) -> EpistemicGuardResult:\n        if not isinstance(\n            decision,\n            InvestigationDecision,\n        ):\n            raise TypeError(\n                "Epistemic guard decision is invalid"\n            )\n\n        if not isinstance(\n            state,\n            InvestigationState,\n        ):\n            raise TypeError(\n                "Epistemic guard state is invalid"\n            )\n\n        if (\n            not decision.stop\n            or decision.stop_reason\n            != InvestigationStopReason.SUFFICIENT_EVIDENCE\n        ):\n            return EpistemicGuardResult(\n                allowed=True\n            )\n\n        conclusion = (\n            decision.conclusion\n        )\n\n        if conclusion is None:\n            return EpistemicGuardResult(\n                allowed=False,\n                code="MissingConclusion",\n            )\n\n        conclusion_ids = set(\n            conclusion.evidence_ids\n        )\n\n        if not conclusion_ids:\n            return EpistemicGuardResult(\n                allowed=False,\n                code="MissingConclusionEvidence",\n            )\n\n        positively_supported = [\n            hypothesis\n            for hypothesis\n            in decision.hypotheses\n            if hypothesis.supporting_evidence_ids\n        ]\n\n        if not positively_supported:\n            return EpistemicGuardResult(\n                allowed=False,\n                code="NoPositiveHypothesisSupport",\n            )\n\n        support_compatible = []\n\n        for hypothesis in positively_supported:\n            supporting_ids = set(\n                hypothesis.supporting_evidence_ids\n            )\n\n            conflicting_ids = set(\n                hypothesis.conflicting_evidence_ids\n            )\n\n            if not conclusion_ids.issubset(\n                supporting_ids\n            ):\n                continue\n\n            if conclusion_ids.intersection(\n                conflicting_ids\n            ):\n                continue\n\n            support_compatible.append(\n                hypothesis\n            )\n\n        if not support_compatible:\n            return EpistemicGuardResult(\n                allowed=False,\n                code="ConclusionEvidenceNotPositiveSupport",\n            )\n\n        # Only root-cause-blocking missing_evidence prevents a terminal RCA.\n        # optional_evidence is deliberately ignored here: it is corroboration,\n        # not a prerequisite for accepting the supported cause.\n        causally_complete = [\n            hypothesis\n            for hypothesis\n            in support_compatible\n            if not hypothesis.missing_evidence\n        ]\n\n        if not causally_complete:\n            return EpistemicGuardResult(\n                allowed=False,\n                code="SupportedHypothesisStillMissingEvidence",\n            )\n\n        quantitatively_consistent = [\n            hypothesis\n            for hypothesis\n            in causally_complete\n            if self._memory_limit_support_is_consistent(\n                hypothesis=hypothesis,\n                conclusion=conclusion,\n                state=state,\n            )\n        ]\n\n        if not quantitatively_consistent:\n            return EpistemicGuardResult(\n                allowed=False,\n                code="MemoryLimitEvidenceNotNearThreshold",\n            )\n\n        confidence_compatible = [\n            hypothesis\n            for hypothesis\n            in quantitatively_consistent\n            if (\n                hypothesis.confidence\n                >= self.min_supported_confidence\n            )\n        ]\n\n        if not confidence_compatible:\n            return EpistemicGuardResult(\n                allowed=False,\n                code="SupportedHypothesisConfidenceTooLow",\n            )\n\n        for hypothesis in confidence_compatible:\n            permitted = min(\n                1.0,\n                (\n                    hypothesis.confidence\n                    + self.max_conclusion_confidence_delta\n                ),\n            )\n\n            if (\n                conclusion.confidence\n                <= permitted\n            ):\n                return EpistemicGuardResult(\n                    allowed=True\n                )\n\n        return EpistemicGuardResult(\n            allowed=False,\n            code="ConclusionConfidenceExceedsSupport",\n        )\n\n    def _memory_limit_support_is_consistent(\n        self,\n        *,\n        hypothesis,\n        conclusion,\n        state: InvestigationState,\n    ) -> bool:\n        """\n        Deterministic consistency policy for one explicit threshold mechanism.\n\n        This does not infer a memory RCA. It only checks a model-proposed\n        positive claim that a configured container memory limit was exceeded.\n\n        The check deliberately uses ALL trusted production memory evidence\n        already present in InvestigationState, not only evidence IDs the model\n        chose to cite. This prevents evidence cherry-picking: a model may not\n        ignore sampled working-set/limit observations and claim that pod-state\n        evidence alone proves a specific memory-limit threshold mechanism.\n\n        For this specific threshold claim:\n        - working-set and limit evidence must exist when the claim is made;\n        - the strongest observed sampled pressure must be near the limit;\n        - both mechanism evidence IDs must be present in hypothesis positive\n          support and in the final conclusion grounding.\n\n        A far-below-limit sample does not prove that no historical spike ever\n        happened. It only means the currently available sampled metrics do not\n        positively establish the specific "memory limit exceeded" mechanism,\n        so sufficient_evidence is not admissible yet.\n        """\n\n        claim_text = (\n            hypothesis.cause\n            + " "\n            + conclusion.root_cause\n        ).strip().lower()\n\n        negative_patterns = (\n            "not exceeded",\n            "did not exceed",\n            "below the limit",\n            "within the limit",\n        )\n\n        if any(\n            pattern in claim_text\n            for pattern in negative_patterns\n        ):\n            return True\n\n        claims_limit_exceeded = (\n            "memory" in claim_text\n            and "limit" in claim_text\n            and bool(\n                re.search(\n                    r"\\b(exceed(?:ed|s|ing)?|exhaust(?:ed|ion)?|too\\s+low)\\b",\n                    claim_text,\n                )\n            )\n        )\n\n        if not claims_limit_exceeded:\n            return True\n\n        trusted_working = []\n        trusted_limits = []\n\n        for item in state.evidence:\n            if not (\n                item.success\n                and item.trusted\n                and item.production_signal\n            ):\n                continue\n\n            if (\n                item.probe.value\n                == "prometheus_memory_working_set"\n            ):\n                value = self._numeric_evidence_value(\n                    item.facts\n                )\n\n                if value is not None:\n                    trusted_working.append(\n                        (\n                            item.evidence_id,\n                            value,\n                        )\n                    )\n\n            elif (\n                item.probe.value\n                == "prometheus_memory_limit"\n            ):\n                value = self._numeric_evidence_value(\n                    item.facts\n                )\n\n                if (\n                    value is not None\n                    and value > 0.0\n                ):\n                    trusted_limits.append(\n                        (\n                            item.evidence_id,\n                            value,\n                        )\n                    )\n\n        if (\n            not trusted_working\n            or not trusted_limits\n        ):\n            return False\n\n        working_id, working = max(\n            trusted_working,\n            key=lambda item: item[1],\n        )\n\n        limit_id, limit = min(\n            trusted_limits,\n            key=lambda item: item[1],\n        )\n\n        ratio = (\n            working\n            / limit\n        )\n\n        if (\n            ratio\n            < self.min_memory_limit_pressure_ratio\n        ):\n            return False\n\n        support_ids = set(\n            hypothesis.supporting_evidence_ids\n        )\n\n        conclusion_ids = set(\n            conclusion.evidence_ids\n        )\n\n        required_ids = {\n            working_id,\n            limit_id,\n        }\n\n        return (\n            required_ids.issubset(\n                support_ids\n            )\n            and required_ids.issubset(\n                conclusion_ids\n            )\n        )\n\n    @staticmethod\n    def _numeric_evidence_value(\n        facts,\n    ) -> float | None:\n        for key in (\n            "value_max",\n            "value_sum",\n        ):\n            value = facts.get(\n                key\n            )\n\n            if isinstance(\n                value,\n                (\n                    int,\n                    float,\n                ),\n            ) and not isinstance(\n                value,\n                bool,\n            ):\n                return float(\n                    value\n                )\n\n        return None\n\n\n__all__ = [\n    "EpistemicConclusionGuard",\n    "EpistemicGuardResult",\n]\n'
SCENARIOS_SOURCE = 'from __future__ import annotations\n\nfrom services.agent_runtime.app.evaluation.intelligence_benchmark.engine import (\n    BenchmarkScenario,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    InvestigationProbe,\n    InvestigationStopReason,\n)\n\n\ndef _all_probes(\n    *,\n    pod_state,\n    working_set,\n    memory_limit,\n    restart_count,\n):\n    return {\n        InvestigationProbe.KUBERNETES_POD_STATE: (\n            pod_state\n        ),\n        InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET: {\n            "value_sum": float(\n                working_set\n            ),\n        },\n        InvestigationProbe.PROMETHEUS_MEMORY_LIMIT: {\n            "value_sum": float(\n                memory_limit\n            ),\n        },\n        InvestigationProbe.PROMETHEUS_RESTART_COUNT: {\n            "value_sum": float(\n                restart_count\n            ),\n        },\n    }\n\n\nSCENARIOS = [\n    BenchmarkScenario(\n        key="oom_limit_pressure",\n        title=(\n            "Clear OOM with memory pressure near container limit"\n        ),\n        alert_name="PodOOMKilled",\n        alert_message=(\n            "payment-api restarted unexpectedly"\n        ),\n        evidence_by_probe=_all_probes(\n            pod_state={\n                "phase": "Running",\n                "ready": False,\n                "scheduled": True,\n                "oom_killed": True,\n                "max_restart_count": 7,\n                "state_reasons": (\n                    "CrashLoopBackOff"\n                ),\n                "last_termination_reasons": (\n                    "OOMKilled"\n                ),\n            },\n            working_set=530_000_000,\n            memory_limit=536_870_912,\n            restart_count=7,\n        ),\n        hidden_expected_stop_reason=(\n            InvestigationStopReason.SUFFICIENT_EVIDENCE\n        ),\n        hidden_required_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n            InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,\n        ],\n        hidden_preferred_first_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n        ],\n        hidden_root_cause_keyword_groups=[\n            [\n                "memory",\n                "内存",\n            ],\n            [\n                "limit",\n                "限制",\n                "oom",\n            ],\n        ],\n        hidden_max_reasonable_tool_calls=4,\n    ),\n    BenchmarkScenario(\n        key="crashloop_not_memory",\n        title=(\n            "CrashLoop with normal memory should not be mislabeled as OOM"\n        ),\n        alert_name="PodRestartHigh",\n        alert_message=(\n            "payment-api restart count is increasing"\n        ),\n        evidence_by_probe={\n            **_all_probes(\n                pod_state={\n                    "phase": "Running",\n                    "ready": False,\n                    "scheduled": True,\n                    "oom_killed": False,\n                    "max_restart_count": 9,\n                    "state_reasons": (\n                        "CrashLoopBackOff"\n                    ),\n                    "last_termination_reasons": (\n                        "Error"\n                    ),\n                },\n                working_set=120_000_000,\n                memory_limit=536_870_912,\n                restart_count=9,\n            ),\n            (\n                InvestigationProbe\n                .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n            ): "unavailable",\n        },\n        hidden_expected_stop_reason=(\n            InvestigationStopReason.INSUFFICIENT_EVIDENCE\n        ),\n        hidden_required_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n            InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,\n        ],\n        hidden_preferred_first_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n            InvestigationProbe.PROMETHEUS_RESTART_COUNT,\n        ],\n        hidden_missing_capability_keywords=[\n            "log",\n            "日志",\n            "stderr",\n            "stdout",\n            "container output",\n        ],\n        hidden_max_reasonable_tool_calls=4,\n    ),\n    BenchmarkScenario(\n        key="conflicting_oom_signal",\n        title=(\n            "Alert suggests OOM while bounded evidence does not confirm it"\n        ),\n        alert_name="PodOOMKilled",\n        alert_message=(\n            "OOM-related alert fired for payment-api"\n        ),\n        evidence_by_probe=_all_probes(\n            pod_state={\n                "phase": "Running",\n                "ready": True,\n                "scheduled": True,\n                "oom_killed": False,\n                "max_restart_count": 1,\n                "state_reasons": "",\n                "last_termination_reasons": (\n                    "Completed"\n                ),\n            },\n            working_set=470_000_000,\n            memory_limit=536_870_912,\n            restart_count=1,\n        ),\n        hidden_expected_stop_reason=(\n            InvestigationStopReason.INSUFFICIENT_EVIDENCE\n        ),\n        hidden_required_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n            InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,\n        ],\n        hidden_preferred_first_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n        ],\n        hidden_max_reasonable_tool_calls=4,\n    ),\n    BenchmarkScenario(\n        key="crashloop_previous_log_rca",\n        title=(\n            "CrashLoop previous-container log provides causal startup evidence"\n        ),\n        alert_name="PodRestartHigh",\n        alert_message=(\n            "payment-api restart count is increasing"\n        ),\n        evidence_by_probe={\n            **_all_probes(\n                pod_state={\n                    "phase": "Running",\n                    "ready": False,\n                    "scheduled": True,\n                    "oom_killed": False,\n                    "max_restart_count": 9,\n                    "state_reasons": (\n                        "CrashLoopBackOff"\n                    ),\n                    "last_termination_reasons": (\n                        "Error"\n                    ),\n                },\n                working_set=120_000_000,\n                memory_limit=536_870_912,\n                restart_count=9,\n            ),\n            (\n                InvestigationProbe\n                .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n            ): {\n                "temporal_basis": (\n                    "previous_container"\n                ),\n                "container_name": (\n                    "payment-api"\n                ),\n                "previous": True,\n                "log_line_count": 2,\n                "tool_truncated": False,\n                "evidence_truncated": False,\n                "redaction_count": 1,\n                "log_excerpt": (\n                    "panic: invalid configuration: "\n                    "MAX_CONNECTIONS must be >= 1\\n"\n                    "password=[REDACTED]"\n                ),\n            },\n        },\n        hidden_expected_stop_reason=(\n            InvestigationStopReason.SUFFICIENT_EVIDENCE\n        ),\n        hidden_required_probes=[\n            (\n                InvestigationProbe\n                .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n            ),\n        ],\n        hidden_preferred_first_probes=[\n            (\n                InvestigationProbe\n                .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n            ),\n            InvestigationProbe.KUBERNETES_POD_STATE,\n            InvestigationProbe.PROMETHEUS_RESTART_COUNT,\n        ],\n        hidden_root_cause_keyword_groups=[\n            [\n                "panic",\n            ],\n            [\n                "config",\n                "configuration",\n            ],\n        ],\n        hidden_max_reasonable_tool_calls=4,\n    ),\n    BenchmarkScenario(\n        key="memory_false_alarm",\n        title=(\n            "Healthy memory state should drive safe abstention"\n        ),\n        alert_name="PodMemoryHigh",\n        alert_message=(\n            "payment-api memory alert fired"\n        ),\n        evidence_by_probe=_all_probes(\n            pod_state={\n                "phase": "Running",\n                "ready": True,\n                "scheduled": True,\n                "oom_killed": False,\n                "max_restart_count": 0,\n                "state_reasons": "",\n                "last_termination_reasons": "",\n            },\n            working_set=220_000_000,\n            memory_limit=536_870_912,\n            restart_count=0,\n        ),\n        hidden_expected_stop_reason=(\n            InvestigationStopReason.INSUFFICIENT_EVIDENCE\n        ),\n        hidden_required_probes=[\n            InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,\n            InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,\n        ],\n        hidden_preferred_first_probes=[\n            InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,\n            InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,\n        ],\n        hidden_max_reasonable_tool_calls=3,\n    ),\n    BenchmarkScenario(\n        key="probe_backend_failure",\n        title=(\n            "Unavailable pod evidence must not produce fabricated RCA"\n        ),\n        alert_name="PodRestartHigh",\n        alert_message=(\n            "payment-api restarts are elevated"\n        ),\n        evidence_by_probe={\n            InvestigationProbe.KUBERNETES_POD_STATE: (\n                "unavailable"\n            ),\n            InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET: {\n                "value_sum": 150_000_000.0,\n            },\n            InvestigationProbe.PROMETHEUS_MEMORY_LIMIT: {\n                "value_sum": 536_870_912.0,\n            },\n            InvestigationProbe.PROMETHEUS_RESTART_COUNT: {\n                "value_sum": 6.0,\n            },\n        },\n        hidden_expected_stop_reason=(\n            InvestigationStopReason.INSUFFICIENT_EVIDENCE\n        ),\n        hidden_required_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n        ],\n        hidden_preferred_first_probes=[\n            (\n                InvestigationProbe\n                .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n            ),\n            InvestigationProbe.KUBERNETES_POD_STATE,\n            InvestigationProbe.PROMETHEUS_RESTART_COUNT,\n        ],\n        hidden_missing_capability_keywords=[\n            "log",\n            "日志",\n            "pod state",\n            "termination",\n        ],\n        hidden_max_reasonable_tool_calls=4,\n    ),\n    BenchmarkScenario(\n        key="oom_without_explanatory_metrics",\n        title=(\n            "OOM termination with non-explanatory sampled metrics should remain cautious"\n        ),\n        alert_name="PodOOMKilled",\n        alert_message=(\n            "payment-api was terminated and restarted"\n        ),\n        evidence_by_probe=_all_probes(\n            pod_state={\n                "phase": "Running",\n                "ready": True,\n                "scheduled": True,\n                "oom_killed": True,\n                "max_restart_count": 3,\n                "state_reasons": "",\n                "last_termination_reasons": (\n                    "OOMKilled"\n                ),\n            },\n            working_set=300_000_000,\n            memory_limit=1_073_741_824,\n            restart_count=3,\n        ),\n        hidden_expected_stop_reason=(\n            InvestigationStopReason.INSUFFICIENT_EVIDENCE\n        ),\n        hidden_required_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n            InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,\n            InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,\n        ],\n        hidden_preferred_first_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n        ],\n        hidden_missing_capability_keywords=[\n            "histor",\n            "历史",\n            "range",\n            "peak",\n            "time",\n            "日志",\n            "log",\n        ],\n        hidden_max_reasonable_tool_calls=4,\n    ),\n]\n\n\nSMOKE_SCENARIO_KEYS = (\n    "oom_limit_pressure",\n    "crashloop_not_memory",\n    "conflicting_oom_signal",\n)\n\n\ndef scenarios_for_mode(\n    mode: str,\n) -> list[\n    BenchmarkScenario\n]:\n    if mode == "smoke":\n        keys = set(\n            SMOKE_SCENARIO_KEYS\n        )\n\n        return [\n            item\n            for item in SCENARIOS\n            if item.key in keys\n        ]\n\n    if mode == "full":\n        return list(\n            SCENARIOS\n        )\n\n    raise ValueError(\n        "Benchmark mode must be smoke or full"\n    )\n\n\ndef scenario_by_key(\n    key: str,\n) -> BenchmarkScenario:\n    for item in SCENARIOS:\n        if item.key == key:\n            return item\n\n    raise KeyError(\n        key\n    )\n\n\n__all__ = [\n    "SCENARIOS",\n    "SMOKE_SCENARIO_KEYS",\n    "scenario_by_key",\n    "scenarios_for_mode",\n]\n'
TEST_SOURCE = 'from __future__ import annotations\n\nfrom datetime import UTC, datetime\n\nfrom services.agent_runtime.app.evaluation.intelligence_benchmark.scenarios import (\n    scenario_by_key,\n)\nfrom services.agent_runtime.app.investigation.epistemic_guard import (\n    EpistemicConclusionGuard,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    EvidenceItem,\n    IncidentHypothesis,\n    InvestigationConclusion,\n    InvestigationDecision,\n    InvestigationProbe,\n    InvestigationScope,\n    InvestigationState,\n    InvestigationStopReason,\n)\n\n\nNOW = datetime(\n    2026,\n    8,\n    10,\n    14,\n    30,\n    tzinfo=UTC,\n)\n\n\ndef scope() -> InvestigationScope:\n    return InvestigationScope(\n        alert_name="PodOOMKilled",\n        alert_message="payment-api restarted",\n        resource="payment-api",\n        namespace="payment",\n        cluster="benchmark-lab",\n    )\n\n\ndef evidence(\n    evidence_id: str,\n    probe: InvestigationProbe,\n    *,\n    value: float | None = None,\n    oom_killed: bool | None = None,\n) -> EvidenceItem:\n    facts = {}\n\n    if value is not None:\n        facts["value_sum"] = value\n\n    if oom_killed is not None:\n        facts["oom_killed"] = oom_killed\n\n    return EvidenceItem(\n        evidence_id=evidence_id,\n        probe=probe,\n        source=(\n            "kubernetes"\n            if probe\n            == InvestigationProbe.KUBERNETES_POD_STATE\n            else "prometheus"\n        ),\n        success=True,\n        trusted=True,\n        production_signal=True,\n        reliability=1.0,\n        observed_at=NOW,\n        facts=facts,\n    )\n\n\ndef decision(\n    *,\n    supporting_ids,\n    conclusion_ids,\n) -> InvestigationDecision:\n    return InvestigationDecision(\n        hypotheses=[\n            IncidentHypothesis(\n                hypothesis_id="h1",\n                cause=(\n                    "container exceeded memory limit causing OOMKilled"\n                ),\n                confidence=0.9,\n                supporting_evidence_ids=list(\n                    supporting_ids\n                ),\n                conflicting_evidence_ids=[],\n                missing_evidence=[],\n                optional_evidence=[],\n            )\n        ],\n        rationale_summary=(\n            "model proposes a memory-limit threshold mechanism"\n        ),\n        stop=True,\n        stop_reason=(\n            InvestigationStopReason.SUFFICIENT_EVIDENCE\n        ),\n        conclusion=InvestigationConclusion(\n            root_cause=(\n                "container exceeded memory limit causing OOMKilled"\n            ),\n            confidence=0.9,\n            evidence_ids=list(\n                conclusion_ids\n            ),\n        ),\n    )\n\n\ndef test_guard_rejects_far_below_limit_even_if_model_omits_metrics_from_support():\n    current = InvestigationState(\n        scope=scope(),\n        evidence=[\n            evidence(\n                "pod",\n                InvestigationProbe.KUBERNETES_POD_STATE,\n                oom_killed=True,\n            ),\n            evidence(\n                "working",\n                InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,\n                value=300_000_000.0,\n            ),\n            evidence(\n                "limit",\n                InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,\n                value=1_073_741_824.0,\n            ),\n        ],\n    )\n\n    result = (\n        EpistemicConclusionGuard()\n        .evaluate(\n            decision=decision(\n                supporting_ids=[\n                    "pod"\n                ],\n                conclusion_ids=[\n                    "pod"\n                ],\n            ),\n            state=current,\n        )\n    )\n\n    assert result.allowed is False\n    assert (\n        result.code\n        == "MemoryLimitEvidenceNotNearThreshold"\n    )\n\n\ndef test_guard_rejects_near_limit_claim_when_model_omits_mechanism_evidence():\n    current = InvestigationState(\n        scope=scope(),\n        evidence=[\n            evidence(\n                "pod",\n                InvestigationProbe.KUBERNETES_POD_STATE,\n                oom_killed=True,\n            ),\n            evidence(\n                "working",\n                InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,\n                value=530_000_000.0,\n            ),\n            evidence(\n                "limit",\n                InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,\n                value=536_870_912.0,\n            ),\n        ],\n    )\n\n    result = (\n        EpistemicConclusionGuard()\n        .evaluate(\n            decision=decision(\n                supporting_ids=[\n                    "pod"\n                ],\n                conclusion_ids=[\n                    "pod"\n                ],\n            ),\n            state=current,\n        )\n    )\n\n    assert result.allowed is False\n    assert (\n        result.code\n        == "MemoryLimitEvidenceNotNearThreshold"\n    )\n\n\ndef test_guard_allows_near_limit_claim_when_mechanism_evidence_is_cited():\n    current = InvestigationState(\n        scope=scope(),\n        evidence=[\n            evidence(\n                "pod",\n                InvestigationProbe.KUBERNETES_POD_STATE,\n                oom_killed=True,\n            ),\n            evidence(\n                "working",\n                InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,\n                value=530_000_000.0,\n            ),\n            evidence(\n                "limit",\n                InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,\n                value=536_870_912.0,\n            ),\n        ],\n    )\n\n    result = (\n        EpistemicConclusionGuard()\n        .evaluate(\n            decision=decision(\n                supporting_ids=[\n                    "pod",\n                    "working",\n                    "limit",\n                ],\n                conclusion_ids=[\n                    "pod",\n                    "working",\n                    "limit",\n                ],\n            ),\n            state=current,\n        )\n    )\n\n    assert result.allowed is True\n    assert result.code is None\n\n\ndef test_crashloop_logs_scenario_accepts_direct_log_first_path():\n    scenario = scenario_by_key(\n        "crashloop_previous_log_rca"\n    )\n\n    assert (\n        scenario.hidden_required_probes\n        == [\n            InvestigationProbe.KUBERNETES_PREVIOUS_CONTAINER_LOGS\n        ]\n    )\n\n    assert (\n        InvestigationProbe.KUBERNETES_PREVIOUS_CONTAINER_LOGS\n        in scenario.hidden_preferred_first_probes\n    )\n\n\ndef test_backend_failure_accepts_previous_logs_as_reasonable_first_probe():\n    scenario = scenario_by_key(\n        "probe_backend_failure"\n    )\n\n    assert (\n        InvestigationProbe.KUBERNETES_PREVIOUS_CONTAINER_LOGS\n        in scenario.hidden_preferred_first_probes\n    )\n'


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

    guard_file = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "investigation"
        / "epistemic_guard.py"
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
        / "test_investigation_evidence_consistency.py"
    )

    sources = {
        guard_file: GUARD_SOURCE,
        scenarios_file: SCENARIOS_SOURCE,
    }

    targets = [
        *sources.keys(),
        test_file,
    ]

    preexisting = {
        path: path.exists()
        for path in targets
    }

    backups = []

    report = [
        "Investigation Evidence Consistency v1",
        f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
        "",
        "Observed acceptance results:",
        "- clear OOM now passes",
        "- backend failure now safely abstains",
        "- direct previous-container log RCA succeeds efficiently",
        "- non-explanatory OOM metrics still allowed a cherry-picked pod-only memory-limit RCA",
        "",
        "Guard changes:",
        "- explicit memory-limit-exceeded claims inspect all trusted production memory evidence already in state",
        "- the model cannot evade quantitative consistency by omitting metrics from supporting_evidence_ids",
        "- far-below-limit sampled pressure blocks sufficient_evidence",
        "- near-limit sampled pressure must also be cited in hypothesis support and final conclusion grounding",
        "",
        "Benchmark calibration:",
        "- direct previous-container logs are accepted as a preferred first probe for CrashLoop RCA",
        "- direct causal previous-container logs are the only required probe for that synthetic causal-log scenario",
        "- previous-container logs are accepted as a reasonable first probe for backend-failure restart investigation",
        "",
        "No Reasoner, Coordinator, Tool, Action, Approval, Verification or write authority is changed.",
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
            name="Evidence Consistency focused regression suite",
            command=[
                "uv",
                "run",
                "pytest",
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_evidence_consistency.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_decision_robustness.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_control_semantics.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_causal_sufficiency.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_epistemic_guard.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_intelligence_benchmark.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_logs_benchmark.py"
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
                "Evidence Consistency focused tests failed"
            )

        compatibility = run_command(
            root=root,
            name="Investigation compatibility suite",
            command=[
                "uv",
                "run",
                "pytest",
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_reasoner.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_models.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_coordinator.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_logs.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_probes.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_evidence_time_policy.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_historical_evidence_replay.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_historical_incident_investigation_runner.py"
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
                "Investigation compatibility tests failed"
            )

        preflight = run_command(
            root=root,
            name="Evidence consistency preflight",
            command=[
                "uv",
                "run",
                "python",
                "-c",
                (
                    "from services.agent_runtime.app.evaluation."
                    "intelligence_benchmark.scenarios import scenario_by_key; "
                    "from services.agent_runtime.app.investigation.models "
                    "import InvestigationProbe; "
                    "c=scenario_by_key('crashloop_previous_log_rca'); "
                    "b=scenario_by_key('probe_backend_failure'); "
                    "print('crash_required='+str([x.value for x in c.hidden_required_probes])); "
                    "print('crash_log_first='+str("
                    "InvestigationProbe.KUBERNETES_PREVIOUS_CONTAINER_LOGS "
                    "in c.hidden_preferred_first_probes)); "
                    "print('backend_log_first='+str("
                    "InvestigationProbe.KUBERNETES_PREVIOUS_CONTAINER_LOGS "
                    "in b.hidden_preferred_first_probes)); "
                    "assert c.hidden_required_probes == "
                    "[InvestigationProbe.KUBERNETES_PREVIOUS_CONTAINER_LOGS]"
                ),
            ],
        )

        add_command(
            report,
            preflight,
        )

        if preflight.returncode != 0:
            raise RuntimeError(
                "Evidence consistency preflight failed"
            )

        authority = run_command(
            root=root,
            name="Authority boundary",
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
                "Evidence Consistency authority boundary failed"
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
                "Evidence Consistency v1 is installed.",
                "",
                "Expected acceptance changes:",
                "- oom_without_explanatory_metrics can no longer succeed by citing pod-state only while ignoring far-below-limit metrics",
                "- oom_limit_pressure remains admissible when near-limit mechanism evidence is explicitly cited",
                "- crashloop_previous_log_rca direct log-first one-tool path receives full evaluator credit",
                "- probe_backend_failure log-first safe-abstention path receives full evaluator first-probe credit",
                "",
                "Next:",
                "rerun the same four acceptance scenarios together, then Full if all outcomes are correct.",
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
            "INVESTIGATION EVIDENCE CONSISTENCY V1 PASSED"
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
                    "Investigation Evidence Consistency v1 FAILED",
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
            "INVESTIGATION EVIDENCE CONSISTENCY V1 FAILED"
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
