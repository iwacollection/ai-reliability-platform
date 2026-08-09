from __future__ import annotations

import ast
import shutil
import subprocess
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


VERSION = "investigation-causal-sufficiency-v1"
AFTER_NAME = "investigation_causal_sufficiency_v1_after.txt"
ERROR_NAME = "investigation_causal_sufficiency_v1_error.txt"

GUARD_OLD = 'from __future__ import annotations\n\nfrom dataclasses import dataclass\n\nfrom services.agent_runtime.app.investigation.models import (\n    InvestigationDecision,\n    InvestigationState,\n    InvestigationStopReason,\n)\n\n\n@dataclass(\n    frozen=True,\n    slots=True,\n)\nclass EpistemicGuardResult:\n    """\n    Result of one structural conclusion-admissibility check.\n\n    The guard does not invent, rewrite or semantically classify a root cause.\n    It only checks whether a sufficient-evidence conclusion is backed by\n    positive support declared on at least one current hypothesis.\n    """\n\n    allowed: bool\n    code: str | None = None\n\n\nclass EpistemicConclusionGuard:\n    """\n    Fail-safe evidence-discipline guard for terminal RCA decisions.\n\n    This guard intentionally does NOT:\n    - infer a root cause;\n    - inspect domain-specific keywords;\n    - decide whether an alert is a false positive;\n    - replace the Investigation reasoner.\n\n    It only enforces generic epistemic invariants for\n    stop_reason=sufficient_evidence:\n\n    1. at least one current hypothesis has positive supporting evidence;\n    2. every conclusion evidence ID is positive support for one hypothesis;\n    3. conclusion evidence is not conflicting evidence for that hypothesis;\n    4. the supporting hypothesis has a minimum confidence;\n    5. conclusion confidence may not materially exceed that hypothesis.\n\n    If these invariants are not met, the Coordinator may safely downgrade the\n    decision to insufficient_evidence instead of accepting an unsupported RCA.\n    """\n\n    def __init__(\n        self,\n        *,\n        min_supported_confidence: float = 0.5,\n        max_conclusion_confidence_delta: float = 0.05,\n    ) -> None:\n        if not (\n            0.0\n            <= min_supported_confidence\n            <= 1.0\n        ):\n            raise ValueError(\n                "min_supported_confidence must be within [0,1]"\n            )\n\n        if not (\n            0.0\n            <= max_conclusion_confidence_delta\n            <= 1.0\n        ):\n            raise ValueError(\n                "max_conclusion_confidence_delta must be within [0,1]"\n            )\n\n        self.min_supported_confidence = (\n            min_supported_confidence\n        )\n\n        self.max_conclusion_confidence_delta = (\n            max_conclusion_confidence_delta\n        )\n\n    def evaluate(\n        self,\n        *,\n        decision: InvestigationDecision,\n        state: InvestigationState,\n    ) -> EpistemicGuardResult:\n        if not isinstance(\n            decision,\n            InvestigationDecision,\n        ):\n            raise TypeError(\n                "Epistemic guard decision is invalid"\n            )\n\n        if not isinstance(\n            state,\n            InvestigationState,\n        ):\n            raise TypeError(\n                "Epistemic guard state is invalid"\n            )\n\n        if (\n            not decision.stop\n            or decision.stop_reason\n            != InvestigationStopReason.SUFFICIENT_EVIDENCE\n        ):\n            return EpistemicGuardResult(\n                allowed=True\n            )\n\n        conclusion = (\n            decision.conclusion\n        )\n\n        if conclusion is None:\n            return EpistemicGuardResult(\n                allowed=False,\n                code="MissingConclusion",\n            )\n\n        conclusion_ids = set(\n            conclusion.evidence_ids\n        )\n\n        if not conclusion_ids:\n            return EpistemicGuardResult(\n                allowed=False,\n                code="MissingConclusionEvidence",\n            )\n\n        positively_supported = [\n            hypothesis\n            for hypothesis\n            in decision.hypotheses\n            if hypothesis.supporting_evidence_ids\n        ]\n\n        if not positively_supported:\n            return EpistemicGuardResult(\n                allowed=False,\n                code="NoPositiveHypothesisSupport",\n            )\n\n        support_compatible = []\n\n        for hypothesis in positively_supported:\n            supporting_ids = set(\n                hypothesis.supporting_evidence_ids\n            )\n\n            conflicting_ids = set(\n                hypothesis.conflicting_evidence_ids\n            )\n\n            if not conclusion_ids.issubset(\n                supporting_ids\n            ):\n                continue\n\n            if conclusion_ids.intersection(\n                conflicting_ids\n            ):\n                continue\n\n            support_compatible.append(\n                hypothesis\n            )\n\n        if not support_compatible:\n            return EpistemicGuardResult(\n                allowed=False,\n                code="ConclusionEvidenceNotPositiveSupport",\n            )\n\n        confidence_compatible = [\n            hypothesis\n            for hypothesis\n            in support_compatible\n            if (\n                hypothesis.confidence\n                >= self.min_supported_confidence\n            )\n        ]\n\n        if not confidence_compatible:\n            return EpistemicGuardResult(\n                allowed=False,\n                code="SupportedHypothesisConfidenceTooLow",\n            )\n\n        for hypothesis in confidence_compatible:\n            permitted = min(\n                1.0,\n                (\n                    hypothesis.confidence\n                    + self.max_conclusion_confidence_delta\n                ),\n            )\n\n            if (\n                conclusion.confidence\n                <= permitted\n            ):\n                return EpistemicGuardResult(\n                    allowed=True\n                )\n\n        return EpistemicGuardResult(\n            allowed=False,\n            code="ConclusionConfidenceExceedsSupport",\n        )\n\n\n__all__ = [\n    "EpistemicConclusionGuard",\n    "EpistemicGuardResult",\n]\n'
GUARD_NEW = 'from __future__ import annotations\n\nfrom dataclasses import dataclass\n\nfrom services.agent_runtime.app.investigation.models import (\n    InvestigationDecision,\n    InvestigationState,\n    InvestigationStopReason,\n)\n\n\n@dataclass(\n    frozen=True,\n    slots=True,\n)\nclass EpistemicGuardResult:\n    """\n    Result of one structural conclusion-admissibility check.\n\n    The guard does not invent, rewrite or semantically classify a root cause.\n    It only checks whether a sufficient-evidence conclusion is backed by\n    positive support declared on at least one current hypothesis.\n    """\n\n    allowed: bool\n    code: str | None = None\n\n\nclass EpistemicConclusionGuard:\n    """\n    Fail-safe evidence-discipline guard for terminal RCA decisions.\n\n    This guard intentionally does NOT:\n    - infer a root cause;\n    - inspect domain-specific keywords;\n    - decide whether an alert is a false positive;\n    - replace the Investigation reasoner.\n\n    It only enforces generic epistemic invariants for\n    stop_reason=sufficient_evidence:\n\n    1. at least one current hypothesis has positive supporting evidence;\n    2. every conclusion evidence ID is positive support for one hypothesis;\n    3. conclusion evidence is not conflicting evidence for that hypothesis;\n    4. the supporting hypothesis has a minimum confidence;\n    5. the positively supported hypothesis used for the conclusion has no\n       unresolved root-cause-critical missing_evidence;\n    6. conclusion confidence may not materially exceed that hypothesis.\n\n    If these invariants are not met, the Coordinator may safely downgrade the\n    decision to insufficient_evidence instead of accepting an unsupported RCA.\n    """\n\n    def __init__(\n        self,\n        *,\n        min_supported_confidence: float = 0.5,\n        max_conclusion_confidence_delta: float = 0.05,\n    ) -> None:\n        if not (\n            0.0\n            <= min_supported_confidence\n            <= 1.0\n        ):\n            raise ValueError(\n                "min_supported_confidence must be within [0,1]"\n            )\n\n        if not (\n            0.0\n            <= max_conclusion_confidence_delta\n            <= 1.0\n        ):\n            raise ValueError(\n                "max_conclusion_confidence_delta must be within [0,1]"\n            )\n\n        self.min_supported_confidence = (\n            min_supported_confidence\n        )\n\n        self.max_conclusion_confidence_delta = (\n            max_conclusion_confidence_delta\n        )\n\n    def evaluate(\n        self,\n        *,\n        decision: InvestigationDecision,\n        state: InvestigationState,\n    ) -> EpistemicGuardResult:\n        if not isinstance(\n            decision,\n            InvestigationDecision,\n        ):\n            raise TypeError(\n                "Epistemic guard decision is invalid"\n            )\n\n        if not isinstance(\n            state,\n            InvestigationState,\n        ):\n            raise TypeError(\n                "Epistemic guard state is invalid"\n            )\n\n        if (\n            not decision.stop\n            or decision.stop_reason\n            != InvestigationStopReason.SUFFICIENT_EVIDENCE\n        ):\n            return EpistemicGuardResult(\n                allowed=True\n            )\n\n        conclusion = (\n            decision.conclusion\n        )\n\n        if conclusion is None:\n            return EpistemicGuardResult(\n                allowed=False,\n                code="MissingConclusion",\n            )\n\n        conclusion_ids = set(\n            conclusion.evidence_ids\n        )\n\n        if not conclusion_ids:\n            return EpistemicGuardResult(\n                allowed=False,\n                code="MissingConclusionEvidence",\n            )\n\n        positively_supported = [\n            hypothesis\n            for hypothesis\n            in decision.hypotheses\n            if hypothesis.supporting_evidence_ids\n        ]\n\n        if not positively_supported:\n            return EpistemicGuardResult(\n                allowed=False,\n                code="NoPositiveHypothesisSupport",\n            )\n\n        support_compatible = []\n\n        for hypothesis in positively_supported:\n            supporting_ids = set(\n                hypothesis.supporting_evidence_ids\n            )\n\n            conflicting_ids = set(\n                hypothesis.conflicting_evidence_ids\n            )\n\n            if not conclusion_ids.issubset(\n                supporting_ids\n            ):\n                continue\n\n            if conclusion_ids.intersection(\n                conflicting_ids\n            ):\n                continue\n\n            support_compatible.append(\n                hypothesis\n            )\n\n        if not support_compatible:\n            return EpistemicGuardResult(\n                allowed=False,\n                code="ConclusionEvidenceNotPositiveSupport",\n            )\n\n        causally_complete = [\n            hypothesis\n            for hypothesis\n            in support_compatible\n            if not hypothesis.missing_evidence\n        ]\n\n        if not causally_complete:\n            return EpistemicGuardResult(\n                allowed=False,\n                code="SupportedHypothesisStillMissingEvidence",\n            )\n\n        confidence_compatible = [\n            hypothesis\n            for hypothesis\n            in causally_complete\n            if (\n                hypothesis.confidence\n                >= self.min_supported_confidence\n            )\n        ]\n\n        if not confidence_compatible:\n            return EpistemicGuardResult(\n                allowed=False,\n                code="SupportedHypothesisConfidenceTooLow",\n            )\n\n        for hypothesis in confidence_compatible:\n            permitted = min(\n                1.0,\n                (\n                    hypothesis.confidence\n                    + self.max_conclusion_confidence_delta\n                ),\n            )\n\n            if (\n                conclusion.confidence\n                <= permitted\n            ):\n                return EpistemicGuardResult(\n                    allowed=True\n                )\n\n        return EpistemicGuardResult(\n            allowed=False,\n            code="ConclusionConfidenceExceedsSupport",\n        )\n\n\n__all__ = [\n    "EpistemicConclusionGuard",\n    "EpistemicGuardResult",\n]\n'

