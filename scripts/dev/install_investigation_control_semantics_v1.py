from __future__ import annotations

import hashlib
import shutil
import subprocess
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


VERSION = "investigation-control-semantics-v1"

AFTER_NAME = (
    "investigation_control_semantics_v1_after.txt"
)

ERROR_NAME = (
    "investigation_control_semantics_v1_error.txt"
)

EXPECTED_HASHES = {'services/agent_runtime/app/investigation/models.py': '356b7cfaa832316951abb893f27134f7ee73cc61fe7c24cf93888d50dd4b6611', 'services/agent_runtime/app/investigation/reasoner.py': '33012e270fdad7b503b640b15836c0a70bea3f489d8a71cf3ca32047355f5248', 'services/agent_runtime/app/investigation/epistemic_guard.py': '0de8bdc7aeaab5b3b96e3bad729540aedf6d8b857dfbb4e12d1f51fe150594f9', 'services/agent_runtime/app/evaluation/intelligence_benchmark/engine.py': '7750791ab8b1152eb853ba89f3b0e7c8953d7e5892f9f4340e948659f10393ed', 'scripts/dev/run_investigation_intelligence_benchmark_v1.py': 'd8cffd0dc6d6087800eb0193b1fc11f12d33f94fb297f3c4cc705eefacb460e5'}

