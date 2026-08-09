from __future__ import annotations

import hashlib
import shutil
import subprocess
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


VERSION = "investigation-decision-robustness-v1"

AFTER_NAME = (
    "investigation_decision_robustness_v1_after.txt"
)

ERROR_NAME = (
    "investigation_decision_robustness_v1_error.txt"
)

EXPECTED_HASHES = {'services/agent_runtime/app/investigation/reasoner.py': '766bc3f9070e51ff9654c46dc1e130ff4eaccc0fb8cf35563b5991e6700af2ea', 'services/agent_runtime/app/investigation/epistemic_guard.py': 'c1fa449af757eb0b00f9dc70068ff71d2e18f91f431405b35284da9384da1557'}

REASONER_SOURCE = 'import json\nfrom abc import ABC, abstractmethod\n\nfrom pydantic import ValidationError\n\nfrom services.agent_runtime.app.investigation.llm_gateway_adapter import (\n    BaseInvestigationLLM,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    InvestigationDecision,\n    InvestigationProbe,\n    InvestigationScope,\n    InvestigationState,\n)\n\n\nclass InvestigationReasonerError(RuntimeError):\n    """\n    Sanitized reasoner failure.\n    """\n\n\nclass InvestigationReasonerJSONError(\n    InvestigationReasonerError\n):\n    """\n    Primary decision was not valid JSON.\n    """\n\n\nclass InvestigationReasonerValidationError(\n    InvestigationReasonerError\n):\n    """\n    Primary JSON did not satisfy InvestigationDecision.\n    """\n\n\nclass InvestigationReasonerRepairJSONError(\n    InvestigationReasonerError\n):\n    """\n    One bounded repair attempt still did not return valid JSON.\n    """\n\n\nclass InvestigationReasonerRepairValidationError(\n    InvestigationReasonerError\n):\n    """\n    One bounded repair attempt still violated InvestigationDecision.\n    """\n\n\nclass BaseInvestigationReasoner(ABC):\n    """\n    Select the next symbolic read-only probe or stop with a conclusion.\n    """\n\n    @abstractmethod\n    async def decide(\n        self,\n        scope: InvestigationScope,\n        state: InvestigationState,\n    ) -> InvestigationDecision:\n        ...\n\n\nclass LLMInvestigationReasoner(\n    BaseInvestigationReasoner\n):\n    """\n    Structured LLM reasoner for the bounded InvestigationCoordinator.\n\n    The reasoner depends only on the Investigation-owned LLM abstraction.\n    Gateway routing, provider selection, fallback, rate limiting and circuit\n    breaking remain outside this class.\n\n    It can select only an InvestigationProbe enum value. It cannot construct\n    tool calls, resource scope, PromQL, URLs or credentials.\n    """\n\n    _SYSTEM_PROMPT = (\n        "You are a bounded SRE investigation reasoner. "\n        "Maintain competing hypotheses, use only supplied "\n        "evidence, and select only one allowed symbolic "\n        "read-only probe. Never propose or execute a write."\n    )\n\n    def __init__(\n        self,\n        investigation_llm: BaseInvestigationLLM,\n    ) -> None:\n        if not isinstance(\n            investigation_llm,\n            BaseInvestigationLLM,\n        ):\n            raise TypeError(\n                "Investigation LLM adapter is invalid"\n            )\n\n        self.investigation_llm = (\n            investigation_llm\n        )\n\n    async def decide(\n        self,\n        scope: InvestigationScope,\n        state: InvestigationState,\n    ) -> InvestigationDecision:\n        prompt = self._build_prompt(\n            scope=scope,\n            state=state,\n        )\n\n        content = await self.investigation_llm.complete(\n            system_prompt=self._SYSTEM_PROMPT,\n            prompt=prompt,\n        )\n\n        if not isinstance(\n            content,\n            str,\n        ):\n            raise InvestigationReasonerError(\n                "Investigation reasoner returned no JSON"\n            )\n\n        try:\n            return self._parse_decision(\n                content,\n                repair=False,\n            )\n\n        except (\n            InvestigationReasonerJSONError,\n            InvestigationReasonerValidationError,\n        ) as primary_error:\n            repair_content = await self.investigation_llm.complete(\n                system_prompt=(\n                    self._SYSTEM_PROMPT\n                    + " Repair the decision contract only; "\n                    "do not invent new evidence."\n                ),\n                prompt=self._build_repair_prompt(\n                    scope=scope,\n                    state=state,\n                    primary_error=primary_error,\n                ),\n            )\n\n            if not isinstance(\n                repair_content,\n                str,\n            ):\n                raise InvestigationReasonerError(\n                    "Investigation reasoner repair returned no JSON"\n                ) from primary_error\n\n            try:\n                return self._parse_decision(\n                    repair_content,\n                    repair=True,\n                )\n\n            except InvestigationReasonerError as repair_error:\n                raise repair_error from primary_error\n\n    @staticmethod\n    def _parse_decision(\n        content: str,\n        *,\n        repair: bool,\n    ) -> InvestigationDecision:\n        try:\n            payload = json.loads(\n                content\n            )\n\n        except json.JSONDecodeError as exc:\n            error_type = (\n                InvestigationReasonerRepairJSONError\n                if repair\n                else InvestigationReasonerJSONError\n            )\n\n            raise error_type(\n                (\n                    "Investigation reasoner repair returned invalid JSON"\n                    if repair\n                    else "Investigation reasoner returned invalid JSON"\n                )\n            ) from exc\n\n        try:\n            return InvestigationDecision.model_validate(\n                payload\n            )\n\n        except (\n            ValidationError,\n            TypeError,\n            ValueError,\n        ) as exc:\n            error_type = (\n                InvestigationReasonerRepairValidationError\n                if repair\n                else InvestigationReasonerValidationError\n            )\n\n            raise error_type(\n                (\n                    "Investigation reasoner repair returned an invalid decision"\n                    if repair\n                    else "Investigation reasoner returned an invalid decision"\n                )\n            ) from exc\n\n    @classmethod\n    def _build_repair_prompt(\n        cls,\n        *,\n        scope: InvestigationScope,\n        state: InvestigationState,\n        primary_error: InvestigationReasonerError,\n    ) -> str:\n        failure_kind = type(\n            primary_error\n        ).__name__\n\n        return (\n            "Your previous decision failed the bounded structured-output "\n            f"contract with failure type {failure_kind}.\\n"\n            "Do not repeat or explain the invalid response.\\n"\n            "Re-evaluate the SAME supplied state. Do not invent evidence, "\n            "do not add a tool call outside allowed_probes, and do not "\n            "change resource scope.\\n"\n            "Return exactly one corrected JSON decision that satisfies every "\n            "shape and evidence rule below.\\n\\n"\n            + cls._build_prompt(\n                scope=scope,\n                state=state,\n            )\n        )\n\n    @staticmethod\n    def _build_prompt(\n        scope: InvestigationScope,\n        state: InvestigationState,\n    ) -> str:\n        trusted_evidence_ids = [\n            item.evidence_id\n            for item in state.evidence\n            if (\n                item.success\n                and item.trusted\n                and item.production_signal\n            )\n        ]\n\n        state_payload = {\n            "scope": scope.model_dump(\n                mode="json"\n            ),\n            "iteration_count": state.iteration_count,\n            "tool_call_count": state.tool_call_count,\n            "attempted_probes": [\n                probe.value\n                for probe in state.attempted_probes\n            ],\n            "hypotheses": [\n                item.model_dump(mode="json")\n                for item in state.hypotheses\n            ],\n            "evidence": [\n                item.model_dump(mode="json")\n                for item in state.evidence\n            ],\n            "trusted_evidence_ids": trusted_evidence_ids,\n            "allowed_probes": [\n                probe.value\n                for probe in InvestigationProbe\n            ],\n        }\n\n        return (\n            "Investigate the incident using the bounded state below.\\n"\n            "Return exactly one JSON object only. Do not return markdown.\\n"\n            "Maintain competing hypotheses and update confidence from evidence.\\n"\n            "Probe affordances:\\n"\n            "- kubernetes_pod_state: current pod/container state, restart "\n            "indicators, and last termination reasons.\\n"\n            "- kubernetes_previous_container_logs: bounded previous-container "\n            "output; high-information evidence for unexplained restart, startup, "\n            "panic, configuration, dependency, or crash symptoms.\\n"\n            "- prometheus_memory_working_set: sampled container memory usage.\\n"\n            "- prometheus_memory_limit: configured container memory limit.\\n"\n            "- prometheus_restart_count: sampled restart frequency/corroboration.\\n"\n            "If trusted evidence falsifies the current leading hypothesis but "\n            "the observed incident symptom remains unexplained, do not stop "\n            "solely because that hypothesis was rejected. Replan with at least "\n            "one evidence-plausible alternative hypothesis when an unattempted "\n            "allowed probe can materially discriminate plausible causes.\\n"\n            "Use insufficient_evidence only when no unattempted safe probe can "\n            "materially discriminate the remaining plausible causes, or when "\n            "required evidence is unavailable.\\n"\n            "Never repeat a probe already listed in attempted_probes.\\n"\n            "Never cite an evidence ID that is absent from State.evidence.\\n"\n            "Conflicting evidence can weaken a hypothesis but cannot by itself "\n            "positively establish an unrelated root cause.\\n"\n            "Ruling out one hypothesis is not sufficient evidence for a different "\n            "causal claim.\\n"\n            "Current-state evidence does not by itself prove that a historical "\n            "event never occurred.\\n"\n            "For sufficient_evidence, the conclusion must affirm a positively "\n            "supported current hypothesis. Every conclusion.evidence_id must also "\n            "appear in that hypothesis supporting_evidence_ids and must not be "\n            "conflicting evidence for that same hypothesis.\\n"\n            "Conclusion confidence must not materially exceed the confidence of "\n            "the positively supported hypothesis.\\n"\n            "A symptom or failure-mode observation such as CrashLoopBackOff, "\n            "restart count, unready state, high latency, or high error rate can "\n            "confirm that a failure exists, but does not by itself establish the "\n            "specific underlying cause that produced it.\\n"\n            "If several underlying causes remain plausible and current allowed "\n            "probes cannot discriminate among them, keep the required "\n            "root-cause evidence in hypothesis.missing_evidence and stop with "\n            "insufficient_evidence or no_safe_probe.\\n"\n            "Use hypothesis.missing_evidence only for evidence that is REQUIRED "\n            "before the specific root cause can be accepted. Use "\n            "hypothesis.optional_evidence for corroboration that may increase "\n            "confidence or describe frequency/severity but is not required to "\n            "establish the root cause.\\n"\n            "Do not put the same evidence need in both missing_evidence and "\n            "optional_evidence.\\n"\n            "Do not clear missing_evidence merely because all allowed probes "\n            "have been attempted. For sufficient_evidence, the positively "\n            "supported hypothesis used by the conclusion must have an empty "\n            "missing_evidence list. optional_evidence may remain non-empty.\\n"\n            "Treat event evidence separately from mechanism evidence. For example, "\n            "OOMKilled proves that an OOM termination occurred, but does not by "\n            "itself prove that a configured container memory limit was exceeded.\\n"\n            "A point-in-time or sampled metric cannot establish an unobserved "\n            "transient peak, historical trend, or threshold crossing. Never invent "\n            "an unseen spike to make a hypothesis fit.\\n"\n            "For quantitative threshold causes, supporting evidence must be "\n            "directionally consistent with the claimed mechanism. If a sampled "\n            "working value is below the sampled limit, that sample is not positive "\n            "support for the claim that the limit was exceeded.\\n"\n            "If an event is confirmed but the available sampled metrics do not "\n            "explain its mechanism, keep the required historical/range/peak "\n            "evidence in missing_evidence and stop with insufficient_evidence "\n            "unless another direct causal observation establishes the cause.\\n"\n            "If the available evidence only rejects hypotheses and does not "\n            "positively establish a root cause, stop with insufficient_evidence.\\n"\n            "If the available evidence only rejects hypotheses or confirms a "\n            "symptom/failure mode without establishing its cause, stop with "\n            "insufficient_evidence.\\n"\n            "For a conclusion, evidence_ids must be non-empty and copied "\n            "exactly from trusted_evidence_ids.\\n"\n            "Allowed model terminal stop_reason values are exactly: "\n            "sufficient_evidence, insufficient_evidence, no_safe_probe.\\n"\n            "Do not emit internal coordinator reasons such as timeout, "\n            "max_iterations, max_tool_calls, duplicate_probe or reasoner_error.\\n"\n            "\\n"\n            "Continuing shape:\\n"\n            "{\\n"\n            \'  "hypotheses": [{"hypothesis_id": "h1", "cause": "candidate", \'\n            \'"confidence": 0.5, "supporting_evidence_ids": [], \'\n            \'"conflicting_evidence_ids": [], "missing_evidence": ["required evidence"], "optional_evidence": ["non-blocking corroboration"]}],\\n\'\n            \'  "rationale_summary": "why this probe is most discriminative",\\n\'\n            \'  "stop": false,\\n\'\n            \'  "stop_reason": null,\\n\'\n            \'  "next_probe": "one unattempted value from allowed_probes",\\n\'\n            \'  "conclusion": null\\n\'\n            "}\\n"\n            "\\n"\n            "Terminal sufficient-evidence shape:\\n"\n            "{\\n"\n            \'  "hypotheses": [{"hypothesis_id": "h1", "cause": "supported cause", \'\n            \'"confidence": 0.9, "supporting_evidence_ids": ["copy exact trusted id"], \'\n            \'"conflicting_evidence_ids": [], "missing_evidence": [], "optional_evidence": []}],\\n\'\n            \'  "rationale_summary": "why trusted evidence is sufficient",\\n\'\n            \'  "stop": true,\\n\'\n            \'  "stop_reason": "sufficient_evidence",\\n\'\n            \'  "next_probe": null,\\n\'\n            \'  "conclusion": {"root_cause": "bounded root cause", \'\n            \'"confidence": 0.9, "evidence_ids": ["copy exact trusted id"], \'\n            \'"remaining_uncertainties": []}\\n\'\n            "}\\n"\n            "\\n"\n            "Terminal insufficient/no-safe-probe shape:\\n"\n            "{\\n"\n            \'  "hypotheses": [{"hypothesis_id": "h1", "cause": "candidate", \'\n            \'"confidence": 0.3, "supporting_evidence_ids": [], \'\n            \'"conflicting_evidence_ids": [], "missing_evidence": ["required missing evidence"], "optional_evidence": ["non-blocking evidence if useful"]}],\\n\'\n            \'  "rationale_summary": "why current bounded evidence cannot support an RCA",\\n\'\n            \'  "stop": true,\\n\'\n            \'  "stop_reason": "insufficient_evidence",\\n\'\n            \'  "next_probe": null,\\n\'\n            \'  "conclusion": null\\n\'\n            "}\\n"\n            "\\n"\n            "If no useful unattempted allowed probe can resolve the remaining "\n            "uncertainty, stop with insufficient_evidence or no_safe_probe.\\n"\n            "State:\\n"\n            f"{json.dumps(state_payload, ensure_ascii=True, sort_keys=True)}"\n        )\n\n\n__all__ = [\n    "BaseInvestigationReasoner",\n    "InvestigationReasonerError",\n    "LLMInvestigationReasoner",\n]\n'
GUARD_SOURCE = 'from __future__ import annotations\n\nfrom dataclasses import dataclass\nimport re\n\nfrom services.agent_runtime.app.investigation.models import (\n    InvestigationDecision,\n    InvestigationState,\n    InvestigationStopReason,\n)\n\n\n@dataclass(\n    frozen=True,\n    slots=True,\n)\nclass EpistemicGuardResult:\n    """\n    Result of one structural conclusion-admissibility check.\n\n    The guard does not invent, rewrite or semantically classify a root cause.\n    It only checks whether a sufficient-evidence conclusion is backed by\n    positive support declared on at least one current hypothesis.\n    """\n\n    allowed: bool\n    code: str | None = None\n\n\nclass EpistemicConclusionGuard:\n    """\n    Fail-safe evidence-discipline guard for terminal RCA decisions.\n\n    This guard intentionally does NOT:\n    - infer a root cause;\n    - inspect domain-specific keywords;\n    - decide whether an alert is a false positive;\n    - replace the Investigation reasoner.\n\n    It only enforces generic epistemic invariants for\n    stop_reason=sufficient_evidence:\n\n    1. at least one current hypothesis has positive supporting evidence;\n    2. every conclusion evidence ID is positive support for one hypothesis;\n    3. conclusion evidence is not conflicting evidence for that hypothesis;\n    4. the supporting hypothesis has a minimum confidence;\n    5. the positively supported hypothesis used for the conclusion has no\n       unresolved root-cause-critical missing_evidence;\n       optional_evidence is explicitly non-blocking corroboration;\n    6. conclusion confidence may not materially exceed that hypothesis.\n\n    If these invariants are not met, the Coordinator may safely downgrade the\n    decision to insufficient_evidence instead of accepting an unsupported RCA.\n    """\n\n    def __init__(\n        self,\n        *,\n        min_supported_confidence: float = 0.5,\n        max_conclusion_confidence_delta: float = 0.05,\n        min_memory_limit_pressure_ratio: float = 0.90,\n    ) -> None:\n        if not (\n            0.0\n            <= min_supported_confidence\n            <= 1.0\n        ):\n            raise ValueError(\n                "min_supported_confidence must be within [0,1]"\n            )\n\n        if not (\n            0.0\n            <= max_conclusion_confidence_delta\n            <= 1.0\n        ):\n            raise ValueError(\n                "max_conclusion_confidence_delta must be within [0,1]"\n            )\n\n        if not (\n            0.0\n            <= min_memory_limit_pressure_ratio\n            <= 1.0\n        ):\n            raise ValueError(\n                "min_memory_limit_pressure_ratio must be within [0,1]"\n            )\n\n        self.min_supported_confidence = (\n            min_supported_confidence\n        )\n\n        self.max_conclusion_confidence_delta = (\n            max_conclusion_confidence_delta\n        )\n\n        self.min_memory_limit_pressure_ratio = (\n            min_memory_limit_pressure_ratio\n        )\n\n    def evaluate(\n        self,\n        *,\n        decision: InvestigationDecision,\n        state: InvestigationState,\n    ) -> EpistemicGuardResult:\n        if not isinstance(\n            decision,\n            InvestigationDecision,\n        ):\n            raise TypeError(\n                "Epistemic guard decision is invalid"\n            )\n\n        if not isinstance(\n            state,\n            InvestigationState,\n        ):\n            raise TypeError(\n                "Epistemic guard state is invalid"\n            )\n\n        if (\n            not decision.stop\n            or decision.stop_reason\n            != InvestigationStopReason.SUFFICIENT_EVIDENCE\n        ):\n            return EpistemicGuardResult(\n                allowed=True\n            )\n\n        conclusion = (\n            decision.conclusion\n        )\n\n        if conclusion is None:\n            return EpistemicGuardResult(\n                allowed=False,\n                code="MissingConclusion",\n            )\n\n        conclusion_ids = set(\n            conclusion.evidence_ids\n        )\n\n        if not conclusion_ids:\n            return EpistemicGuardResult(\n                allowed=False,\n                code="MissingConclusionEvidence",\n            )\n\n        positively_supported = [\n            hypothesis\n            for hypothesis\n            in decision.hypotheses\n            if hypothesis.supporting_evidence_ids\n        ]\n\n        if not positively_supported:\n            return EpistemicGuardResult(\n                allowed=False,\n                code="NoPositiveHypothesisSupport",\n            )\n\n        support_compatible = []\n\n        for hypothesis in positively_supported:\n            supporting_ids = set(\n                hypothesis.supporting_evidence_ids\n            )\n\n            conflicting_ids = set(\n                hypothesis.conflicting_evidence_ids\n            )\n\n            if not conclusion_ids.issubset(\n                supporting_ids\n            ):\n                continue\n\n            if conclusion_ids.intersection(\n                conflicting_ids\n            ):\n                continue\n\n            support_compatible.append(\n                hypothesis\n            )\n\n        if not support_compatible:\n            return EpistemicGuardResult(\n                allowed=False,\n                code="ConclusionEvidenceNotPositiveSupport",\n            )\n\n        # Only root-cause-blocking missing_evidence prevents a terminal RCA.\n        # optional_evidence is deliberately ignored here: it is corroboration,\n        # not a prerequisite for accepting the supported cause.\n        causally_complete = [\n            hypothesis\n            for hypothesis\n            in support_compatible\n            if not hypothesis.missing_evidence\n        ]\n\n        if not causally_complete:\n            return EpistemicGuardResult(\n                allowed=False,\n                code="SupportedHypothesisStillMissingEvidence",\n            )\n\n        quantitatively_consistent = [\n            hypothesis\n            for hypothesis\n            in causally_complete\n            if self._memory_limit_support_is_consistent(\n                hypothesis=hypothesis,\n                conclusion=conclusion,\n                state=state,\n            )\n        ]\n\n        if not quantitatively_consistent:\n            return EpistemicGuardResult(\n                allowed=False,\n                code="MemoryLimitEvidenceNotNearThreshold",\n            )\n\n        confidence_compatible = [\n            hypothesis\n            for hypothesis\n            in quantitatively_consistent\n            if (\n                hypothesis.confidence\n                >= self.min_supported_confidence\n            )\n        ]\n\n        if not confidence_compatible:\n            return EpistemicGuardResult(\n                allowed=False,\n                code="SupportedHypothesisConfidenceTooLow",\n            )\n\n        for hypothesis in confidence_compatible:\n            permitted = min(\n                1.0,\n                (\n                    hypothesis.confidence\n                    + self.max_conclusion_confidence_delta\n                ),\n            )\n\n            if (\n                conclusion.confidence\n                <= permitted\n            ):\n                return EpistemicGuardResult(\n                    allowed=True\n                )\n\n        return EpistemicGuardResult(\n            allowed=False,\n            code="ConclusionConfidenceExceedsSupport",\n        )\n\n    def _memory_limit_support_is_consistent(\n        self,\n        *,\n        hypothesis,\n        conclusion,\n        state: InvestigationState,\n    ) -> bool:\n        """\n        Deterministic consistency policy for one explicit threshold mechanism.\n\n        This does not infer a memory RCA. It only checks a model-proposed\n        positive claim that a container memory limit was exceeded. When the\n        decision cites both sampled working-set and limit evidence as positive\n        support, the sampled working set must at least be near the configured\n        limit. A far-below-limit sample cannot be called positive support for\n        "limit exceeded", even if an OOM event itself is confirmed.\n        """\n\n        claim_text = (\n            hypothesis.cause\n            + " "\n            + conclusion.root_cause\n        ).strip().lower()\n\n        negative_patterns = (\n            "not exceeded",\n            "did not exceed",\n            "below the limit",\n            "within the limit",\n        )\n\n        if any(\n            pattern in claim_text\n            for pattern in negative_patterns\n        ):\n            return True\n\n        claims_limit_exceeded = (\n            "memory" in claim_text\n            and "limit" in claim_text\n            and bool(\n                re.search(\n                    r"\\b(exceed(?:ed|s|ing)?|exhaust(?:ed|ion)?|too\\s+low)\\b",\n                    claim_text,\n                )\n            )\n        )\n\n        if not claims_limit_exceeded:\n            return True\n\n        support_ids = set(\n            hypothesis.supporting_evidence_ids\n        ).intersection(\n            conclusion.evidence_ids\n        )\n\n        working = None\n        limit = None\n\n        for item in state.evidence:\n            if item.evidence_id not in support_ids:\n                continue\n\n            if (\n                item.probe.value\n                == "prometheus_memory_working_set"\n            ):\n                working = self._numeric_evidence_value(\n                    item.facts\n                )\n\n            elif (\n                item.probe.value\n                == "prometheus_memory_limit"\n            ):\n                limit = self._numeric_evidence_value(\n                    item.facts\n                )\n\n        if (\n            working is None\n            or limit is None\n            or limit <= 0.0\n        ):\n            return True\n\n        ratio = (\n            working\n            / limit\n        )\n\n        return (\n            ratio\n            >= self.min_memory_limit_pressure_ratio\n        )\n\n    @staticmethod\n    def _numeric_evidence_value(\n        facts,\n    ) -> float | None:\n        for key in (\n            "value_max",\n            "value_sum",\n        ):\n            value = facts.get(\n                key\n            )\n\n            if isinstance(\n                value,\n                (\n                    int,\n                    float,\n                ),\n            ) and not isinstance(\n                value,\n                bool,\n            ):\n                return float(\n                    value\n                )\n\n        return None\n\n\n__all__ = [\n    "EpistemicConclusionGuard",\n    "EpistemicGuardResult",\n]\n'
TEST_SOURCE = 'from __future__ import annotations\n\nimport json\nfrom datetime import UTC, datetime\n\nimport pytest\n\nfrom services.agent_runtime.app.investigation.epistemic_guard import (\n    EpistemicConclusionGuard,\n)\nfrom services.agent_runtime.app.investigation.llm_gateway_adapter import (\n    BaseInvestigationLLM,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    EvidenceItem,\n    IncidentHypothesis,\n    InvestigationConclusion,\n    InvestigationDecision,\n    InvestigationProbe,\n    InvestigationScope,\n    InvestigationState,\n    InvestigationStopReason,\n)\nfrom services.agent_runtime.app.investigation.reasoner import (\n    InvestigationReasonerRepairValidationError,\n    LLMInvestigationReasoner,\n)\n\n\nNOW = datetime(\n    2026,\n    8,\n    10,\n    14,\n    15,\n    tzinfo=UTC,\n)\n\n\nclass SequenceInvestigationLLM(\n    BaseInvestigationLLM\n):\n    def __init__(\n        self,\n        responses,\n    ) -> None:\n        self.responses = list(\n            responses\n        )\n        self.calls = []\n\n    async def complete(\n        self,\n        *,\n        system_prompt: str,\n        prompt: str,\n    ) -> str:\n        self.calls.append(\n            {\n                "system_prompt": system_prompt,\n                "prompt": prompt,\n            }\n        )\n\n        return self.responses.pop(\n            0\n        )\n\n\ndef scope() -> InvestigationScope:\n    return InvestigationScope(\n        alert_name="PodRestartHigh",\n        alert_message="payment-api restarts are increasing",\n        resource="payment-api",\n        namespace="payment",\n        cluster="benchmark-lab",\n    )\n\n\ndef state() -> InvestigationState:\n    return InvestigationState(\n        scope=scope()\n    )\n\n\ndef valid_probe_decision() -> str:\n    return json.dumps(\n        {\n            "hypotheses": [\n                {\n                    "hypothesis_id": "h1",\n                    "cause": "unresolved startup failure",\n                    "confidence": 0.4,\n                    "supporting_evidence_ids": [],\n                    "conflicting_evidence_ids": [],\n                    "missing_evidence": [\n                        "previous container logs"\n                    ],\n                    "optional_evidence": [\n                        "restart count"\n                    ],\n                }\n            ],\n            "rationale_summary": (\n                "previous container logs are the most discriminative next probe"\n            ),\n            "stop": False,\n            "stop_reason": None,\n            "next_probe": (\n                "kubernetes_previous_container_logs"\n            ),\n            "conclusion": None,\n        }\n    )\n\n\n@pytest.mark.asyncio\nasync def test_reasoner_repairs_one_invalid_decision_without_new_evidence():\n    llm = SequenceInvestigationLLM(\n        [\n            json.dumps(\n                {\n                    "hypotheses": [\n                        {\n                            "hypothesis_id": "bad",\n                            "cause": "attempt a write",\n                            "confidence": 0.9,\n                        }\n                    ],\n                    "rationale_summary": "bad probe",\n                    "stop": False,\n                    "next_probe": "kubernetes_patch",\n                }\n            ),\n            valid_probe_decision(),\n        ]\n    )\n\n    reasoner = LLMInvestigationReasoner(\n        llm\n    )\n\n    current = state()\n\n    decision = await reasoner.decide(\n        current.scope,\n        current,\n    )\n\n    assert (\n        decision.next_probe\n        == InvestigationProbe.KUBERNETES_PREVIOUS_CONTAINER_LOGS\n    )\n\n    assert len(\n        llm.calls\n    ) == 2\n\n    repair_call = llm.calls[\n        1\n    ]\n\n    assert (\n        "Repair the decision contract only"\n        in repair_call[\n            "system_prompt"\n        ]\n    )\n\n    assert (\n        "Re-evaluate the SAME supplied state"\n        in repair_call[\n            "prompt"\n        ]\n    )\n\n    assert (\n        "kubernetes_patch"\n        not in repair_call[\n            "prompt"\n        ]\n    )\n\n\n@pytest.mark.asyncio\nasync def test_reasoner_repair_still_fails_closed_after_one_retry():\n    invalid = json.dumps(\n        {\n            "hypotheses": [\n                {\n                    "hypothesis_id": "bad",\n                    "cause": "attempt a write",\n                    "confidence": 0.9,\n                }\n            ],\n            "rationale_summary": "bad probe",\n            "stop": False,\n            "next_probe": "kubernetes_patch",\n        }\n    )\n\n    llm = SequenceInvestigationLLM(\n        [\n            invalid,\n            invalid,\n        ]\n    )\n\n    reasoner = LLMInvestigationReasoner(\n        llm\n    )\n\n    current = state()\n\n    with pytest.raises(\n        InvestigationReasonerRepairValidationError,\n    ):\n        await reasoner.decide(\n            current.scope,\n            current,\n        )\n\n    assert len(\n        llm.calls\n    ) == 2\n\n\ndef test_reasoner_prompt_contains_probe_affordances_and_replan_rule():\n    current = state()\n\n    prompt = (\n        LLMInvestigationReasoner\n        ._build_prompt(\n            scope=current.scope,\n            state=current,\n        )\n    )\n\n    assert (\n        "Probe affordances:"\n        in prompt\n    )\n\n    assert (\n        "kubernetes_previous_container_logs"\n        in prompt\n    )\n\n    assert (\n        "If trusted evidence falsifies the current leading hypothesis"\n        in prompt\n    )\n\n    assert (\n        "Replan with at least one evidence-plausible alternative hypothesis"\n        in prompt\n    )\n\n\ndef trusted(\n    evidence_id: str,\n    probe: InvestigationProbe,\n    value: float | None = None,\n    *,\n    oom_killed: bool | None = None,\n) -> EvidenceItem:\n    facts = {}\n\n    if value is not None:\n        facts[\n            "value_sum"\n        ] = value\n\n    if oom_killed is not None:\n        facts[\n            "oom_killed"\n        ] = oom_killed\n\n    return EvidenceItem(\n        evidence_id=evidence_id,\n        probe=probe,\n        source=(\n            "kubernetes"\n            if probe\n            == InvestigationProbe.KUBERNETES_POD_STATE\n            else "prometheus"\n        ),\n        success=True,\n        trusted=True,\n        production_signal=True,\n        reliability=1.0,\n        observed_at=NOW,\n        facts=facts,\n    )\n\n\ndef memory_limit_decision(\n    *,\n    working: str,\n    limit: str,\n) -> InvestigationDecision:\n    return InvestigationDecision(\n        hypotheses=[\n            IncidentHypothesis(\n                hypothesis_id="h1",\n                cause=(\n                    "container exceeded memory limit causing OOMKilled"\n                ),\n                confidence=0.9,\n                supporting_evidence_ids=[\n                    "pod",\n                    working,\n                    limit,\n                ],\n                conflicting_evidence_ids=[],\n                missing_evidence=[],\n                optional_evidence=[],\n            )\n        ],\n        rationale_summary=(\n            "memory evidence supports the proposed threshold mechanism"\n        ),\n        stop=True,\n        stop_reason=(\n            InvestigationStopReason.SUFFICIENT_EVIDENCE\n        ),\n        conclusion=InvestigationConclusion(\n            root_cause=(\n                "container exceeded memory limit causing OOMKilled"\n            ),\n            confidence=0.9,\n            evidence_ids=[\n                "pod",\n                working,\n                limit,\n            ],\n        ),\n    )\n\n\ndef test_guard_allows_near_limit_oom_support():\n    current = InvestigationState(\n        scope=scope(),\n        evidence=[\n            trusted(\n                "pod",\n                InvestigationProbe.KUBERNETES_POD_STATE,\n                oom_killed=True,\n            ),\n            trusted(\n                "working",\n                InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,\n                530_000_000.0,\n            ),\n            trusted(\n                "limit",\n                InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,\n                536_870_912.0,\n            ),\n        ],\n    )\n\n    result = (\n        EpistemicConclusionGuard()\n        .evaluate(\n            decision=memory_limit_decision(\n                working="working",\n                limit="limit",\n            ),\n            state=current,\n        )\n    )\n\n    assert result.allowed is True\n    assert result.code is None\n\n\ndef test_guard_rejects_far_below_limit_sample_as_positive_limit_support():\n    current = InvestigationState(\n        scope=scope(),\n        evidence=[\n            trusted(\n                "pod",\n                InvestigationProbe.KUBERNETES_POD_STATE,\n                oom_killed=True,\n            ),\n            trusted(\n                "working",\n                InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,\n                300_000_000.0,\n            ),\n            trusted(\n                "limit",\n                InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,\n                1_073_741_824.0,\n            ),\n        ],\n    )\n\n    result = (\n        EpistemicConclusionGuard()\n        .evaluate(\n            decision=memory_limit_decision(\n                working="working",\n                limit="limit",\n            ),\n            state=current,\n        )\n    )\n\n    assert result.allowed is False\n    assert (\n        result.code\n        == "MemoryLimitEvidenceNotNearThreshold"\n    )\n'


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
        .replace(
            "\r\n",
            "\n",
        )
        .replace(
            "\r",
            "\n",
        )
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

    expected = (
        EXPECTED_HASHES[
            relative
        ]
    )

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

    reasoner_file = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "investigation"
        / "reasoner.py"
    )

    guard_file = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "investigation"
        / "epistemic_guard.py"
    )

    test_file = (
        root
        / "services"
        / "agent_runtime"
        / "tests"
        / "test_investigation_decision_robustness.py"
    )

    sources = {
        reasoner_file: REASONER_SOURCE,
        guard_file: GUARD_SOURCE,
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
        "Investigation Decision Robustness v1",
        f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
        "",
        "Observed Full Benchmark failures addressed:",
        "- reasoner_error remained after benchmark wall-clock separation",
        "- CrashLoop Logs RCA scenario stopped after falsifying only the OOM hypothesis",
        "- OOM + far-below-limit sampled memory still became a memory-limit-exceeded RCA",
        "",
        "Decision contract:",
        "- valid first decision still uses exactly one LLM call",
        "- invalid JSON/decision gets exactly one bounded repair call",
        "- repair reuses the same InvestigationState and adds no Tool call",
        "- invalid model output is never echoed into errors or repair prompt",
        "- second invalid decision still fails closed",
        "- final repair failure types are stage-specific",
        "",
        "Planning:",
        "- Reasoner now receives explicit symbolic Probe affordances",
        "- falsifying one hypothesis does not justify terminal stop while the incident symptom remains unexplained and a discriminative unattempted Probe exists",
        "",
        "Evidence consistency:",
        "- explicit positive memory-limit-exceeded claims are checked against their own cited working-set and limit evidence",
        "- a far-below-limit sampled value cannot be positive support for limit exceeded",
        "- near-limit evidence remains admissible when other positive evidence supports the OOM mechanism",
        "",
        "No new Tool, Action, Approval, Verification, Kubernetes write, PromQL or credential authority is added.",
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
                (
                    relative
                    + "="
                    + EXPECTED_HASHES[
                        relative
                    ]
                )
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
            name="Decision Robustness focused regression suite",
            command=[
                "uv",
                "run",
                "pytest",
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
                    "test_investigation_terminal_contract.py"
                ),
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
                "-q",
            ],
        )

        add_command(
            report,
            focused,
        )

        if focused.returncode != 0:
            raise RuntimeError(
                "Decision Robustness focused tests failed"
            )

        integration = run_command(
            root=root,
            name="Investigation evaluation/log compatibility suite",
            command=[
                "uv",
                "run",
                "pytest",
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_intelligence_benchmark.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_logs.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_logs_benchmark.py"
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
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_evaluation_matrix.py"
                ),
                "-q",
            ],
        )

        add_command(
            report,
            integration,
        )

        if integration.returncode != 0:
            raise RuntimeError(
                "Investigation evaluation/log compatibility tests failed"
            )

        prompt = run_command(
            root=root,
            name="Decision robustness prompt preflight",
            command=[
                "uv",
                "run",
                "python",
                "-c",
                (
                    "from services.agent_runtime.app.investigation.models "
                    "import InvestigationScope,InvestigationState; "
                    "from services.agent_runtime.app.investigation.reasoner "
                    "import LLMInvestigationReasoner; "
                    "s=InvestigationScope(alert_name='A',resource='r'); "
                    "p=LLMInvestigationReasoner._build_prompt("
                    "scope=s,state=InvestigationState(scope=s)); "
                    "print('affordances='+str('Probe affordances:' in p)); "
                    "print('replan='+str('Replan with at least one evidence-plausible alternative hypothesis' in p)); "
                    "print('logs='+str('kubernetes_previous_container_logs' in p)); "
                    "assert 'Probe affordances:' in p; "
                    "assert 'Replan with at least one evidence-plausible alternative hypothesis' in p"
                ),
            ],
        )

        add_command(
            report,
            prompt,
        )

        if prompt.returncode != 0:
            raise RuntimeError(
                "Decision robustness prompt preflight failed"
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
                    "files=["
                    "Path(r'services/agent_runtime/app/investigation/reasoner.py'),"
                    "Path(r'services/agent_runtime/app/investigation/epistemic_guard.py')"
                    "]; "
                    "s='\\n'.join(x.read_text(encoding='utf-8') for x in files); "
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
                "Decision Robustness authority boundary failed"
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
                "Decision Robustness v1 is installed.",
                "",
                "Expected behavioral changes:",
                "- transient malformed/invalid structured decisions can recover once without any new Tool call",
                "- persistent invalid decisions still fail closed with a more precise failure_code",
                "- CrashLoop after OOM falsification should replan toward previous-container logs when that probe remains available",
                "- a far-below-limit sample cannot support a positive memory-limit-exceeded RCA",
                "",
                "Next acceptance:",
                "run the four previously failing scenarios individually before another Full benchmark.",
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
            "INVESTIGATION DECISION ROBUSTNESS V1 PASSED"
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
                    "Investigation Decision Robustness v1 FAILED",
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
            "INVESTIGATION DECISION ROBUSTNESS V1 FAILED"
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