REASONER_OLD_BLOCK = '        trusted_evidence_ids = [\n            item.evidence_id\n            for item in state.evidence\n            if (\n                item.success\n                and item.trusted\n                and item.production_signal\n            )\n        ]\n\n        state_payload = {\n            "scope": scope.model_dump(\n                mode="json"\n            ),\n            "iteration_count": state.iteration_count,\n            "tool_call_count": state.tool_call_count,\n            "attempted_probes": [\n                probe.value\n                for probe in state.attempted_probes\n            ],\n            "hypotheses": [\n                item.model_dump(mode="json")\n                for item in state.hypotheses\n            ],\n            "evidence": [\n                item.model_dump(mode="json")\n                for item in state.evidence\n            ],\n            "trusted_evidence_ids": trusted_evidence_ids,\n            "allowed_probes": [\n                probe.value\n                for probe in InvestigationProbe\n            ],\n        }\n\n        return (\n            "Investigate the incident using the bounded state below.\\n"\n            "Return exactly one JSON object only. Do not return markdown.\\n"\n            "Maintain competing hypotheses and update confidence from evidence.\\n"\n            "Never repeat a probe already listed in attempted_probes.\\n"\n            "Never cite an evidence ID that is absent from State.evidence.\\n"\n            "Conflicting evidence can weaken a hypothesis but cannot by itself "\n            "positively establish an unrelated root cause.\\n"\n            "Ruling out one hypothesis is not sufficient evidence for a different "\n            "causal claim.\\n"\n            "Current-state evidence does not by itself prove that a historical "\n            "event never occurred.\\n"\n            "For sufficient_evidence, the conclusion must affirm a positively "\n            "supported current hypothesis. Every conclusion.evidence_id must also "\n            "appear in that hypothesis supporting_evidence_ids and must not be "\n            "conflicting evidence for that same hypothesis.\\n"\n            "Conclusion confidence must not materially exceed the confidence of "\n            "the positively supported hypothesis.\\n"\n            "If the available evidence only rejects hypotheses and does not "\n            "positively establish a root cause, stop with insufficient_evidence.\\n"\n            "For a conclusion, evidence_ids must be non-empty and copied "\n            "exactly from trusted_evidence_ids.\\n"\n            "Allowed model terminal stop_reason values are exactly: "\n            "sufficient_evidence, insufficient_evidence, no_safe_probe.\\n"\n            "Do not emit internal coordinator reasons such as timeout, "\n            "max_iterations, max_tool_calls, duplicate_probe or reasoner_error.\\n"\n            "\\n"\n            "Continuing shape:\\n"\n            "{\\n"\n            \'  "hypotheses": [{"hypothesis_id": "h1", "cause": "candidate", \'\n            \'"confidence": 0.5, "supporting_evidence_ids": [], \'\n            \'"conflicting_evidence_ids": [], "missing_evidence": ["needed evidence"]}],\\n\'\n            \'  "rationale_summary": "why this probe is most discriminative",\\n\'\n            \'  "stop": false,\\n\'\n            \'  "stop_reason": null,\\n\'\n            \'  "next_probe": "one unattempted value from allowed_probes",\\n\'\n            \'  "conclusion": null\\n\'\n            "}\\n"\n            "\\n"\n            "Terminal sufficient-evidence shape:\\n"\n            "{\\n"\n            \'  "hypotheses": [{"hypothesis_id": "h1", "cause": "supported cause", \'\n            \'"confidence": 0.9, "supporting_evidence_ids": ["copy exact trusted id"], \'\n            \'"conflicting_evidence_ids": [], "missing_evidence": []}],\\n\'\n            \'  "rationale_summary": "why trusted evidence is sufficient",\\n\'\n            \'  "stop": true,\\n\'\n            \'  "stop_reason": "sufficient_evidence",\\n\'\n            \'  "next_probe": null,\\n\'\n            \'  "conclusion": {"root_cause": "bounded root cause", \'\n            \'"confidence": 0.9, "evidence_ids": ["copy exact trusted id"], \'\n            \'"remaining_uncertainties": []}\\n\'\n            "}\\n"\n            "\\n"\n            "Terminal insufficient/no-safe-probe shape:\\n"\n            "{\\n"\n            \'  "hypotheses": [{"hypothesis_id": "h1", "cause": "candidate", \'\n            \'"confidence": 0.3, "supporting_evidence_ids": [], \'\n            \'"conflicting_evidence_ids": [], "missing_evidence": ["what is still missing"]}],\\n\'\n            \'  "rationale_summary": "why current bounded evidence cannot support an RCA",\\n\'\n            \'  "stop": true,\\n\'\n            \'  "stop_reason": "insufficient_evidence",\\n\'\n            \'  "next_probe": null,\\n\'\n            \'  "conclusion": null\\n\'\n            "}\\n"\n            "\\n"\n            "If no useful unattempted allowed probe can resolve the remaining "\n            "uncertainty, stop with insufficient_evidence or no_safe_probe.\\n"\n            "State:\\n"\n            f"{json.dumps(state_payload, ensure_ascii=True, sort_keys=True)}"\n        )\n'
REASONER_NEW_BLOCK = '        trusted_evidence_ids = [\n            item.evidence_id\n            for item in state.evidence\n            if (\n                item.success\n                and item.trusted\n                and item.production_signal\n            )\n        ]\n\n        state_payload = {\n            "scope": scope.model_dump(\n                mode="json"\n            ),\n            "iteration_count": state.iteration_count,\n            "tool_call_count": state.tool_call_count,\n            "attempted_probes": [\n                probe.value\n                for probe in state.attempted_probes\n            ],\n            "hypotheses": [\n                item.model_dump(mode="json")\n                for item in state.hypotheses\n            ],\n            "evidence": [\n                item.model_dump(mode="json")\n                for item in state.evidence\n            ],\n            "trusted_evidence_ids": trusted_evidence_ids,\n            "allowed_probes": [\n                probe.value\n                for probe in InvestigationProbe\n            ],\n        }\n\n        return (\n            "Investigate the incident using the bounded state below.\\n"\n            "Return exactly one JSON object only. Do not return markdown.\\n"\n            "Maintain competing hypotheses and update confidence from evidence.\\n"\n            "Never repeat a probe already listed in attempted_probes.\\n"\n            "Never cite an evidence ID that is absent from State.evidence.\\n"\n            "Conflicting evidence can weaken a hypothesis but cannot by itself "\n            "positively establish an unrelated root cause.\\n"\n            "Ruling out one hypothesis is not sufficient evidence for a different "\n            "causal claim.\\n"\n            "Current-state evidence does not by itself prove that a historical "\n            "event never occurred.\\n"\n            "For sufficient_evidence, the conclusion must affirm a positively "\n            "supported current hypothesis. Every conclusion.evidence_id must also "\n            "appear in that hypothesis supporting_evidence_ids and must not be "\n            "conflicting evidence for that same hypothesis.\\n"\n            "Conclusion confidence must not materially exceed the confidence of "\n            "the positively supported hypothesis.\\n"\n            "A symptom or failure-mode observation such as CrashLoopBackOff, "\n            "restart count, unready state, high latency, or high error rate can "\n            "confirm that a failure exists, but does not by itself establish the "\n            "specific underlying cause that produced it.\\n"\n            "If several underlying causes remain plausible and current allowed "\n            "probes cannot discriminate among them, keep the required "\n            "root-cause evidence in hypothesis.missing_evidence and stop with "\n            "insufficient_evidence or no_safe_probe.\\n"\n            "Do not clear missing_evidence merely because all allowed probes "\n            "have been attempted. For sufficient_evidence, the positively "\n            "supported hypothesis used by the conclusion must have an empty "\n            "missing_evidence list.\\n"\n            "If the available evidence only rejects hypotheses or confirms a "\n            "symptom/failure mode without establishing its cause, stop with "\n            "insufficient_evidence.\\n"\n            "For a conclusion, evidence_ids must be non-empty and copied "\n            "exactly from trusted_evidence_ids.\\n"\n            "Allowed model terminal stop_reason values are exactly: "\n            "sufficient_evidence, insufficient_evidence, no_safe_probe.\\n"\n            "Do not emit internal coordinator reasons such as timeout, "\n            "max_iterations, max_tool_calls, duplicate_probe or reasoner_error.\\n"\n            "\\n"\n            "Continuing shape:\\n"\n            "{\\n"\n            \'  "hypotheses": [{"hypothesis_id": "h1", "cause": "candidate", \'\n            \'"confidence": 0.5, "supporting_evidence_ids": [], \'\n            \'"conflicting_evidence_ids": [], "missing_evidence": ["needed evidence"]}],\\n\'\n            \'  "rationale_summary": "why this probe is most discriminative",\\n\'\n            \'  "stop": false,\\n\'\n            \'  "stop_reason": null,\\n\'\n            \'  "next_probe": "one unattempted value from allowed_probes",\\n\'\n            \'  "conclusion": null\\n\'\n            "}\\n"\n            "\\n"\n            "Terminal sufficient-evidence shape:\\n"\n            "{\\n"\n            \'  "hypotheses": [{"hypothesis_id": "h1", "cause": "supported cause", \'\n            \'"confidence": 0.9, "supporting_evidence_ids": ["copy exact trusted id"], \'\n            \'"conflicting_evidence_ids": [], "missing_evidence": []}],\\n\'\n            \'  "rationale_summary": "why trusted evidence is sufficient",\\n\'\n            \'  "stop": true,\\n\'\n            \'  "stop_reason": "sufficient_evidence",\\n\'\n            \'  "next_probe": null,\\n\'\n            \'  "conclusion": {"root_cause": "bounded root cause", \'\n            \'"confidence": 0.9, "evidence_ids": ["copy exact trusted id"], \'\n            \'"remaining_uncertainties": []}\\n\'\n            "}\\n"\n            "\\n"\n            "Terminal insufficient/no-safe-probe shape:\\n"\n            "{\\n"\n            \'  "hypotheses": [{"hypothesis_id": "h1", "cause": "candidate", \'\n            \'"confidence": 0.3, "supporting_evidence_ids": [], \'\n            \'"conflicting_evidence_ids": [], "missing_evidence": ["what is still missing"]}],\\n\'\n            \'  "rationale_summary": "why current bounded evidence cannot support an RCA",\\n\'\n            \'  "stop": true,\\n\'\n            \'  "stop_reason": "insufficient_evidence",\\n\'\n            \'  "next_probe": null,\\n\'\n            \'  "conclusion": null\\n\'\n            "}\\n"\n            "\\n"\n            "If no useful unattempted allowed probe can resolve the remaining "\n            "uncertainty, stop with insufficient_evidence or no_safe_probe.\\n"\n            "State:\\n"\n            f"{json.dumps(state_payload, ensure_ascii=True, sort_keys=True)}"\n        )\n'