MODELS_SOURCE = 'from datetime import UTC, datetime\nfrom enum import Enum\nfrom typing import Annotated, Literal\nfrom uuid import uuid4\n\nfrom pydantic import (\n    BaseModel,\n    ConfigDict,\n    Field,\n    StringConstraints,\n    model_validator,\n)\n\n\nShortText = Annotated[\n    str,\n    StringConstraints(\n        strip_whitespace=True,\n        min_length=1,\n        max_length=256,\n    ),\n]\n\nLongText = Annotated[\n    str,\n    StringConstraints(\n        strip_whitespace=True,\n        min_length=1,\n        max_length=2000,\n    ),\n]\n\nEvidenceScalar = bool | int | float | str | None\n\n\nclass InvestigationProbe(str, Enum):\n    """\n    Closed set of read-only probes selectable by a reasoner.\n\n    The reasoner selects only a symbolic probe. It never supplies a tool\n    name, Kubernetes verb, resource target, URL, credential or PromQL.\n    """\n\n    KUBERNETES_POD_STATE = "kubernetes_pod_state"\n    KUBERNETES_PREVIOUS_CONTAINER_LOGS = (\n        "kubernetes_previous_container_logs"\n    )\n    PROMETHEUS_MEMORY_WORKING_SET = (\n        "prometheus_memory_working_set"\n    )\n    PROMETHEUS_MEMORY_LIMIT = "prometheus_memory_limit"\n    PROMETHEUS_RESTART_COUNT = "prometheus_restart_count"\n\n\nclass InvestigationStatus(str, Enum):\n    PENDING = "pending"\n    RUNNING = "running"\n    CONCLUDED = "concluded"\n    EXHAUSTED = "exhausted"\n    FAILED = "failed"\n\n\nclass InvestigationStopReason(str, Enum):\n    SUFFICIENT_EVIDENCE = "sufficient_evidence"\n    INSUFFICIENT_EVIDENCE = "insufficient_evidence"\n    MAX_ITERATIONS = "max_iterations"\n    MAX_TOOL_CALLS = "max_tool_calls"\n    TIMEOUT = "timeout"\n    DUPLICATE_PROBE = "duplicate_probe"\n    NO_SAFE_PROBE = "no_safe_probe"\n    REASONER_ERROR = "reasoner_error"\n    INVALID_SCOPE = "invalid_scope"\n\n\nclass InvestigationLimits(BaseModel):\n    """\n    Hard execution limits for one read-only investigation.\n    """\n\n    model_config = ConfigDict(\n        frozen=True,\n        extra="forbid",\n    )\n\n    max_iterations: int = Field(\n        default=6,\n        ge=1,\n        le=10,\n    )\n    max_tool_calls: int = Field(\n        default=10,\n        ge=1,\n        le=20,\n    )\n    timeout_seconds: float = Field(\n        default=30.0,\n        ge=1.0,\n        le=60.0,\n    )\n\n\nclass InvestigationScope(BaseModel):\n    """\n    Trusted scope derived from StandardEvent, never from LLM output.\n    """\n\n    model_config = ConfigDict(\n        frozen=True,\n        extra="forbid",\n    )\n\n    alert_name: ShortText\n    alert_message: str = Field(\n        default="",\n        max_length=2000,\n    )\n    event_occurred_at: datetime | None = None\n    resource: ShortText\n    namespace: ShortText = "default"\n    cluster: ShortText | None = None\n\n\nclass EvidenceItem(BaseModel):\n    """\n    Bounded evidence retained by the Shadow loop.\n\n    Raw Kubernetes or Prometheus payloads are not stored here. facts accepts\n    scalar values only. Kubernetes log evidence is retained only as a bounded,\n    redacted excerpt, which prevents nested responses or raw log streams from\n    becoming an unbounded or sensitive reasoning transcript.\n    """\n\n    model_config = ConfigDict(\n        frozen=True,\n        extra="forbid",\n    )\n\n    evidence_id: str = Field(\n        default_factory=lambda: str(uuid4()),\n        min_length=1,\n        max_length=64,\n    )\n    probe: InvestigationProbe\n    source: ShortText\n    success: bool\n    trusted: bool\n    production_signal: bool\n    reliability: float = Field(\n        ge=0.0,\n        le=1.0,\n    )\n    observed_at: datetime\n    facts: dict[str, EvidenceScalar] = Field(\n        default_factory=dict,\n        max_length=32,\n    )\n    error_code: ShortText | None = None\n\n    @model_validator(mode="after")\n    def validate_trust_boundary(self):\n        if self.trusted and (\n            not self.success\n            or not self.production_signal\n        ):\n            raise ValueError(\n                "trusted evidence requires a successful production signal"\n            )\n\n        if not self.success and self.error_code is None:\n            raise ValueError(\n                "failed evidence requires an error code"\n            )\n\n        return self\n\n\nclass IncidentHypothesis(BaseModel):\n    """\n    One current incident explanation maintained by the reasoner.\n    """\n\n    model_config = ConfigDict(\n        extra="forbid",\n    )\n\n    hypothesis_id: ShortText\n    cause: LongText\n    confidence: float = Field(\n        ge=0.0,\n        le=1.0,\n    )\n    supporting_evidence_ids: list[ShortText] = Field(\n        default_factory=list,\n        max_length=32,\n    )\n    conflicting_evidence_ids: list[ShortText] = Field(\n        default_factory=list,\n        max_length=32,\n    )\n    missing_evidence: list[ShortText] = Field(\n        default_factory=list,\n        max_length=16,\n    )\n    optional_evidence: list[ShortText] = Field(\n        default_factory=list,\n        max_length=16,\n    )\n\n\nclass InvestigationConclusion(BaseModel):\n    """\n    Structured diagnosis output. It contains no remediation authorization.\n    """\n\n    model_config = ConfigDict(\n        extra="forbid",\n    )\n\n    root_cause: LongText\n    confidence: float = Field(\n        ge=0.0,\n        le=1.0,\n    )\n    evidence_ids: list[ShortText] = Field(\n        min_length=1,\n        max_length=32,\n    )\n    remaining_uncertainties: list[ShortText] = Field(\n        default_factory=list,\n        max_length=16,\n    )\n\n\nclass InvestigationDecision(BaseModel):\n    """\n    One bounded reasoner decision.\n\n    A non-terminal decision must select exactly one symbolic read-only probe.\n    A terminal decision cannot select a probe. Sufficient-evidence stops must\n    include a structured conclusion.\n    """\n\n    model_config = ConfigDict(\n        extra="forbid",\n    )\n\n    hypotheses: list[IncidentHypothesis] = Field(\n        min_length=1,\n        max_length=8,\n    )\n    rationale_summary: LongText\n    stop: bool = False\n    stop_reason: InvestigationStopReason | None = None\n    next_probe: InvestigationProbe | None = None\n    conclusion: InvestigationConclusion | None = None\n\n    @model_validator(mode="after")\n    def validate_decision_shape(self):\n        if self.stop:\n            if self.next_probe is not None:\n                raise ValueError(\n                    "terminal decision cannot select a probe"\n                )\n            if self.stop_reason is None:\n                raise ValueError(\n                    "terminal decision requires a stop reason"\n                )\n            if self.stop_reason not in {\n                InvestigationStopReason.SUFFICIENT_EVIDENCE,\n                InvestigationStopReason.INSUFFICIENT_EVIDENCE,\n                InvestigationStopReason.NO_SAFE_PROBE,\n            }:\n                raise ValueError(\n                    "reasoner cannot select an internal stop reason"\n                )\n            if (\n                self.stop_reason\n                == InvestigationStopReason.SUFFICIENT_EVIDENCE\n                and self.conclusion is None\n            ):\n                raise ValueError(\n                    "sufficient evidence requires a conclusion"\n                )\n            if (\n                self.stop_reason\n                != InvestigationStopReason.SUFFICIENT_EVIDENCE\n                and self.conclusion is not None\n            ):\n                raise ValueError(\n                    "insufficient evidence cannot include a conclusion"\n                )\n        else:\n            if self.next_probe is None:\n                raise ValueError(\n                    "continuing decision requires a probe"\n                )\n            if self.stop_reason is not None:\n                raise ValueError(\n                    "continuing decision cannot have a stop reason"\n                )\n            if self.conclusion is not None:\n                raise ValueError(\n                    "continuing decision cannot have a conclusion"\n                )\n\n        return self\n\n\nclass InvestigationState(BaseModel):\n    """\n    Complete bounded state of one Shadow investigation.\n    """\n\n    model_config = ConfigDict(\n        extra="forbid",\n    )\n\n    investigation_id: str = Field(\n        default_factory=lambda: str(uuid4()),\n        min_length=1,\n        max_length=64,\n    )\n    shadow_mode: Literal[True] = True\n    read_only: Literal[True] = True\n    status: InvestigationStatus = InvestigationStatus.PENDING\n    scope: InvestigationScope\n    limits: InvestigationLimits = Field(\n        default_factory=InvestigationLimits\n    )\n    started_at: datetime = Field(\n        default_factory=lambda: datetime.now(UTC)\n    )\n    updated_at: datetime = Field(\n        default_factory=lambda: datetime.now(UTC)\n    )\n    iteration_count: int = Field(\n        default=0,\n        ge=0,\n        le=10,\n    )\n    tool_call_count: int = Field(\n        default=0,\n        ge=0,\n        le=20,\n    )\n    hypotheses: list[IncidentHypothesis] = Field(\n        default_factory=list,\n        max_length=8,\n    )\n    evidence: list[EvidenceItem] = Field(\n        default_factory=list,\n        max_length=20,\n    )\n    attempted_probes: list[InvestigationProbe] = Field(\n        default_factory=list,\n        max_length=20,\n    )\n    decision_summaries: list[LongText] = Field(\n        default_factory=list,\n        max_length=10,\n    )\n    stop_reason: InvestigationStopReason | None = None\n    failure_code: ShortText | None = None\n    epistemic_guard_code: ShortText | None = None\n    conclusion: InvestigationConclusion | None = None\n'
REASONER_SOURCE = 'import json\nfrom abc import ABC, abstractmethod\n\nfrom pydantic import ValidationError\n\nfrom services.agent_runtime.app.investigation.llm_gateway_adapter import (\n    BaseInvestigationLLM,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    InvestigationDecision,\n    InvestigationProbe,\n    InvestigationScope,\n    InvestigationState,\n)\n\n\nclass InvestigationReasonerError(RuntimeError):\n    """\n    Sanitized reasoner failure.\n    """\n\n\nclass BaseInvestigationReasoner(ABC):\n    """\n    Select the next symbolic read-only probe or stop with a conclusion.\n    """\n\n    @abstractmethod\n    async def decide(\n        self,\n        scope: InvestigationScope,\n        state: InvestigationState,\n    ) -> InvestigationDecision:\n        ...\n\n\nclass LLMInvestigationReasoner(\n    BaseInvestigationReasoner\n):\n    """\n    Structured LLM reasoner for the bounded InvestigationCoordinator.\n\n    The reasoner depends only on the Investigation-owned LLM abstraction.\n    Gateway routing, provider selection, fallback, rate limiting and circuit\n    breaking remain outside this class.\n\n    It can select only an InvestigationProbe enum value. It cannot construct\n    tool calls, resource scope, PromQL, URLs or credentials.\n    """\n\n    _SYSTEM_PROMPT = (\n        "You are a bounded SRE investigation reasoner. "\n        "Maintain competing hypotheses, use only supplied "\n        "evidence, and select only one allowed symbolic "\n        "read-only probe. Never propose or execute a write."\n    )\n\n    def __init__(\n        self,\n        investigation_llm: BaseInvestigationLLM,\n    ) -> None:\n        if not isinstance(\n            investigation_llm,\n            BaseInvestigationLLM,\n        ):\n            raise TypeError(\n                "Investigation LLM adapter is invalid"\n            )\n\n        self.investigation_llm = (\n            investigation_llm\n        )\n\n    async def decide(\n        self,\n        scope: InvestigationScope,\n        state: InvestigationState,\n    ) -> InvestigationDecision:\n        content = await self.investigation_llm.complete(\n            system_prompt=self._SYSTEM_PROMPT,\n            prompt=self._build_prompt(\n                scope=scope,\n                state=state,\n            ),\n        )\n\n        if not isinstance(\n            content,\n            str,\n        ):\n            raise InvestigationReasonerError(\n                "Investigation reasoner returned no JSON"\n            )\n\n        try:\n            payload = json.loads(\n                content\n            )\n\n            return InvestigationDecision.model_validate(\n                payload\n            )\n\n        except (\n            json.JSONDecodeError,\n            ValidationError,\n            TypeError,\n            ValueError,\n        ) as exc:\n            raise InvestigationReasonerError(\n                "Investigation reasoner returned an invalid decision"\n            ) from exc\n\n    @staticmethod\n    def _build_prompt(\n        scope: InvestigationScope,\n        state: InvestigationState,\n    ) -> str:\n        trusted_evidence_ids = [\n            item.evidence_id\n            for item in state.evidence\n            if (\n                item.success\n                and item.trusted\n                and item.production_signal\n            )\n        ]\n\n        state_payload = {\n            "scope": scope.model_dump(\n                mode="json"\n            ),\n            "iteration_count": state.iteration_count,\n            "tool_call_count": state.tool_call_count,\n            "attempted_probes": [\n                probe.value\n                for probe in state.attempted_probes\n            ],\n            "hypotheses": [\n                item.model_dump(mode="json")\n                for item in state.hypotheses\n            ],\n            "evidence": [\n                item.model_dump(mode="json")\n                for item in state.evidence\n            ],\n            "trusted_evidence_ids": trusted_evidence_ids,\n            "allowed_probes": [\n                probe.value\n                for probe in InvestigationProbe\n            ],\n        }\n\n        return (\n            "Investigate the incident using the bounded state below.\\n"\n            "Return exactly one JSON object only. Do not return markdown.\\n"\n            "Maintain competing hypotheses and update confidence from evidence.\\n"\n            "Never repeat a probe already listed in attempted_probes.\\n"\n            "Never cite an evidence ID that is absent from State.evidence.\\n"\n            "Conflicting evidence can weaken a hypothesis but cannot by itself "\n            "positively establish an unrelated root cause.\\n"\n            "Ruling out one hypothesis is not sufficient evidence for a different "\n            "causal claim.\\n"\n            "Current-state evidence does not by itself prove that a historical "\n            "event never occurred.\\n"\n            "For sufficient_evidence, the conclusion must affirm a positively "\n            "supported current hypothesis. Every conclusion.evidence_id must also "\n            "appear in that hypothesis supporting_evidence_ids and must not be "\n            "conflicting evidence for that same hypothesis.\\n"\n            "Conclusion confidence must not materially exceed the confidence of "\n            "the positively supported hypothesis.\\n"\n            "A symptom or failure-mode observation such as CrashLoopBackOff, "\n            "restart count, unready state, high latency, or high error rate can "\n            "confirm that a failure exists, but does not by itself establish the "\n            "specific underlying cause that produced it.\\n"\n            "If several underlying causes remain plausible and current allowed "\n            "probes cannot discriminate among them, keep the required "\n            "root-cause evidence in hypothesis.missing_evidence and stop with "\n            "insufficient_evidence or no_safe_probe.\\n"\n            "Use hypothesis.missing_evidence only for evidence that is REQUIRED "\n            "before the specific root cause can be accepted. Use "\n            "hypothesis.optional_evidence for corroboration that may increase "\n            "confidence or describe frequency/severity but is not required to "\n            "establish the root cause.\\n"\n            "Do not put the same evidence need in both missing_evidence and "\n            "optional_evidence.\\n"\n            "Do not clear missing_evidence merely because all allowed probes "\n            "have been attempted. For sufficient_evidence, the positively "\n            "supported hypothesis used by the conclusion must have an empty "\n            "missing_evidence list. optional_evidence may remain non-empty.\\n"\n            "Treat event evidence separately from mechanism evidence. For example, "\n            "OOMKilled proves that an OOM termination occurred, but does not by "\n            "itself prove that a configured container memory limit was exceeded.\\n"\n            "A point-in-time or sampled metric cannot establish an unobserved "\n            "transient peak, historical trend, or threshold crossing. Never invent "\n            "an unseen spike to make a hypothesis fit.\\n"\n            "For quantitative threshold causes, supporting evidence must be "\n            "directionally consistent with the claimed mechanism. If a sampled "\n            "working value is below the sampled limit, that sample is not positive "\n            "support for the claim that the limit was exceeded.\\n"\n            "If an event is confirmed but the available sampled metrics do not "\n            "explain its mechanism, keep the required historical/range/peak "\n            "evidence in missing_evidence and stop with insufficient_evidence "\n            "unless another direct causal observation establishes the cause.\\n"\n            "If the available evidence only rejects hypotheses and does not "\n            "positively establish a root cause, stop with insufficient_evidence.\\n"\n            "If the available evidence only rejects hypotheses or confirms a "\n            "symptom/failure mode without establishing its cause, stop with "\n            "insufficient_evidence.\\n"\n            "For a conclusion, evidence_ids must be non-empty and copied "\n            "exactly from trusted_evidence_ids.\\n"\n            "Allowed model terminal stop_reason values are exactly: "\n            "sufficient_evidence, insufficient_evidence, no_safe_probe.\\n"\n            "Do not emit internal coordinator reasons such as timeout, "\n            "max_iterations, max_tool_calls, duplicate_probe or reasoner_error.\\n"\n            "\\n"\n            "Continuing shape:\\n"\n            "{\\n"\n            \'  "hypotheses": [{"hypothesis_id": "h1", "cause": "candidate", \'\n            \'"confidence": 0.5, "supporting_evidence_ids": [], \'\n            \'"conflicting_evidence_ids": [], "missing_evidence": ["required evidence"], "optional_evidence": ["non-blocking corroboration"]}],\\n\'\n            \'  "rationale_summary": "why this probe is most discriminative",\\n\'\n            \'  "stop": false,\\n\'\n            \'  "stop_reason": null,\\n\'\n            \'  "next_probe": "one unattempted value from allowed_probes",\\n\'\n            \'  "conclusion": null\\n\'\n            "}\\n"\n            "\\n"\n            "Terminal sufficient-evidence shape:\\n"\n            "{\\n"\n            \'  "hypotheses": [{"hypothesis_id": "h1", "cause": "supported cause", \'\n            \'"confidence": 0.9, "supporting_evidence_ids": ["copy exact trusted id"], \'\n            \'"conflicting_evidence_ids": [], "missing_evidence": [], "optional_evidence": []}],\\n\'\n            \'  "rationale_summary": "why trusted evidence is sufficient",\\n\'\n            \'  "stop": true,\\n\'\n            \'  "stop_reason": "sufficient_evidence",\\n\'\n            \'  "next_probe": null,\\n\'\n            \'  "conclusion": {"root_cause": "bounded root cause", \'\n            \'"confidence": 0.9, "evidence_ids": ["copy exact trusted id"], \'\n            \'"remaining_uncertainties": []}\\n\'\n            "}\\n"\n            "\\n"\n            "Terminal insufficient/no-safe-probe shape:\\n"\n            "{\\n"\n            \'  "hypotheses": [{"hypothesis_id": "h1", "cause": "candidate", \'\n            \'"confidence": 0.3, "supporting_evidence_ids": [], \'\n            \'"conflicting_evidence_ids": [], "missing_evidence": ["required missing evidence"], "optional_evidence": ["non-blocking evidence if useful"]}],\\n\'\n            \'  "rationale_summary": "why current bounded evidence cannot support an RCA",\\n\'\n            \'  "stop": true,\\n\'\n            \'  "stop_reason": "insufficient_evidence",\\n\'\n            \'  "next_probe": null,\\n\'\n            \'  "conclusion": null\\n\'\n            "}\\n"\n            "\\n"\n            "If no useful unattempted allowed probe can resolve the remaining "\n            "uncertainty, stop with insufficient_evidence or no_safe_probe.\\n"\n            "State:\\n"\n            f"{json.dumps(state_payload, ensure_ascii=True, sort_keys=True)}"\n        )\n\n\n__all__ = [\n    "BaseInvestigationReasoner",\n    "InvestigationReasonerError",\n    "LLMInvestigationReasoner",\n]\n'
GUARD_SOURCE = 'from __future__ import annotations\n\nfrom dataclasses import dataclass\n\nfrom services.agent_runtime.app.investigation.models import (\n    InvestigationDecision,\n    InvestigationState,\n    InvestigationStopReason,\n)\n\n\n@dataclass(\n    frozen=True,\n    slots=True,\n)\nclass EpistemicGuardResult:\n    """\n    Result of one structural conclusion-admissibility check.\n\n    The guard does not invent, rewrite or semantically classify a root cause.\n    It only checks whether a sufficient-evidence conclusion is backed by\n    positive support declared on at least one current hypothesis.\n    """\n\n    allowed: bool\n    code: str | None = None\n\n\nclass EpistemicConclusionGuard:\n    """\n    Fail-safe evidence-discipline guard for terminal RCA decisions.\n\n    This guard intentionally does NOT:\n    - infer a root cause;\n    - inspect domain-specific keywords;\n    - decide whether an alert is a false positive;\n    - replace the Investigation reasoner.\n\n    It only enforces generic epistemic invariants for\n    stop_reason=sufficient_evidence:\n\n    1. at least one current hypothesis has positive supporting evidence;\n    2. every conclusion evidence ID is positive support for one hypothesis;\n    3. conclusion evidence is not conflicting evidence for that hypothesis;\n    4. the supporting hypothesis has a minimum confidence;\n    5. the positively supported hypothesis used for the conclusion has no\n       unresolved root-cause-critical missing_evidence;\n       optional_evidence is explicitly non-blocking corroboration;\n    6. conclusion confidence may not materially exceed that hypothesis.\n\n    If these invariants are not met, the Coordinator may safely downgrade the\n    decision to insufficient_evidence instead of accepting an unsupported RCA.\n    """\n\n    def __init__(\n        self,\n        *,\n        min_supported_confidence: float = 0.5,\n        max_conclusion_confidence_delta: float = 0.05,\n    ) -> None:\n        if not (\n            0.0\n            <= min_supported_confidence\n            <= 1.0\n        ):\n            raise ValueError(\n                "min_supported_confidence must be within [0,1]"\n            )\n\n        if not (\n            0.0\n            <= max_conclusion_confidence_delta\n            <= 1.0\n        ):\n            raise ValueError(\n                "max_conclusion_confidence_delta must be within [0,1]"\n            )\n\n        self.min_supported_confidence = (\n            min_supported_confidence\n        )\n\n        self.max_conclusion_confidence_delta = (\n            max_conclusion_confidence_delta\n        )\n\n    def evaluate(\n        self,\n        *,\n        decision: InvestigationDecision,\n        state: InvestigationState,\n    ) -> EpistemicGuardResult:\n        if not isinstance(\n            decision,\n            InvestigationDecision,\n        ):\n            raise TypeError(\n                "Epistemic guard decision is invalid"\n            )\n\n        if not isinstance(\n            state,\n            InvestigationState,\n        ):\n            raise TypeError(\n                "Epistemic guard state is invalid"\n            )\n\n        if (\n            not decision.stop\n            or decision.stop_reason\n            != InvestigationStopReason.SUFFICIENT_EVIDENCE\n        ):\n            return EpistemicGuardResult(\n                allowed=True\n            )\n\n        conclusion = (\n            decision.conclusion\n        )\n\n        if conclusion is None:\n            return EpistemicGuardResult(\n                allowed=False,\n                code="MissingConclusion",\n            )\n\n        conclusion_ids = set(\n            conclusion.evidence_ids\n        )\n\n        if not conclusion_ids:\n            return EpistemicGuardResult(\n                allowed=False,\n                code="MissingConclusionEvidence",\n            )\n\n        positively_supported = [\n            hypothesis\n            for hypothesis\n            in decision.hypotheses\n            if hypothesis.supporting_evidence_ids\n        ]\n\n        if not positively_supported:\n            return EpistemicGuardResult(\n                allowed=False,\n                code="NoPositiveHypothesisSupport",\n            )\n\n        support_compatible = []\n\n        for hypothesis in positively_supported:\n            supporting_ids = set(\n                hypothesis.supporting_evidence_ids\n            )\n\n            conflicting_ids = set(\n                hypothesis.conflicting_evidence_ids\n            )\n\n            if not conclusion_ids.issubset(\n                supporting_ids\n            ):\n                continue\n\n            if conclusion_ids.intersection(\n                conflicting_ids\n            ):\n                continue\n\n            support_compatible.append(\n                hypothesis\n            )\n\n        if not support_compatible:\n            return EpistemicGuardResult(\n                allowed=False,\n                code="ConclusionEvidenceNotPositiveSupport",\n            )\n\n        # Only root-cause-blocking missing_evidence prevents a terminal RCA.\n        # optional_evidence is deliberately ignored here: it is corroboration,\n        # not a prerequisite for accepting the supported cause.\n        causally_complete = [\n            hypothesis\n            for hypothesis\n            in support_compatible\n            if not hypothesis.missing_evidence\n        ]\n\n        if not causally_complete:\n            return EpistemicGuardResult(\n                allowed=False,\n                code="SupportedHypothesisStillMissingEvidence",\n            )\n\n        confidence_compatible = [\n            hypothesis\n            for hypothesis\n            in causally_complete\n            if (\n                hypothesis.confidence\n                >= self.min_supported_confidence\n            )\n        ]\n\n        if not confidence_compatible:\n            return EpistemicGuardResult(\n                allowed=False,\n                code="SupportedHypothesisConfidenceTooLow",\n            )\n\n        for hypothesis in confidence_compatible:\n            permitted = min(\n                1.0,\n                (\n                    hypothesis.confidence\n                    + self.max_conclusion_confidence_delta\n                ),\n            )\n\n            if (\n                conclusion.confidence\n                <= permitted\n            ):\n                return EpistemicGuardResult(\n                    allowed=True\n                )\n\n        return EpistemicGuardResult(\n            allowed=False,\n            code="ConclusionConfidenceExceedsSupport",\n        )\n\n\n__all__ = [\n    "EpistemicConclusionGuard",\n    "EpistemicGuardResult",\n]\n'
BENCHMARK_ENGINE_SOURCE = 'from __future__ import annotations\n\nimport json\nfrom dataclasses import dataclass\nfrom datetime import UTC, datetime\nfrom types import SimpleNamespace\nfrom typing import Any\n\nfrom pydantic import BaseModel, ConfigDict, Field\n\nfrom services.agent_runtime.app.evaluation.real_incident.llm_run import (\n    create_historical_llm_runtime,\n)\nfrom services.agent_runtime.app.investigation.coordinator import (\n    EvidenceDrivenInvestigationCoordinator,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    EvidenceItem,\n    InvestigationDecision,\n    InvestigationLimits,\n    InvestigationProbe,\n    InvestigationState,\n    InvestigationStopReason,\n)\nfrom services.agent_runtime.app.investigation.reasoner import (\n    BaseInvestigationReasoner,\n)\n\n\nclass IntelligenceBenchmarkError(\n    RuntimeError\n):\n    pass\n\n\nclass _BenchmarkMonotonicClock:\n    """\n    Deterministic logical clock for Intelligence Benchmark control limits.\n\n    Real provider/network latency must not decide whether an intelligence\n    scenario reaches its terminal reasoning step. asyncio.wait_for still keeps\n    the coordinator\'s per-call timeout protection, while cumulative benchmark\n    elapsed time advances only by a tiny logical step per control check.\n    """\n\n    def __init__(\n        self,\n        *,\n        step_seconds: float = 0.001,\n    ) -> None:\n        self._value = 0.0\n        self._step_seconds = (\n            step_seconds\n        )\n\n    def __call__(\n        self,\n    ) -> float:\n        current = self._value\n        self._value += (\n            self._step_seconds\n        )\n        return current\n\n\nclass BenchmarkScenario(BaseModel):\n    """\n    One hidden-label Investigation exam.\n\n    hidden_* fields are evaluator-only. They never enter the Agent context,\n    InvestigationScope, LLM prompt, EvidenceItem facts or decision history.\n    """\n\n    model_config = ConfigDict(\n        frozen=True,\n        extra="forbid",\n    )\n\n    key: str\n    title: str\n\n    alert_name: str\n    alert_message: str\n\n    resource: str = "payment-api"\n    namespace: str = "payment"\n    cluster: str = "benchmark-lab"\n\n    evidence_by_probe: dict[\n        InvestigationProbe,\n        dict[str, Any] | str,\n    ]\n\n    hidden_expected_stop_reason: (\n        InvestigationStopReason\n    )\n\n    hidden_required_probes: list[\n        InvestigationProbe\n    ] = Field(\n        default_factory=list\n    )\n\n    hidden_preferred_first_probes: list[\n        InvestigationProbe\n    ] = Field(\n        default_factory=list\n    )\n\n    hidden_root_cause_keyword_groups: list[\n        list[str]\n    ] = Field(\n        default_factory=list\n    )\n\n    hidden_missing_capability_keywords: list[\n        str\n    ] = Field(\n        default_factory=list\n    )\n\n    hidden_max_reasonable_tool_calls: int = Field(\n        default=4,\n        ge=0,\n        le=10,\n    )\n\n\nclass ScenarioScore(BaseModel):\n    model_config = ConfigDict(\n        frozen=True,\n        extra="forbid",\n    )\n\n    scenario_key: str\n    title: str\n\n    score: float = Field(\n        ge=0.0,\n        le=100.0,\n    )\n\n    expected_stop_reason: str\n    outcome_correct: bool\n    grounding_correct: bool\n    required_probe_coverage: float\n    first_probe_quality: bool | None\n    tool_efficiency: float\n    root_cause_or_abstention_correct: bool\n    missing_capability_awareness: bool | None\n\n    final_status: str\n    final_stop_reason: str | None\n    failure_code: str | None\n    epistemic_guard_code: str | None\n    guard_rescued: bool\n\n    attempted_probes: list[str]\n    tool_call_count: int\n    iteration_count: int\n\n    conclusion_root_cause: str | None\n    conclusion_confidence: float | None\n\n    decision_trace: list[\n        dict[str, Any]\n    ]\n\n    notes: list[str] = Field(\n        default_factory=list\n    )\n\n\nclass IntelligenceBenchmarkReport(BaseModel):\n    model_config = ConfigDict(\n        frozen=True,\n        extra="forbid",\n    )\n\n    schema_version: str = "v1"\n    generated_at: datetime\n\n    provider: str\n    mode: str\n\n    scenario_count: int\n    overall_score: float = Field(\n        ge=0.0,\n        le=100.0,\n    )\n\n    outcome_accuracy: float = Field(\n        ge=0.0,\n        le=100.0,\n    )\n\n    abstention_accuracy: float = Field(\n        ge=0.0,\n        le=100.0,\n    )\n\n    sufficient_evidence_accuracy: float = Field(\n        ge=0.0,\n        le=100.0,\n    )\n\n    average_tool_calls: float = Field(\n        ge=0.0,\n    )\n\n    guard_rescue_count: int = Field(\n        ge=0,\n    )\n\n    guard_rescue_rate: float = Field(\n        ge=0.0,\n        le=100.0,\n    )\n\n    scenarios: list[\n        ScenarioScore\n    ]\n\n    strongest_signals: list[str]\n    weakest_signals: list[str]\n\n\nclass BenchmarkProbeExecutor:\n    """\n    Synthetic evidence backend for model-intelligence evaluation.\n\n    The model sees only the evidence corresponding to probes it chose.\n    Hidden labels remain inside BenchmarkScenario and never cross this class\n    into EvidenceItem.\n    """\n\n    def __init__(\n        self,\n        scenario: BenchmarkScenario,\n        *,\n        observed_at: datetime,\n    ) -> None:\n        self.scenario = scenario\n        self.observed_at = observed_at\n        self.calls: list[\n            InvestigationProbe\n        ] = []\n\n    async def collect(\n        self,\n        context,\n        scope,\n        probe,\n    ) -> EvidenceItem:\n        self.calls.append(\n            probe\n        )\n\n        value = (\n            self.scenario\n            .evidence_by_probe\n            .get(\n                probe\n            )\n        )\n\n        if isinstance(\n            value,\n            str,\n        ):\n            raise RuntimeError(\n                "Benchmark probe unavailable"\n            )\n\n        if value is None:\n            raise RuntimeError(\n                "Benchmark probe has no observation"\n            )\n\n        source = (\n            "kubernetes"\n            if probe\n            in {\n                InvestigationProbe.KUBERNETES_POD_STATE,\n                (\n                    InvestigationProbe\n                    .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n                ),\n            }\n            else "prometheus"\n        )\n\n        return EvidenceItem(\n            evidence_id=(\n                f"{self.scenario.key}:"\n                f"{probe.value}"\n            ),\n            probe=probe,\n            source=source,\n            success=True,\n            trusted=True,\n            production_signal=True,\n            reliability=1.0,\n            observed_at=self.observed_at,\n            facts=dict(\n                value\n            ),\n        )\n\n\nclass TracingReasoner(\n    BaseInvestigationReasoner\n):\n    """\n    Transparent delegate that records the actual Agent decisions.\n\n    It does not modify prompts, decisions, state or provider behavior.\n    """\n\n    def __init__(\n        self,\n        delegate: BaseInvestigationReasoner,\n    ) -> None:\n        if not isinstance(\n            delegate,\n            BaseInvestigationReasoner,\n        ):\n            raise TypeError(\n                "Benchmark delegate reasoner is invalid"\n            )\n\n        self.delegate = delegate\n\n        self.decisions: list[\n            InvestigationDecision\n        ] = []\n\n        self.states: list[\n            InvestigationState\n        ] = []\n\n    async def decide(\n        self,\n        scope,\n        state,\n    ) -> InvestigationDecision:\n        self.states.append(\n            state.model_copy(\n                deep=True\n            )\n        )\n\n        decision = await (\n            self.delegate.decide(\n                scope,\n                state,\n            )\n        )\n\n        self.decisions.append(\n            decision.model_copy(\n                deep=True\n            )\n        )\n\n        return decision\n\n\ndef _context(\n    scenario: BenchmarkScenario,\n):\n    return SimpleNamespace(\n        event=SimpleNamespace(\n            signal=SimpleNamespace(\n                name=scenario.alert_name,\n                message=(\n                    scenario.alert_message\n                ),\n            ),\n            resources=[\n                SimpleNamespace(\n                    name=scenario.resource,\n                    namespace=scenario.namespace,\n                    cluster=scenario.cluster,\n                )\n            ],\n        ),\n        metadata={},\n        variables={},\n    )\n\n\ndef _normalize_text(\n    value: str | None,\n) -> str:\n    if not value:\n        return ""\n\n    return (\n        value\n        .strip()\n        .lower()\n    )\n\n\ndef _missing_capability_text(\n    decisions: list[\n        InvestigationDecision\n    ],\n) -> str:\n    """\n    Return only explicit unresolved-evidence language.\n\n    Hypothesis causes, rationale prose and conclusion root-cause text are\n    intentionally excluded. Guessing "application panic" is not the same as\n    recognizing that application/container logs are missing.\n    """\n\n    fragments: list[\n        str\n    ] = []\n\n    for decision in decisions:\n        for hypothesis in decision.hypotheses:\n            fragments.extend(\n                hypothesis.missing_evidence\n            )\n\n        if decision.conclusion is not None:\n            fragments.extend(\n                decision.conclusion.remaining_uncertainties\n            )\n\n    return _normalize_text(\n        "\\n".join(\n            fragments\n        )\n    )\n\n\ndef _keyword_groups_match(\n    text: str,\n    groups: list[\n        list[str]\n    ],\n) -> bool:\n    normalized = _normalize_text(\n        text\n    )\n\n    if not groups:\n        return True\n\n    for group in groups:\n        if not any(\n            _normalize_text(\n                token\n            )\n            in normalized\n            for token in group\n        ):\n            return False\n\n    return True\n\n\ndef _decision_trace(\n    decisions: list[\n        InvestigationDecision\n    ],\n) -> list[\n    dict[str, Any]\n]:\n    trace = []\n\n    for index, decision in enumerate(\n        decisions,\n        start=1,\n    ):\n        trace.append(\n            {\n                "iteration": index,\n                "hypotheses": [\n                    {\n                        "hypothesis_id": (\n                            item.hypothesis_id\n                        ),\n                        "cause": item.cause,\n                        "confidence": (\n                            item.confidence\n                        ),\n                        "supporting_evidence_ids": list(\n                            item.supporting_evidence_ids\n                        ),\n                        "conflicting_evidence_ids": list(\n                            item.conflicting_evidence_ids\n                        ),\n                        "missing_evidence": list(\n                            item.missing_evidence\n                        ),\n                        "optional_evidence": list(\n                            item.optional_evidence\n                        ),\n                    }\n                    for item in decision.hypotheses\n                ],\n                "rationale_summary": (\n                    decision.rationale_summary\n                ),\n                "stop": decision.stop,\n                "stop_reason": (\n                    decision.stop_reason.value\n                    if decision.stop_reason\n                    is not None\n                    else None\n                ),\n                "next_probe": (\n                    decision.next_probe.value\n                    if decision.next_probe\n                    is not None\n                    else None\n                ),\n                "conclusion": (\n                    decision.conclusion.model_dump(\n                        mode="json"\n                    )\n                    if decision.conclusion\n                    is not None\n                    else None\n                ),\n            }\n        )\n\n    return trace\n\n\ndef score_scenario(\n    *,\n    scenario: BenchmarkScenario,\n    state: InvestigationState,\n    decisions: list[\n        InvestigationDecision\n    ],\n) -> ScenarioScore:\n    attempted = list(state.attempted_probes)\n    expected_stop = scenario.hidden_expected_stop_reason\n\n    legitimate_terminal = (\n        state.status.value == "concluded"\n        and state.stop_reason == expected_stop\n    )\n    outcome_correct = legitimate_terminal\n\n    if expected_stop == InvestigationStopReason.SUFFICIENT_EVIDENCE:\n        if not legitimate_terminal or state.conclusion is None:\n            grounding_correct = False\n        else:\n            trusted_ids = {\n                item.evidence_id\n                for item in state.evidence\n                if (\n                    item.success\n                    and item.trusted\n                    and item.production_signal\n                )\n            }\n            conclusion_ids = set(state.conclusion.evidence_ids)\n            grounding_correct = (\n                bool(conclusion_ids)\n                and conclusion_ids.issubset(trusted_ids)\n            )\n    else:\n        grounding_correct = (\n            legitimate_terminal\n            and state.conclusion is None\n        )\n\n    required = set(scenario.hidden_required_probes)\n    attempted_set = set(attempted)\n    required_probe_coverage = (\n        len(required & attempted_set) / len(required)\n        if required\n        else 1.0\n    )\n\n    if scenario.hidden_preferred_first_probes:\n        first_probe_quality = (\n            bool(attempted)\n            and attempted[0]\n            in scenario.hidden_preferred_first_probes\n        )\n    else:\n        first_probe_quality = None\n\n    max_calls = scenario.hidden_max_reasonable_tool_calls\n    if max_calls <= 0:\n        tool_efficiency = 1.0 if state.tool_call_count == 0 else 0.0\n    elif state.tool_call_count <= max_calls:\n        tool_efficiency = 1.0\n    else:\n        tool_efficiency = max(\n            0.0,\n            1.0 - (\n                state.tool_call_count - max_calls\n            ) / max_calls,\n        )\n\n    if expected_stop == InvestigationStopReason.SUFFICIENT_EVIDENCE:\n        root_cause_or_abstention_correct = (\n            legitimate_terminal\n            and state.conclusion is not None\n            and _keyword_groups_match(\n                state.conclusion.root_cause,\n                scenario.hidden_root_cause_keyword_groups,\n            )\n        )\n    else:\n        root_cause_or_abstention_correct = (\n            legitimate_terminal\n            and state.conclusion is None\n        )\n\n    if scenario.hidden_missing_capability_keywords:\n        reasoner_text = _missing_capability_text(decisions)\n        missing_capability_awareness = any(\n            _normalize_text(keyword) in reasoner_text\n            for keyword\n            in scenario.hidden_missing_capability_keywords\n        )\n    else:\n        missing_capability_awareness = None\n\n    score = 0.0\n    score += 30.0 if outcome_correct else 0.0\n    score += 20.0 if grounding_correct else 0.0\n\n    probe_weight = 30.0 if first_probe_quality is None else 20.0\n    score += required_probe_coverage * probe_weight\n\n    if first_probe_quality is not None:\n        score += 10.0 if first_probe_quality else 0.0\n\n    score += tool_efficiency * 10.0\n    score += 10.0 if root_cause_or_abstention_correct else 0.0\n\n    guard_rescued = (\n        state.epistemic_guard_code\n        is not None\n        and outcome_correct\n    )\n\n    if guard_rescued:\n        score = min(\n            score,\n            85.0,\n        )\n\n    notes: list[str] = []\n\n    if guard_rescued:\n        notes.append(\n            "Epistemic guard converted an unsupported sufficient-evidence "\n            "decision into safe insufficient_evidence."\n        )\n\n    if not outcome_correct:\n        notes.append(\n            "Final stop reason/status did not match the hidden evaluator label."\n        )\n\n    if state.status.value == "failed":\n        notes.append(\n            "Failed investigation is not counted as a valid abstention."\n        )\n\n    if (\n        expected_stop != InvestigationStopReason.SUFFICIENT_EVIDENCE\n        and state.conclusion is not None\n    ):\n        notes.append(\n            "Agent produced an RCA where the benchmark expected abstention."\n        )\n\n    if missing_capability_awareness is False:\n        notes.append(\n            "Agent did not explicitly recognize the expected missing capability."\n        )\n\n    return ScenarioScore(\n        scenario_key=scenario.key,\n        title=scenario.title,\n        expected_stop_reason=expected_stop.value,\n        score=round(\n            min(100.0, max(0.0, score)),\n            1,\n        ),\n        outcome_correct=outcome_correct,\n        grounding_correct=grounding_correct,\n        required_probe_coverage=round(\n            required_probe_coverage,\n            3,\n        ),\n        first_probe_quality=first_probe_quality,\n        tool_efficiency=round(\n            tool_efficiency,\n            3,\n        ),\n        root_cause_or_abstention_correct=(\n            root_cause_or_abstention_correct\n        ),\n        missing_capability_awareness=(\n            missing_capability_awareness\n        ),\n        final_status=state.status.value,\n        final_stop_reason=(\n            state.stop_reason.value\n            if state.stop_reason is not None\n            else None\n        ),\n        failure_code=state.failure_code,\n        epistemic_guard_code=(\n            state.epistemic_guard_code\n        ),\n        guard_rescued=guard_rescued,\n        attempted_probes=[\n            item.value\n            for item in attempted\n        ],\n        tool_call_count=state.tool_call_count,\n        iteration_count=state.iteration_count,\n        conclusion_root_cause=(\n            state.conclusion.root_cause\n            if state.conclusion is not None\n            else None\n        ),\n        conclusion_confidence=(\n            state.conclusion.confidence\n            if state.conclusion is not None\n            else None\n        ),\n        decision_trace=_decision_trace(decisions),\n        notes=notes,\n    )\n\n\nasync def run_scenario(\n    *,\n    reasoner: BaseInvestigationReasoner,\n    scenario: BenchmarkScenario,\n    limits: InvestigationLimits,\n    observed_at: datetime,\n) -> ScenarioScore:\n    tracing = TracingReasoner(\n        reasoner\n    )\n\n    probes = BenchmarkProbeExecutor(\n        scenario,\n        observed_at=observed_at,\n    )\n\n    coordinator = (\n        EvidenceDrivenInvestigationCoordinator(\n            reasoner=tracing,\n            probe_executor=probes,\n            limits=limits,\n            monotonic_clock=(\n                _BenchmarkMonotonicClock()\n            ),\n            utc_clock=lambda: observed_at,\n        )\n    )\n\n    state = await coordinator.investigate(\n        _context(\n            scenario\n        )\n    )\n\n    return score_scenario(\n        scenario=scenario,\n        state=state,\n        decisions=tracing.decisions,\n    )\n\n\ndef build_bailian_reasoner(\n    *,\n    provider_name: str,\n    limits: InvestigationLimits,\n) -> BaseInvestigationReasoner:\n    runtime = (\n        create_historical_llm_runtime(\n            limits=limits,\n            provider_name=provider_name,\n        )\n    )\n\n    coordinator = getattr(\n        runtime,\n        "investigation_coordinator",\n        None,\n    )\n\n    reasoner = getattr(\n        coordinator,\n        "reasoner",\n        None,\n    )\n\n    if not isinstance(\n        reasoner,\n        BaseInvestigationReasoner,\n    ):\n        raise IntelligenceBenchmarkError(\n            "Benchmark could not obtain the canonical Investigation reasoner"\n        )\n\n    return reasoner\n\n\ndef build_report(\n    *,\n    provider: str,\n    mode: str,\n    scenarios: list[\n        ScenarioScore\n    ],\n) -> IntelligenceBenchmarkReport:\n    if not scenarios:\n        raise IntelligenceBenchmarkError(\n            "Benchmark produced no scenario results"\n        )\n\n    overall_score = (\n        sum(item.score for item in scenarios)\n        / len(scenarios)\n    )\n\n    outcome_accuracy = (\n        sum(\n            1\n            for item in scenarios\n            if item.outcome_correct\n        )\n        / len(scenarios)\n        * 100.0\n    )\n\n    expected_abstention_cases = [\n        item\n        for item in scenarios\n        if item.expected_stop_reason\n        != InvestigationStopReason.SUFFICIENT_EVIDENCE.value\n    ]\n\n    abstention_accuracy = (\n        sum(\n            1\n            for item in expected_abstention_cases\n            if (\n                item.outcome_correct\n                and item.root_cause_or_abstention_correct\n            )\n        )\n        / len(expected_abstention_cases)\n        * 100.0\n        if expected_abstention_cases\n        else 0.0\n    )\n\n    expected_sufficient_cases = [\n        item\n        for item in scenarios\n        if item.expected_stop_reason\n        == InvestigationStopReason.SUFFICIENT_EVIDENCE.value\n    ]\n\n    sufficient_evidence_accuracy = (\n        sum(\n            1\n            for item in expected_sufficient_cases\n            if (\n                item.outcome_correct\n                and item.grounding_correct\n                and item.root_cause_or_abstention_correct\n            )\n        )\n        / len(expected_sufficient_cases)\n        * 100.0\n        if expected_sufficient_cases\n        else 0.0\n    )\n\n    average_tool_calls = (\n        sum(\n            item.tool_call_count\n            for item in scenarios\n        )\n        / len(scenarios)\n    )\n\n    guard_rescue_count = sum(\n        1\n        for item in scenarios\n        if item.guard_rescued\n    )\n\n    guard_rescue_rate = (\n        guard_rescue_count\n        / len(scenarios)\n        * 100.0\n    )\n\n    ordered = sorted(\n        scenarios,\n        key=lambda item: (\n            item.score,\n            item.scenario_key,\n        ),\n    )\n\n    weakest = [\n        f"{item.scenario_key}: {item.score:.1f}/100"\n        for item in ordered[:3]\n    ]\n\n    strongest = [\n        f"{item.scenario_key}: {item.score:.1f}/100"\n        for item in reversed(ordered[-3:])\n    ]\n\n    return IntelligenceBenchmarkReport(\n        generated_at=datetime.now(UTC),\n        provider=provider,\n        mode=mode,\n        scenario_count=len(scenarios),\n        overall_score=round(\n            overall_score,\n            1,\n        ),\n        outcome_accuracy=round(\n            outcome_accuracy,\n            1,\n        ),\n        abstention_accuracy=round(\n            abstention_accuracy,\n            1,\n        ),\n        sufficient_evidence_accuracy=round(\n            sufficient_evidence_accuracy,\n            1,\n        ),\n        average_tool_calls=round(\n            average_tool_calls,\n            2,\n        ),\n        guard_rescue_count=(\n            guard_rescue_count\n        ),\n        guard_rescue_rate=round(\n            guard_rescue_rate,\n            1,\n        ),\n        scenarios=scenarios,\n        strongest_signals=strongest,\n        weakest_signals=weakest,\n    )\n\n\ndef render_report(\n    report: IntelligenceBenchmarkReport,\n) -> str:\n    lines = [\n        "=" * 96,\n        "INVESTIGATION INTELLIGENCE BENCHMARK v1",\n        "=" * 96,\n        "",\n        f"GeneratedAt: {report.generated_at.isoformat()}",\n        f"Provider: {report.provider}",\n        f"Mode: {report.mode}",\n        f"Scenarios: {report.scenario_count}",\n        "",\n        f"OverallScore: {report.overall_score:.1f}/100",\n        f"OutcomeAccuracy: {report.outcome_accuracy:.1f}%",\n        f"AbstentionAccuracy: {report.abstention_accuracy:.1f}%",\n        (\n            "SufficientEvidenceAccuracy: "\n            f"{report.sufficient_evidence_accuracy:.1f}%"\n        ),\n        f"AverageToolCalls: {report.average_tool_calls:.2f}",\n        f"GuardRescueCount: {report.guard_rescue_count}",\n        f"GuardRescueRate: {report.guard_rescue_rate:.1f}%",\n        "",\n        "Important:",\n        "- This is a controlled synthetic-evidence intelligence benchmark.",\n        "- The actual LLM Investigation reasoner is used in live mode.",\n        "- Hidden evaluator labels never enter the Agent prompt.",\n        "- This is stronger than unit testing but is not a production validation.",\n        "",\n        "SCENARIOS",\n        "-" * 96,\n    ]\n\n    for item in report.scenarios:\n        lines.extend(\n            [\n                "",\n                (\n                    f"[{item.score:5.1f}] "\n                    f"{item.scenario_key} - {item.title}"\n                ),\n                (\n                    "  outcome_correct="\n                    f"{item.outcome_correct}"\n                ),\n                (\n                    "  grounding_correct="\n                    f"{item.grounding_correct}"\n                ),\n                (\n                    "  required_probe_coverage="\n                    f"{item.required_probe_coverage:.3f}"\n                ),\n                (\n                    "  first_probe_quality="\n                    f"{item.first_probe_quality}"\n                ),\n                (\n                    "  tool_efficiency="\n                    f"{item.tool_efficiency:.3f}"\n                ),\n                (\n                    "  root_cause_or_abstention_correct="\n                    f"{item.root_cause_or_abstention_correct}"\n                ),\n                (\n                    "  missing_capability_awareness="\n                    f"{item.missing_capability_awareness}"\n                ),\n                (\n                    "  expected_stop_reason="\n                    f"{item.expected_stop_reason}"\n                ),\n                (\n                    "  final="\n                    f"{item.final_status}/"\n                    f"{item.final_stop_reason}"\n                ),\n                (\n                    "  failure_code="\n                    f"{item.failure_code}"\n                ),\n                (\n                    "  epistemic_guard_code="\n                    f"{item.epistemic_guard_code}"\n                ),\n                (\n                    "  guard_rescued="\n                    f"{item.guard_rescued}"\n                ),\n                (\n                    "  probes="\n                    + ", ".join(\n                        item.attempted_probes\n                    )\n                ),\n                (\n                    "  conclusion="\n                    + (\n                        item.conclusion_root_cause\n                        or "<NONE>"\n                    )\n                ),\n                (\n                    "  confidence="\n                    + (\n                        str(\n                            item.conclusion_confidence\n                        )\n                        if item.conclusion_confidence\n                        is not None\n                        else "<NONE>"\n                    )\n                ),\n            ]\n        )\n\n        for note in item.notes:\n            lines.append(\n                f"  note: {note}"\n            )\n\n        lines.append(\n            "  decision_trace:"\n        )\n\n        for decision in item.decision_trace:\n            lines.append(\n                "    "\n                + json.dumps(\n                    decision,\n                    ensure_ascii=False,\n                    sort_keys=True,\n                )\n            )\n\n    lines.extend(\n        [\n            "",\n            "STRONGEST",\n            "-" * 96,\n            *[\n                f"- {value}"\n                for value\n                in report.strongest_signals\n            ],\n            "",\n            "WEAKEST",\n            "-" * 96,\n            *[\n                f"- {value}"\n                for value\n                in report.weakest_signals\n            ],\n            "",\n            "=" * 96,\n        ]\n    )\n\n    return "\\n".join(\n        lines\n    ) + "\\n"\n\n\n__all__ = [\n    "BenchmarkProbeExecutor",\n    "BenchmarkScenario",\n    "IntelligenceBenchmarkError",\n    "IntelligenceBenchmarkReport",\n    "ScenarioScore",\n    "TracingReasoner",\n    "build_bailian_reasoner",\n    "build_report",\n    "render_report",\n    "run_scenario",\n    "score_scenario",\n]\n'
BENCHMARK_RUNNER_SOURCE = 'from __future__ import annotations\n\nimport argparse\nimport asyncio\nimport json\nimport os\nimport sys\nimport traceback\nfrom datetime import UTC, datetime\nfrom pathlib import Path\n\n\nTEXT_REPORT = (\n    "investigation_intelligence_benchmark_v1_report.txt"\n)\n\nJSON_REPORT = (\n    "investigation_intelligence_benchmark_v1_report.json"\n)\n\nERROR_REPORT = (\n    "investigation_intelligence_benchmark_v1_error.txt"\n)\n\n\ndef find_repo_root(\n    start: Path,\n) -> Path:\n    for candidate in (\n        start,\n        *start.parents,\n    ):\n        if (\n            (candidate / "pyproject.toml").exists()\n            and (candidate / "services").exists()\n            and (candidate / "packages").exists()\n        ):\n            return candidate\n\n    raise RuntimeError(\n        "Repository root not found."\n    )\n\n\ndef install_import_paths(\n    root: Path,\n) -> None:\n    for candidate in reversed(\n        [\n            root,\n            root / "packages" / "common" / "src",\n        ]\n    ):\n        value = str(\n            candidate\n        )\n\n        if value not in sys.path:\n            sys.path.insert(\n                0,\n                value,\n            )\n\n\ndef write_text(\n    path: Path,\n    value: str,\n) -> None:\n    path.write_text(\n        value.replace(\n            "\\r\\n",\n            "\\n",\n        ).replace(\n            "\\r",\n            "\\n",\n        ),\n        encoding="utf-8",\n        newline="\\n",\n    )\n\n\ndef verify_app_yaml_mock(\n    root: Path,\n) -> None:\n    path = (\n        root\n        / "configs"\n        / "app.yaml"\n    )\n\n    text = path.read_text(\n        encoding="utf-8-sig"\n    )\n\n    start = text.find(\n        "llm:"\n    )\n\n    if start < 0:\n        raise RuntimeError(\n            "configs/app.yaml has no llm section"\n        )\n\n    provider = None\n\n    for line in text[\n        start\n        + len(\n            "llm:"\n        ) :\n    ].splitlines():\n        stripped = line.strip()\n\n        if (\n            stripped\n            and not line.startswith(\n                (\n                    " ",\n                    "\\t",\n                )\n            )\n        ):\n            break\n\n        if stripped.startswith(\n            "provider:"\n        ):\n            provider = (\n                stripped\n                .split(\n                    ":",\n                    1,\n                )[1]\n                .strip()\n            )\n            break\n\n    if provider != "mock":\n        raise RuntimeError(\n            "Safety invariant failed: configs/app.yaml must remain provider: mock"\n        )\n\n\nasync def run_live(\n    *,\n    provider: str,\n    mode: str,\n    selected_keys: list[str],\n):\n    from services.agent_runtime.app.evaluation.intelligence_benchmark import (\n        build_bailian_reasoner,\n        build_report,\n        render_report,\n        run_scenario,\n        scenario_by_key,\n        scenarios_for_mode,\n    )\n    from services.agent_runtime.app.investigation.models import (\n        InvestigationLimits,\n    )\n\n    limits = InvestigationLimits(\n        max_iterations=6,\n        max_tool_calls=5,\n        timeout_seconds=60,\n    )\n\n    reasoner = (\n        build_bailian_reasoner(\n            provider_name=provider,\n            limits=limits,\n        )\n    )\n\n    if selected_keys:\n        scenarios = [\n            scenario_by_key(\n                key\n            )\n            for key in selected_keys\n        ]\n    else:\n        scenarios = scenarios_for_mode(\n            mode\n        )\n\n    observed_at = datetime(\n        2026,\n        8,\n        10,\n        8,\n        45,\n        tzinfo=UTC,\n    )\n\n    scores = []\n\n    for scenario in scenarios:\n        print(\n            f"[RUN] {scenario.key}"\n        )\n\n        score = await run_scenario(\n            reasoner=reasoner,\n            scenario=scenario,\n            limits=limits,\n            observed_at=observed_at,\n        )\n\n        scores.append(\n            score\n        )\n\n        print(\n            (\n                f"[DONE] {scenario.key}: "\n                f"{score.score:.1f}/100 "\n                f"stop={score.final_stop_reason} "\n                f"tools={score.tool_call_count}"\n            )\n        )\n\n    report = build_report(\n        provider=provider,\n        mode=(\n            "custom"\n            if selected_keys\n            else mode\n        ),\n        scenarios=scores,\n    )\n\n    return (\n        report,\n        render_report(\n            report\n        ),\n    )\n\n\ndef main() -> int:\n    parser = argparse.ArgumentParser(\n        description=(\n            "Run the real Investigation LLM reasoner against hidden-label "\n            "synthetic evidence scenarios."\n        )\n    )\n\n    parser.add_argument(\n        "--provider",\n        default="bailian",\n    )\n\n    parser.add_argument(\n        "--mode",\n        choices=(\n            "smoke",\n            "full",\n        ),\n        default="smoke",\n    )\n\n    parser.add_argument(\n        "--scenario",\n        action="append",\n        default=[],\n        help=(\n            "Run one named scenario. Repeat to run multiple. "\n            "Overrides --mode."\n        ),\n    )\n\n    args = parser.parse_args()\n\n    root = find_repo_root(\n        Path.cwd().resolve()\n    )\n\n    install_import_paths(\n        root\n    )\n\n    text_path = (\n        root\n        / TEXT_REPORT\n    )\n\n    json_path = (\n        root\n        / JSON_REPORT\n    )\n\n    error_path = (\n        root\n        / ERROR_REPORT\n    )\n\n    for path in (\n        text_path,\n        json_path,\n        error_path,\n    ):\n        try:\n            path.unlink()\n        except FileNotFoundError:\n            pass\n\n    try:\n        verify_app_yaml_mock(\n            root\n        )\n\n        provider = (\n            args.provider\n            .strip()\n            .lower()\n        )\n\n        if not provider:\n            raise RuntimeError(\n                "Provider cannot be blank"\n            )\n\n        if provider == "mock":\n            raise RuntimeError(\n                "Intelligence Benchmark requires a real LLM provider"\n            )\n\n        # Preserve existing working configuration if explicitly set.\n        # These process-local defaults match the already-proven Bailian\n        # connectivity path used earlier in this repository.\n        if provider == "bailian":\n            os.environ.setdefault(\n                "BAILIAN_BASE_URL",\n                (\n                    "https://dashscope.aliyuncs.com"\n                    "/compatible-mode/v1"\n                ),\n            )\n\n            os.environ.setdefault(\n                "BAILIAN_MODEL",\n                "qwen-plus",\n            )\n\n            if not os.getenv(\n                "DASHSCOPE_API_KEY",\n                "",\n            ).strip():\n                raise RuntimeError(\n                    "DASHSCOPE_API_KEY is not present"\n                )\n\n        print(\n            "=" * 72\n        )\n        print(\n            "INVESTIGATION INTELLIGENCE BENCHMARK V1"\n        )\n        print(\n            "=" * 72\n        )\n        print(\n            f"Provider: {provider}"\n        )\n        print(\n            f"Mode: {args.mode}"\n        )\n        print(\n            "Ground truth is evaluator-only and is never sent to the model."\n        )\n        print("")\n\n        report, rendered = asyncio.run(\n            run_live(\n                provider=provider,\n                mode=args.mode,\n                selected_keys=(\n                    args.scenario\n                ),\n            )\n        )\n\n        write_text(\n            text_path,\n            rendered,\n        )\n\n        write_text(\n            json_path,\n            (\n                json.dumps(\n                    report.model_dump(\n                        mode="json"\n                    ),\n                    ensure_ascii=False,\n                    indent=2,\n                    sort_keys=True,\n                )\n                + "\\n"\n            ),\n        )\n\n        print("")\n        print(\n            "=" * 72\n        )\n        print(\n            "BENCHMARK COMPLETED"\n        )\n        print(\n            "=" * 72\n        )\n        print(\n            (\n                f"Overall: "\n                f"{report.overall_score:.1f}/100"\n            )\n        )\n        print(\n            (\n                f"Outcome accuracy: "\n                f"{report.outcome_accuracy:.1f}%"\n            )\n        )\n        print(\n            (\n                f"Average tool calls: "\n                f"{report.average_tool_calls:.2f}"\n            )\n        )\n        print("")\n        print(\n            "Upload BOTH:"\n        )\n        print(\n            text_path\n        )\n        print(\n            json_path\n        )\n\n        return 0\n\n    except Exception as exc:\n        write_text(\n            error_path,\n            (\n                "Investigation Intelligence Benchmark v1 FAILED\\n\\n"\n                f"{type(exc).__name__}: {exc}\\n\\n"\n                + traceback.format_exc()\n            ),\n        )\n\n        print("")\n        print(\n            "=" * 72\n        )\n        print(\n            "BENCHMARK FAILED"\n        )\n        print(\n            "=" * 72\n        )\n        print(\n            "Upload:"\n        )\n        print(\n            error_path\n        )\n\n        return 1\n\n\nif __name__ == "__main__":\n    raise SystemExit(\n        main()\n    )\n'
TEST_SOURCE = 'from __future__ import annotations\n\nfrom datetime import UTC, datetime\n\nfrom services.agent_runtime.app.evaluation.intelligence_benchmark.engine import (\n    BenchmarkScenario,\n    _BenchmarkMonotonicClock,\n    score_scenario,\n)\nfrom services.agent_runtime.app.investigation.epistemic_guard import (\n    EpistemicConclusionGuard,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    EvidenceItem,\n    IncidentHypothesis,\n    InvestigationConclusion,\n    InvestigationDecision,\n    InvestigationProbe,\n    InvestigationScope,\n    InvestigationState,\n    InvestigationStatus,\n    InvestigationStopReason,\n)\nfrom services.agent_runtime.app.investigation.reasoner import (\n    LLMInvestigationReasoner,\n)\n\n\nNOW = datetime(\n    2026,\n    8,\n    10,\n    13,\n    30,\n    tzinfo=UTC,\n)\n\n\ndef scope() -> InvestigationScope:\n    return InvestigationScope(\n        alert_name="PodOOMKilled",\n        alert_message="payment-api restarted",\n        resource="payment-api",\n        namespace="payment",\n        cluster="benchmark-lab",\n    )\n\n\ndef trusted(\n    evidence_id: str,\n    probe: InvestigationProbe,\n    facts=None,\n) -> EvidenceItem:\n    return EvidenceItem(\n        evidence_id=evidence_id,\n        probe=probe,\n        source=(\n            "kubernetes"\n            if probe.value.startswith(\n                "kubernetes_"\n            )\n            else "prometheus"\n        ),\n        success=True,\n        trusted=True,\n        production_signal=True,\n        reliability=1.0,\n        observed_at=NOW,\n        facts=(\n            facts\n            or {\n                "observed": True,\n            }\n        ),\n    )\n\n\ndef hypothesis(\n    *,\n    cause: str,\n    confidence: float,\n    supporting=None,\n    conflicting=None,\n    missing=None,\n    optional=None,\n) -> IncidentHypothesis:\n    return IncidentHypothesis(\n        hypothesis_id="h1",\n        cause=cause,\n        confidence=confidence,\n        supporting_evidence_ids=(\n            supporting\n            or []\n        ),\n        conflicting_evidence_ids=(\n            conflicting\n            or []\n        ),\n        missing_evidence=(\n            missing\n            or []\n        ),\n        optional_evidence=(\n            optional\n            or []\n        ),\n    )\n\n\ndef test_hypothesis_supports_blocking_and_optional_evidence():\n    value = hypothesis(\n        cause="application panic due to invalid configuration",\n        confidence=0.9,\n        supporting=[\n            "log-1"\n        ],\n        missing=[],\n        optional=[\n            "restart count for frequency corroboration"\n        ],\n    )\n\n    assert value.missing_evidence == []\n    assert value.optional_evidence == [\n        "restart count for frequency corroboration"\n    ]\n\n\ndef test_optional_evidence_does_not_block_supported_conclusion():\n    state = InvestigationState(\n        scope=scope(),\n        evidence=[\n            trusted(\n                "log-1",\n                InvestigationProbe.KUBERNETES_PREVIOUS_CONTAINER_LOGS,\n            )\n        ],\n    )\n\n    decision = InvestigationDecision(\n        hypotheses=[\n            hypothesis(\n                cause=(\n                    "application panic due to invalid configuration"\n                ),\n                confidence=0.9,\n                supporting=[\n                    "log-1"\n                ],\n                missing=[],\n                optional=[\n                    "prometheus restart count"\n                ],\n            )\n        ],\n        rationale_summary=(\n            "panic log directly establishes the startup failure"\n        ),\n        stop=True,\n        stop_reason=(\n            InvestigationStopReason.SUFFICIENT_EVIDENCE\n        ),\n        conclusion=InvestigationConclusion(\n            root_cause=(\n                "application panic due to invalid configuration"\n            ),\n            confidence=0.9,\n            evidence_ids=[\n                "log-1"\n            ],\n        ),\n    )\n\n    result = (\n        EpistemicConclusionGuard()\n        .evaluate(\n            decision=decision,\n            state=state,\n        )\n    )\n\n    assert result.allowed is True\n    assert result.code is None\n\n\ndef test_blocking_missing_evidence_still_blocks_conclusion():\n    state = InvestigationState(\n        scope=scope(),\n        evidence=[\n            trusted(\n                "log-1",\n                InvestigationProbe.KUBERNETES_PREVIOUS_CONTAINER_LOGS,\n            )\n        ],\n    )\n\n    decision = InvestigationDecision(\n        hypotheses=[\n            hypothesis(\n                cause=(\n                    "application panic due to invalid configuration"\n                ),\n                confidence=0.9,\n                supporting=[\n                    "log-1"\n                ],\n                missing=[\n                    "required configuration source"\n                ],\n                optional=[\n                    "restart count"\n                ],\n            )\n        ],\n        rationale_summary=(\n            "root cause still requires blocking evidence"\n        ),\n        stop=True,\n        stop_reason=(\n            InvestigationStopReason.SUFFICIENT_EVIDENCE\n        ),\n        conclusion=InvestigationConclusion(\n            root_cause=(\n                "application panic due to invalid configuration"\n            ),\n            confidence=0.9,\n            evidence_ids=[\n                "log-1"\n            ],\n        ),\n    )\n\n    result = (\n        EpistemicConclusionGuard()\n        .evaluate(\n            decision=decision,\n            state=state,\n        )\n    )\n\n    assert result.allowed is False\n    assert (\n        result.code\n        == "SupportedHypothesisStillMissingEvidence"\n    )\n\n\ndef test_reasoner_prompt_contains_optional_evidence_contract():\n    value = scope()\n\n    prompt = (\n        LLMInvestigationReasoner\n        ._build_prompt(\n            scope=value,\n            state=InvestigationState(\n                scope=value\n            ),\n        )\n    )\n\n    assert (\n        "Use hypothesis.missing_evidence only for evidence that is REQUIRED"\n        in prompt\n    )\n\n    assert (\n        "hypothesis.optional_evidence"\n        in prompt\n    )\n\n    assert (\n        "optional_evidence may remain non-empty"\n        in prompt\n    )\n\n\ndef test_reasoner_prompt_contains_point_sample_temporal_causality_contract():\n    value = scope()\n\n    prompt = (\n        LLMInvestigationReasoner\n        ._build_prompt(\n            scope=value,\n            state=InvestigationState(\n                scope=value\n            ),\n        )\n    )\n\n    assert (\n        "OOMKilled proves that an OOM termination occurred"\n        in prompt\n    )\n\n    assert (\n        "does not by itself prove that a configured container memory limit was exceeded"\n        in prompt\n    )\n\n    assert (\n        "point-in-time or sampled metric cannot establish an unobserved transient peak"\n        in prompt\n    )\n\n    assert (\n        "sample is not positive support for the claim that the limit was exceeded"\n        in prompt\n    )\n\n\ndef test_benchmark_logical_clock_does_not_follow_wall_clock():\n    clock = _BenchmarkMonotonicClock(\n        step_seconds=0.001\n    )\n\n    values = [\n        clock()\n        for _ in range(\n            6\n        )\n    ]\n\n    assert values == [\n        0.0,\n        0.001,\n        0.002,\n        0.003,\n        0.004,\n        0.005,\n    ]\n\n\ndef test_benchmark_trace_schema_accepts_optional_evidence_state():\n    scenario = BenchmarkScenario(\n        key="optional-trace",\n        title="optional-trace",\n        alert_name="PodRestartHigh",\n        alert_message="restart",\n        evidence_by_probe={},\n        hidden_expected_stop_reason=(\n            InvestigationStopReason.INSUFFICIENT_EVIDENCE\n        ),\n    )\n\n    decision = InvestigationDecision(\n        hypotheses=[\n            hypothesis(\n                cause="unresolved startup failure",\n                confidence=0.4,\n                missing=[\n                    "previous container logs"\n                ],\n                optional=[\n                    "restart count for frequency only"\n                ],\n            )\n        ],\n        rationale_summary=(\n            "causal evidence remains unavailable"\n        ),\n        stop=True,\n        stop_reason=(\n            InvestigationStopReason.INSUFFICIENT_EVIDENCE\n        ),\n    )\n\n    state = InvestigationState(\n        status=(\n            InvestigationStatus.CONCLUDED\n        ),\n        scope=scope(),\n        stop_reason=(\n            InvestigationStopReason.INSUFFICIENT_EVIDENCE\n        ),\n    )\n\n    score = score_scenario(\n        scenario=scenario,\n        state=state,\n        decisions=[\n            decision\n        ],\n    )\n\n    assert (\n        score.decision_trace[\n            0\n        ][\n            "hypotheses"\n        ][\n            0\n        ][\n            "optional_evidence"\n        ]\n        == [\n            "restart count for frequency only"\n        ]\n    )\n'


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

    models_file = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "investigation"
        / "models.py"
    )

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

    engine_file = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "evaluation"
        / "intelligence_benchmark"
        / "engine.py"
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
        / "test_investigation_control_semantics.py"
    )

    sources = {
        models_file: MODELS_SOURCE,
        reasoner_file: REASONER_SOURCE,
        guard_file: GUARD_SOURCE,
        engine_file: BENCHMARK_ENGINE_SOURCE,
        runner_file: BENCHMARK_RUNNER_SOURCE,
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
        "Investigation Control Semantics v1",
        f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
        "",
        "Full Benchmark findings addressed:",
        "- cumulative provider/network latency must not consume Intelligence Benchmark wall-clock budget",
        "- one new Logs probe requires a five-tool hard ceiling while efficiency scoring stays unchanged",
        "- blocking root-cause evidence must be distinct from optional corroboration",
        "- point/sampled metrics must not be promoted into unobserved historical threshold crossings",
        "",
        "Changes:",
        "- IncidentHypothesis adds optional_evidence",
        "- missing_evidence remains RCA-blocking",
        "- optional_evidence is explicitly non-blocking in EpistemicConclusionGuard",
        "- Reasoner receives blocking-vs-optional evidence instructions",
        "- Reasoner receives event-vs-mechanism and point-sample temporal causality instructions",
        "- Intelligence Benchmark uses deterministic logical monotonic time",
        "- Benchmark runner hard limits become max_iterations=6 and max_tool_calls=5",
        "- per-scenario hidden efficiency budgets are NOT changed",
        "- production InvestigationLimits defaults and upper bounds are NOT changed",
        "- Benchmark decision trace exposes optional_evidence",
        "",
        "No Tool, Kubernetes, Prometheus, Action, Approval or Verification capability is added.",
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
            name="Control Semantics focused regression suite",
            command=[
                "uv",
                "run",
                "pytest",
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
                    "test_investigation_models.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_reasoner.py"
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
                "Control Semantics focused tests failed"
            )

        compatibility = run_command(
            root=root,
            name="Investigation compatibility regression suite",
            command=[
                "uv",
                "run",
                "pytest",
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
                    "test_investigation_production_tool_contract.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_factory.py"
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
            compatibility,
        )

        if compatibility.returncode != 0:
            raise RuntimeError(
                "Investigation compatibility tests failed"
            )

        model_preflight = run_command(
            root=root,
            name="Control semantics model/prompt preflight",
            command=[
                "uv",
                "run",
                "python",
                "-c",
                (
                    "from services.agent_runtime.app.investigation.models "
                    "import IncidentHypothesis,InvestigationLimits,"
                    "InvestigationScope,InvestigationState; "
                    "from services.agent_runtime.app.investigation.reasoner "
                    "import LLMInvestigationReasoner; "
                    "h=IncidentHypothesis(hypothesis_id='h1',cause='c',confidence=.5,"
                    "optional_evidence=['restart count']); "
                    "s=InvestigationScope(alert_name='A',resource='r'); "
                    "p=LLMInvestigationReasoner._build_prompt("
                    "scope=s,state=InvestigationState(scope=s)); "
                    "d=InvestigationLimits(); "
                    "print('optional='+str(h.optional_evidence)); "
                    "print('defaults='+str((d.max_iterations,d.max_tool_calls,d.timeout_seconds))); "
                    "print('temporal_rule='+str('point-in-time or sampled metric' in p)); "
                    "print('optional_rule='+str('hypothesis.optional_evidence' in p))"
                ),
            ],
        )

        add_command(
            report,
            model_preflight,
        )

        if model_preflight.returncode != 0:
            raise RuntimeError(
                "Control semantics model/prompt preflight failed"
            )

        runner_preflight = run_command(
            root=root,
            name="Benchmark budget separation preflight",
            command=[
                "uv",
                "run",
                "python",
                "-c",
                (
                    "from pathlib import Path; "
                    "r=Path(r'scripts/dev/run_investigation_intelligence_benchmark_v1.py')"
                    ".read_text(encoding='utf-8'); "
                    "e=Path(r'services/agent_runtime/app/evaluation/"
                    "intelligence_benchmark/engine.py').read_text(encoding='utf-8'); "
                    "print('runner_iterations='+str('max_iterations=6' in r)); "
                    "print('runner_tools='+str('max_tool_calls=5' in r)); "
                    "print('runner_timeout_unchanged='+str('timeout_seconds=60' in r)); "
                    "print('logical_clock='+str('_BenchmarkMonotonicClock' in e)); "
                    "assert 'max_iterations=6' in r; "
                    "assert 'max_tool_calls=5' in r; "
                    "assert 'timeout_seconds=60' in r; "
                    "assert '_BenchmarkMonotonicClock' in e"
                ),
            ],
        )

        add_command(
            report,
            runner_preflight,
        )

        if runner_preflight.returncode != 0:
            raise RuntimeError(
                "Benchmark budget separation preflight failed"
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
                    "Path(r'services/agent_runtime/app/investigation/models.py'),"
                    "Path(r'services/agent_runtime/app/investigation/reasoner.py'),"
                    "Path(r'services/agent_runtime/app/investigation/epistemic_guard.py')"
                    "]; "
                    "s='\\n'.join(x.read_text(encoding='utf-8') for x in files); "
                    "bad=[x for x in ['ActionRuntime','ApprovalService',"
                    "'VerificationRuntime','kubectl','create_llm_gateway'] if x in s]; "
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
                "Control semantics authority boundary failed"
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
                "Control Semantics v1 is installed.",
                "",
                "Expected behavioral changes:",
                "- direct causal Logs may conclude even when optional corroboration remains",
                "- genuinely blocking missing evidence still prevents RCA",
                "- sampled metrics cannot be treated as proof of unseen threshold crossings",
                "- Intelligence Benchmark no longer fails only because prior LLM calls consumed cumulative wall time",
                "- all five current symbolic probes can fit inside Benchmark hard tool ceiling",
                "- inefficient five-probe paths are still penalized by scenario scoring",
                "",
                "Next acceptance:",
                "rerun Bailian full benchmark; if stable, repeat it three times before adding Change Investigation.",
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
            "INVESTIGATION CONTROL SEMANTICS V1 PASSED"
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
                    "Investigation Control Semantics v1 FAILED",
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
            "INVESTIGATION CONTROL SEMANTICS V1 FAILED"
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