ENGINE_OLD_HELPER = 'def _all_reasoner_text(\n    decisions: list[\n        InvestigationDecision\n    ],\n) -> str:\n    fragments: list[\n        str\n    ] = []\n\n    for decision in decisions:\n        fragments.append(\n            decision.rationale_summary\n        )\n\n        for hypothesis in decision.hypotheses:\n            fragments.append(\n                hypothesis.cause\n            )\n\n            fragments.extend(\n                hypothesis.missing_evidence\n            )\n\n        if decision.conclusion is not None:\n            fragments.append(\n                decision.conclusion.root_cause\n            )\n\n            fragments.extend(\n                decision.conclusion.remaining_uncertainties\n            )\n\n    return _normalize_text(\n        "\\n".join(\n            fragments\n        )\n    )\n'
ENGINE_NEW_HELPER = 'def _missing_capability_text(\n    decisions: list[\n        InvestigationDecision\n    ],\n) -> str:\n    """\n    Return only explicit unresolved-evidence language.\n\n    Hypothesis causes, rationale prose and conclusion root-cause text are\n    intentionally excluded. Guessing "application panic" is not the same as\n    recognizing that application/container logs are missing.\n    """\n\n    fragments: list[\n        str\n    ] = []\n\n    for decision in decisions:\n        for hypothesis in decision.hypotheses:\n            fragments.extend(\n                hypothesis.missing_evidence\n            )\n\n        if decision.conclusion is not None:\n            fragments.extend(\n                decision.conclusion.remaining_uncertainties\n            )\n\n    return _normalize_text(\n        "\\n".join(\n            fragments\n        )\n    )\n'
ENGINE_OLD_CALL = 'reasoner_text = _all_reasoner_text(decisions)'
ENGINE_NEW_CALL = 'reasoner_text = _missing_capability_text(decisions)'

SCENARIOS_OLD = '        hidden_missing_capability_keywords=[\n            "log",\n            "日志",\n            "application",\n            "应用",\n        ],\n'
SCENARIOS_NEW = '        hidden_missing_capability_keywords=[\n            "log",\n            "日志",\n            "stderr",\n            "stdout",\n            "container output",\n        ],\n'

TEST_SOURCE = 'from __future__ import annotations\n\nfrom datetime import UTC, datetime\n\nfrom services.agent_runtime.app.evaluation.intelligence_benchmark.engine import (\n    BenchmarkScenario,\n    score_scenario,\n)\nfrom services.agent_runtime.app.evaluation.intelligence_benchmark.scenarios import (\n    scenario_by_key,\n)\nfrom services.agent_runtime.app.investigation.epistemic_guard import (\n    EpistemicConclusionGuard,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    EvidenceItem,\n    IncidentHypothesis,\n    InvestigationConclusion,\n    InvestigationDecision,\n    InvestigationProbe,\n    InvestigationScope,\n    InvestigationState,\n    InvestigationStatus,\n    InvestigationStopReason,\n)\nfrom services.agent_runtime.app.investigation.reasoner import (\n    LLMInvestigationReasoner,\n)\n\n\nNOW = datetime(\n    2026,\n    8,\n    10,\n    10,\n    45,\n    tzinfo=UTC,\n)\n\n\ndef scope() -> InvestigationScope:\n    return InvestigationScope(\n        alert_name="PodRestartHigh",\n        alert_message="payment-api restart count is increasing",\n        resource="payment-api",\n        namespace="payment",\n        cluster="benchmark-lab",\n    )\n\n\ndef evidence(\n    evidence_id: str,\n    probe: InvestigationProbe,\n) -> EvidenceItem:\n    return EvidenceItem(\n        evidence_id=evidence_id,\n        probe=probe,\n        source=(\n            "kubernetes"\n            if probe\n            == InvestigationProbe.KUBERNETES_POD_STATE\n            else "prometheus"\n        ),\n        success=True,\n        trusted=True,\n        production_signal=True,\n        reliability=1.0,\n        observed_at=NOW,\n        facts={\n            "observed": True,\n        },\n    )\n\n\ndef hypothesis(\n    *,\n    cause: str,\n    confidence: float,\n    supporting=None,\n    conflicting=None,\n    missing=None,\n) -> IncidentHypothesis:\n    return IncidentHypothesis(\n        hypothesis_id="h1",\n        cause=cause,\n        confidence=confidence,\n        supporting_evidence_ids=(\n            supporting\n            or []\n        ),\n        conflicting_evidence_ids=(\n            conflicting\n            or []\n        ),\n        missing_evidence=(\n            missing\n            or []\n        ),\n    )\n\n\ndef sufficient(\n    *,\n    hypothesis_value: IncidentHypothesis,\n    evidence_ids,\n    root_cause: str,\n    confidence: float,\n) -> InvestigationDecision:\n    return InvestigationDecision(\n        hypotheses=[\n            hypothesis_value\n        ],\n        rationale_summary="candidate root cause",\n        stop=True,\n        stop_reason=(\n            InvestigationStopReason.SUFFICIENT_EVIDENCE\n        ),\n        conclusion=(\n            InvestigationConclusion(\n                root_cause=root_cause,\n                confidence=confidence,\n                evidence_ids=list(\n                    evidence_ids\n                ),\n            )\n        ),\n    )\n\n\ndef test_guard_rejects_supported_hypothesis_with_unresolved_missing_evidence():\n    state = InvestigationState(\n        scope=scope(),\n        evidence=[\n            evidence(\n                "restart",\n                InvestigationProbe.PROMETHEUS_RESTART_COUNT,\n            ),\n            evidence(\n                "pod",\n                InvestigationProbe.KUBERNETES_POD_STATE,\n            ),\n        ],\n    )\n\n    decision = sufficient(\n        hypothesis_value=hypothesis(\n            cause=(\n                "CrashLoopBackOff due to application panic or misconfiguration"\n            ),\n            confidence=0.7,\n            supporting=[\n                "restart",\n                "pod",\n            ],\n            missing=[\n                "previous container logs or termination message"\n            ],\n        ),\n        evidence_ids=[\n            "restart",\n            "pod",\n        ],\n        root_cause=(\n            "CrashLoopBackOff due to application panic or misconfiguration"\n        ),\n        confidence=0.7,\n    )\n\n    result = (\n        EpistemicConclusionGuard()\n        .evaluate(\n            decision=decision,\n            state=state,\n        )\n    )\n\n    assert result.allowed is False\n    assert (\n        result.code\n        == "SupportedHypothesisStillMissingEvidence"\n    )\n\n\ndef test_guard_preserves_complete_positive_oom_conclusion():\n    state = InvestigationState(\n        scope=scope(),\n        evidence=[\n            evidence(\n                "pod",\n                InvestigationProbe.KUBERNETES_POD_STATE,\n            ),\n            evidence(\n                "working",\n                InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,\n            ),\n            evidence(\n                "limit",\n                InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,\n            ),\n        ],\n    )\n\n    decision = sufficient(\n        hypothesis_value=hypothesis(\n            cause="OOMKilled due to memory limit exceeded",\n            confidence=0.9,\n            supporting=[\n                "pod",\n                "working",\n                "limit",\n            ],\n            missing=[],\n        ),\n        evidence_ids=[\n            "pod",\n            "working",\n            "limit",\n        ],\n        root_cause="OOMKilled due to memory limit exceeded",\n        confidence=0.9,\n    )\n\n    result = (\n        EpistemicConclusionGuard()\n        .evaluate(\n            decision=decision,\n            state=state,\n        )\n    )\n\n    assert result.allowed is True\n    assert result.code is None\n\n\ndef test_prompt_distinguishes_symptom_from_specific_root_cause():\n    value = scope()\n\n    prompt = (\n        LLMInvestigationReasoner\n        ._build_prompt(\n            scope=value,\n            state=InvestigationState(\n                scope=value\n            ),\n        )\n    )\n\n    assert (\n        "symptom or failure-mode observation"\n        in prompt\n    )\n\n    assert (\n        "does not by itself establish the specific underlying cause"\n        in prompt\n    )\n\n    assert (\n        "Do not clear missing_evidence merely because all allowed probes have been attempted"\n        in prompt\n    )\n\n    assert (\n        "confirms a symptom/failure mode without establishing its cause"\n        in prompt\n    )\n\n\ndef test_missing_capability_awareness_does_not_use_guessed_cause_text():\n    scenario = BenchmarkScenario(\n        key="awareness-negative",\n        title="awareness-negative",\n        alert_name="PodRestartHigh",\n        alert_message="restart",\n        evidence_by_probe={},\n        hidden_expected_stop_reason=(\n            InvestigationStopReason.INSUFFICIENT_EVIDENCE\n        ),\n        hidden_missing_capability_keywords=[\n            "log",\n        ],\n    )\n\n    decision = InvestigationDecision(\n        hypotheses=[\n            hypothesis(\n                cause=(\n                    "CrashLoopBackOff due to application panic or misconfiguration"\n                ),\n                confidence=0.4,\n                missing=[\n                    "memory limit"\n                ],\n            )\n        ],\n        rationale_summary=(\n            "Application panic remains possible"\n        ),\n        stop=True,\n        stop_reason=(\n            InvestigationStopReason.INSUFFICIENT_EVIDENCE\n        ),\n    )\n\n    state = InvestigationState(\n        status=InvestigationStatus.CONCLUDED,\n        scope=scope(),\n        stop_reason=(\n            InvestigationStopReason.INSUFFICIENT_EVIDENCE\n        ),\n    )\n\n    score = score_scenario(\n        scenario=scenario,\n        state=state,\n        decisions=[\n            decision\n        ],\n    )\n\n    assert (\n        score.missing_capability_awareness\n        is False\n    )\n\n\ndef test_missing_capability_awareness_requires_explicit_missing_logs():\n    scenario = BenchmarkScenario(\n        key="awareness-positive",\n        title="awareness-positive",\n        alert_name="PodRestartHigh",\n        alert_message="restart",\n        evidence_by_probe={},\n        hidden_expected_stop_reason=(\n            InvestigationStopReason.INSUFFICIENT_EVIDENCE\n        ),\n        hidden_missing_capability_keywords=[\n            "log",\n        ],\n    )\n\n    decision = InvestigationDecision(\n        hypotheses=[\n            hypothesis(\n                cause=(\n                    "CrashLoopBackOff has an unresolved underlying cause"\n                ),\n                confidence=0.4,\n                missing=[\n                    "previous container logs"\n                ],\n            )\n        ],\n        rationale_summary=(\n            "Current bounded probes cannot identify the application failure."\n        ),\n        stop=True,\n        stop_reason=(\n            InvestigationStopReason.INSUFFICIENT_EVIDENCE\n        ),\n    )\n\n    state = InvestigationState(\n        status=InvestigationStatus.CONCLUDED,\n        scope=scope(),\n        stop_reason=(\n            InvestigationStopReason.INSUFFICIENT_EVIDENCE\n        ),\n    )\n\n    score = score_scenario(\n        scenario=scenario,\n        state=state,\n        decisions=[\n            decision\n        ],\n    )\n\n    assert (\n        score.missing_capability_awareness\n        is True\n    )\n\n\ndef test_crashloop_hidden_labels_no_longer_accept_application_word():\n    scenario = scenario_by_key(\n        "crashloop_not_memory"\n    )\n\n    normalized = {\n        item.lower()\n        for item\n        in scenario.hidden_missing_capability_keywords\n    }\n\n    assert "application" not in normalized\n    assert "应用" not in normalized\n    assert "log" in normalized\n'


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


def exact_replace(
    *,
    path: Path,
    old: str,
    new: str,
    label: str,
) -> None:
    text = read_text(
        path
    )

    ast.parse(
        text
    )

    count = text.count(
        old
    )

    if count != 1:
        raise RuntimeError(
            f"{label}: expected exactly one current patch anchor, found {count}"
        )

    updated = text.replace(
        old,
        new,
        1,
    )

    ast.parse(
        updated
    )

    write_text(
        path,
        updated,
    )


def validate_current_structure(
    *,
    guard_file: Path,
    reasoner_file: Path,
    engine_file: Path,
    scenarios_file: Path,
) -> list[str]:
    guard_text = read_text(
        guard_file
    )
    reasoner_text = read_text(
        reasoner_file
    )
    engine_text = read_text(
        engine_file
    )
    scenarios_text = read_text(
        scenarios_file
    )

    for text in (
        guard_text,
        reasoner_text,
        engine_text,
        scenarios_text,
    ):
        ast.parse(
            text
        )

    expected = {
        "epistemic_guard.py": (
            guard_text.count(
                GUARD_OLD
            )
        ),
        "reasoner.py": (
            reasoner_text.count(
                REASONER_OLD_BLOCK
            )
        ),
        "benchmark_engine_helper": (
            engine_text.count(
                ENGINE_OLD_HELPER
            )
        ),
        "benchmark_engine_call": (
            engine_text.count(
                ENGINE_OLD_CALL
            )
        ),
        "benchmark_crashloop_labels": (
            scenarios_text.count(
                SCENARIOS_OLD
            )
        ),
    }

    invalid = {
        name: count
        for name, count
        in expected.items()
        if count != 1
    }

    if invalid:
        raise RuntimeError(
            "Current structure changed; refusing stale patch: "
            + str(
                invalid
            )
        )

    return [
        f"{name}=current"
        for name
        in expected
    ]


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

    investigation_dir = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "investigation"
    )

    guard_file = (
        investigation_dir
        / "epistemic_guard.py"
    )

    reasoner_file = (
        investigation_dir
        / "reasoner.py"
    )

    benchmark_dir = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "evaluation"
        / "intelligence_benchmark"
    )

    engine_file = (
        benchmark_dir
        / "engine.py"
    )

    scenarios_file = (
        benchmark_dir
        / "scenarios.py"
    )

    test_file = (
        root
        / "services"
        / "agent_runtime"
        / "tests"
        / "test_investigation_causal_sufficiency.py"
    )

    required = (
        guard_file,
        reasoner_file,
        engine_file,
        scenarios_file,
    )

    for path in required:
        if not path.exists():
            raise RuntimeError(
                f"Required file is missing: {path}"
            )

    targets = (
        guard_file,
        reasoner_file,
        engine_file,
        scenarios_file,
        test_file,
    )

    preexisting = {
        path: path.exists()
        for path in targets
    }

    backups = []

    report = [
        "Investigation Causal Sufficiency v1",
        f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
        "",
        "Observed benchmark gap:",
        "- CrashLoopBackOff/restart evidence proved the failure mode",
        "- the model promoted that symptom into application panic/misconfiguration RCA",
        "- no causal/discriminating evidence established that specific cause",
        "",
        "Changes:",
        "- sufficient_evidence rejects a supported hypothesis that still declares missing_evidence",
        "- Reasoner distinguishes symptom/failure-mode evidence from root-cause evidence",
        "- exhausting all allowed probes does not justify clearing unresolved missing evidence",
        "- missing_capability_awareness uses explicit missing_evidence/remaining_uncertainties only",
        "- CrashLoop evaluator no longer treats the word 'application' as Logs awareness",
        "",
        "No domain-specific root cause is hard-coded.",
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
            "CURRENT FULL-FILE STRUCTURAL PREFLIGHT",
        )

        report.extend(
            validate_current_structure(
                guard_file=guard_file,
                reasoner_file=reasoner_file,
                engine_file=engine_file,
                scenarios_file=scenarios_file,
            )
        )

        exact_replace(
            path=guard_file,
            old=GUARD_OLD,
            new=GUARD_NEW,
            label="epistemic_guard.py",
        )

        exact_replace(
            path=reasoner_file,
            old=REASONER_OLD_BLOCK,
            new=REASONER_NEW_BLOCK,
            label="reasoner.py",
        )

        exact_replace(
            path=engine_file,
            old=ENGINE_OLD_HELPER,
            new=ENGINE_NEW_HELPER,
            label="benchmark engine helper",
        )

        exact_replace(
            path=engine_file,
            old=ENGINE_OLD_CALL,
            new=ENGINE_NEW_CALL,
            label="benchmark engine call",
        )

        exact_replace(
            path=scenarios_file,
            old=SCENARIOS_OLD,
            new=SCENARIOS_NEW,
            label="benchmark CrashLoop evaluator labels",
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
                    guard_file.relative_to(
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
                    scenarios_file.relative_to(
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
            name="Causal sufficiency focused regression tests",
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
                    "test_investigation_epistemic_guard.py"
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
                "Causal sufficiency regression tests failed"
            )

        prompt = run_command(
            root=root,
            name="Causal sufficiency prompt preflight",
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
                    "print('symptom_rule=' + "
                    "str('symptom or failure-mode observation' in p)); "
                    "print('root_cause_rule=' + "
                    "str('does not by itself establish the specific underlying cause' in p)); "
                    "print('missing_rule=' + "
                    "str('Do not clear missing_evidence merely because all allowed probes have been attempted' in p))"
                ),
            ],
        )

        add_command(
            report,
            prompt,
        )

        if prompt.returncode != 0:
            raise RuntimeError(
                "Causal sufficiency prompt preflight failed"
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
                "Causal sufficiency authority boundary failed"
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
                    guard_file.relative_to(
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
                    scenarios_file.relative_to(
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
                "Agent autonomy remains unchanged:",
                "- hypotheses remain model-owned",
                "- Probe choice remains model-owned",
                "- confidence remains model-owned",
                "- RCA/abstention remain model proposals",
                "",
                "Platform sufficiency boundary:",
                "- symptom confirmation alone is not enough for a specific root cause",
                "- sufficient-evidence hypothesis cannot still declare unresolved missing evidence",
                "- unsupported terminal RCA is downgraded safely by the existing Guard",
                "",
                "Benchmark integrity:",
                "- guessed cause text no longer counts as missing capability awareness",
                "",
                "Next:",
                "rerun the same three Bailian smoke scenarios.",
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
            "INVESTIGATION CAUSAL SUFFICIENCY V1 PASSED"
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
                    "Investigation Causal Sufficiency v1 FAILED",
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
            "INVESTIGATION CAUSAL SUFFICIENCY V1 FAILED"
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
