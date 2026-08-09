from __future__ import annotations

import hashlib
import shutil
import subprocess
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


VERSION = "investigation-final-synthesis-budget-discipline-v1"

AFTER_NAME = (
    "investigation_final_synthesis_budget_discipline_v1_after.txt"
)

ERROR_NAME = (
    "investigation_final_synthesis_budget_discipline_v1_error.txt"
)

EXPECTED_HASHES = {'services/agent_runtime/app/investigation/reasoner.py': 'f2ffa87042a6e9ce7d27fbedc72e739d88a7f777d22b33e4cfbca30ae9cea43d'}

REASONER_SOURCE = 'import json\nfrom abc import ABC, abstractmethod\n\nfrom pydantic import ValidationError\n\nfrom services.agent_runtime.app.investigation.llm_gateway_adapter import (\n    BaseInvestigationLLM,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    InvestigationDecision,\n    InvestigationProbe,\n    InvestigationScope,\n    InvestigationState,\n)\n\n\nclass InvestigationReasonerError(RuntimeError):\n    """\n    Sanitized reasoner failure.\n    """\n\n\nclass InvestigationReasonerJSONError(\n    InvestigationReasonerError\n):\n    """\n    Primary decision was not valid JSON.\n    """\n\n\nclass InvestigationReasonerValidationError(\n    InvestigationReasonerError\n):\n    """\n    Primary JSON did not satisfy InvestigationDecision.\n    """\n\n\nclass InvestigationReasonerRepairJSONError(\n    InvestigationReasonerError\n):\n    """\n    One bounded repair attempt still did not return valid JSON.\n    """\n\n\nclass InvestigationReasonerRepairValidationError(\n    InvestigationReasonerError\n):\n    """\n    One bounded repair attempt still violated InvestigationDecision.\n    """\n\n\nclass InvestigationReasonerExecutionRetryError(\n    InvestigationReasonerError\n):\n    """\n    The sanitized LLM execution failed twice for the same reasoning request.\n    """\n\n\nclass BaseInvestigationReasoner(ABC):\n    """\n    Select the next symbolic read-only probe or stop with a conclusion.\n    """\n\n    @abstractmethod\n    async def decide(\n        self,\n        scope: InvestigationScope,\n        state: InvestigationState,\n    ) -> InvestigationDecision:\n        ...\n\n\nclass LLMInvestigationReasoner(\n    BaseInvestigationReasoner\n):\n    """\n    Structured LLM reasoner for the bounded InvestigationCoordinator.\n\n    The reasoner depends only on the Investigation-owned LLM abstraction.\n    Gateway routing, provider selection, fallback, rate limiting and circuit\n    breaking remain outside this class.\n\n    Transport execution retry ownership remains entirely in the shared\n    LLM Gateway. The Reasoner does not repeat a failed Gateway request.\n    Its only bounded second model call is structured Decision-contract repair\n    after a model response was successfully received but failed validation.\n\n    It can select only an InvestigationProbe enum value. It cannot construct\n    tool calls, resource scope, PromQL, URLs or credentials.\n    """\n\n    _SYSTEM_PROMPT = (\n        "You are a bounded SRE investigation reasoner. "\n        "Maintain competing hypotheses, use only supplied "\n        "evidence, and select only one allowed symbolic "\n        "read-only probe. Never propose or execute a write."\n    )\n\n    def __init__(\n        self,\n        investigation_llm: BaseInvestigationLLM,\n    ) -> None:\n        if not isinstance(\n            investigation_llm,\n            BaseInvestigationLLM,\n        ):\n            raise TypeError(\n                "Investigation LLM adapter is invalid"\n            )\n\n        self.investigation_llm = (\n            investigation_llm\n        )\n\n    async def decide(\n        self,\n        scope: InvestigationScope,\n        state: InvestigationState,\n    ) -> InvestigationDecision:\n        prompt = self._build_prompt(\n            scope=scope,\n            state=state,\n        )\n\n        content = await self.investigation_llm.complete(\n            system_prompt=self._SYSTEM_PROMPT,\n            prompt=prompt,\n        )\n\n        if not isinstance(\n            content,\n            str,\n        ):\n            raise InvestigationReasonerError(\n                "Investigation reasoner returned no JSON"\n            )\n\n        try:\n            decision = self._parse_decision(\n                content,\n                repair=False,\n            )\n\n            self._validate_decision_against_state(\n                decision=decision,\n                state=state,\n                repair=False,\n            )\n\n            return decision\n\n        except (\n            InvestigationReasonerJSONError,\n            InvestigationReasonerValidationError,\n        ) as primary_error:\n            repair_content = await self.investigation_llm.complete(\n                system_prompt=(\n                    self._SYSTEM_PROMPT\n                    + " Repair the decision contract only; "\n                    "do not invent new evidence."\n                ),\n                prompt=self._build_repair_prompt(\n                    scope=scope,\n                    state=state,\n                    primary_error=primary_error,\n                ),\n            )\n\n            if not isinstance(\n                repair_content,\n                str,\n            ):\n                raise InvestigationReasonerError(\n                    "Investigation reasoner repair returned no JSON"\n                ) from primary_error\n\n            try:\n                decision = self._parse_decision(\n                    repair_content,\n                    repair=True,\n                )\n\n                self._validate_decision_against_state(\n                    decision=decision,\n                    state=state,\n                    repair=True,\n                )\n\n                return decision\n\n            except InvestigationReasonerError as repair_error:\n                raise repair_error from primary_error\n\n    @staticmethod\n    def _validate_decision_against_state(\n        *,\n        decision: InvestigationDecision,\n        state: InvestigationState,\n        repair: bool,\n    ) -> None:\n        probe = decision.next_probe\n\n        remaining_tool_calls = max(\n            0,\n            (\n                state.limits.max_tool_calls\n                - state.tool_call_count\n            ),\n        )\n\n        remaining_reasoning_iterations = max(\n            0,\n            (\n                state.limits.max_iterations\n                - state.iteration_count\n            ),\n        )\n\n        if (\n            not decision.stop\n            and (\n                remaining_tool_calls <= 0\n                or remaining_reasoning_iterations <= 1\n            )\n        ):\n            error_type = (\n                InvestigationReasonerRepairValidationError\n                if repair\n                else InvestigationReasonerValidationError\n            )\n\n            raise error_type(\n                "Investigation reasoner must return a terminal decision "\n                "because no safe probe-plus-final-synthesis budget remains"\n            )\n\n        if (\n            probe is not None\n            and probe in state.attempted_probes\n        ):\n            error_type = (\n                InvestigationReasonerRepairValidationError\n                if repair\n                else InvestigationReasonerValidationError\n            )\n\n            raise error_type(\n                "Investigation reasoner selected an already-attempted probe"\n            )\n\n    @staticmethod\n    def _parse_decision(\n        content: str,\n        *,\n        repair: bool,\n    ) -> InvestigationDecision:\n        try:\n            payload = json.loads(\n                content\n            )\n\n        except json.JSONDecodeError as exc:\n            error_type = (\n                InvestigationReasonerRepairJSONError\n                if repair\n                else InvestigationReasonerJSONError\n            )\n\n            raise error_type(\n                (\n                    "Investigation reasoner repair returned invalid JSON"\n                    if repair\n                    else "Investigation reasoner returned invalid JSON"\n                )\n            ) from exc\n\n        try:\n            return InvestigationDecision.model_validate(\n                payload\n            )\n\n        except (\n            ValidationError,\n            TypeError,\n            ValueError,\n        ) as exc:\n            error_type = (\n                InvestigationReasonerRepairValidationError\n                if repair\n                else InvestigationReasonerValidationError\n            )\n\n            raise error_type(\n                (\n                    "Investigation reasoner repair returned an invalid decision"\n                    if repair\n                    else "Investigation reasoner returned an invalid decision"\n                )\n            ) from exc\n\n    @classmethod\n    def _build_repair_prompt(\n        cls,\n        *,\n        scope: InvestigationScope,\n        state: InvestigationState,\n        primary_error: InvestigationReasonerError,\n    ) -> str:\n        failure_kind = type(\n            primary_error\n        ).__name__\n\n        return (\n            "Your previous decision failed the bounded structured-output "\n            f"contract with failure type {failure_kind}.\\n"\n            "Do not repeat or explain the invalid response.\\n"\n            "Re-evaluate the SAME supplied state. Do not invent evidence, "\n            "do not add a tool call outside allowed_probes, and do not "\n            "change resource scope.\\n"\n            "Return exactly one corrected JSON decision that satisfies every "\n            "shape and evidence rule below.\\n\\n"\n            + cls._build_prompt(\n                scope=scope,\n                state=state,\n            )\n        )\n\n    @staticmethod\n    def _build_prompt(\n        scope: InvestigationScope,\n        state: InvestigationState,\n    ) -> str:\n        trusted_evidence_ids = [\n            item.evidence_id\n            for item in state.evidence\n            if (\n                item.success\n                and item.trusted\n                and item.production_signal\n            )\n        ]\n\n        attempted_probe_set = set(\n            state.attempted_probes\n        )\n\n        remaining_tool_calls = max(\n            0,\n            (\n                state.limits.max_tool_calls\n                - state.tool_call_count\n            ),\n        )\n\n        remaining_reasoning_iterations = max(\n            0,\n            (\n                state.limits.max_iterations\n                - state.iteration_count\n            ),\n        )\n\n        continuation_allowed = (\n            remaining_tool_calls > 0\n            and remaining_reasoning_iterations > 1\n        )\n\n        state_payload = {\n            "scope": scope.model_dump(\n                mode="json"\n            ),\n            "iteration_count": state.iteration_count,\n            "max_iterations": state.limits.max_iterations,\n            "remaining_reasoning_iterations": (\n                remaining_reasoning_iterations\n            ),\n            "tool_call_count": state.tool_call_count,\n            "max_tool_calls": state.limits.max_tool_calls,\n            "remaining_tool_calls": remaining_tool_calls,\n            "continuation_allowed": continuation_allowed,\n            "attempted_probes": [\n                probe.value\n                for probe in state.attempted_probes\n            ],\n            "failed_probes": [\n                item.probe.value\n                for item in state.evidence\n                if not item.success\n            ],\n            "hypotheses": [\n                item.model_dump(mode="json")\n                for item in state.hypotheses\n            ],\n            "evidence": [\n                item.model_dump(mode="json")\n                for item in state.evidence\n            ],\n            "trusted_evidence_ids": trusted_evidence_ids,\n            "allowed_probes": [\n                probe.value\n                for probe in state.available_probes\n                if probe not in attempted_probe_set\n            ],\n        }\n\n        return (\n            "Investigate the incident using the bounded state below.\\n"\n            "Return exactly one JSON object only. Do not return markdown.\\n"\n            "Maintain competing hypotheses and update confidence from evidence.\\n"\n            "Probe affordances:\\n"\n            "- kubernetes_pod_state: current pod/container state, restart "\n            "indicators, and last termination reasons.\\n"\n            "- kubernetes_previous_container_logs: bounded previous-container "\n            "output; high-information evidence for unexplained restart, startup, "\n            "panic, configuration, dependency, or crash symptoms.\\n"\n            "- kubernetes_workload_change: bounded trusted Deployment-owner "\n            "change context for a Pod, including current/previous rollout "\n            "revision when available, image-before/image-after, rollout time, "\n            "generation/observedGeneration and replica status. It is temporal "\n            "change evidence, not by itself proof that the change caused the "\n            "incident.\\n"\n            "- prometheus_memory_working_set: sampled container memory usage.\\n"\n            "- prometheus_memory_limit: configured container memory limit.\\n"\n            "- prometheus_restart_count: sampled restart frequency/corroboration.\\n"\n            "If trusted evidence falsifies the current leading hypothesis but "\n            "the observed incident symptom remains unexplained, do not stop "\n            "solely because that hypothesis was rejected. Replan with at least "\n            "one evidence-plausible alternative hypothesis when an unattempted "\n            "allowed probe can materially discriminate plausible causes.\\n"\n            "Use insufficient_evidence only when no unattempted safe probe can "\n            "materially discriminate the remaining plausible causes, or when "\n            "required evidence is unavailable.\\n"\n            "State.allowed_probes already excludes every attempted probe. "\n            "Select next_probe only from State.allowed_probes.\\n"\n            "Budget discipline is mandatory. State.remaining_tool_calls is the "\n            "number of additional read-only probes that may still execute. "\n            "State.remaining_reasoning_iterations counts this decision and any "\n            "future synthesis decisions. A continuing decision consumes the "\n            "current reasoning iteration and requires at least one later "\n            "reasoning iteration to interpret the new evidence. Therefore, if "\n            "State.continuation_allowed is false, you MUST return a terminal "\n            "decision now with next_probe=null. Do not request one more probe "\n            "when there is no probe-plus-final-synthesis budget remaining.\\n"\n            "Do not spend the final useful budget on evidence that only "\n            "corroborates frequency, severity, or a symptom already established "\n            "when it cannot resolve required root-cause mechanism evidence or "\n            "materially falsify a competing hypothesis. For example, restart "\n            "count is corroborative and does not establish why a CrashLoop or "\n            "OOM occurred.\\n"\n            "A failed probe is still an attempted probe. Do not retry it inside "\n            "the same investigation; keep its required evidence missing and "\n            "use another unattempted discriminative probe or safely abstain.\\n"\n            "Never repeat a probe already listed in attempted_probes.\\n"\n            "Never cite an evidence ID that is absent from State.evidence.\\n"\n            "Conflicting evidence can weaken a hypothesis but cannot by itself "\n            "positively establish an unrelated root cause.\\n"\n            "Ruling out one hypothesis is not sufficient evidence for a different "\n            "causal claim.\\n"\n            "A recent rollout, revision change, image change, or replica state "\n            "is temporal/correlation evidence. Do not claim that a workload "\n            "change CAUSED the incident from change evidence alone. Pair change "\n            "evidence with independent symptom or mechanism evidence such as "\n            "logs, termination state, or relevant metrics before accepting a "\n            "change-caused root cause.\\n"\n            "Current-state evidence does not by itself prove that a historical "\n            "event never occurred.\\n"\n            "For sufficient_evidence, the conclusion must affirm a positively "\n            "supported current hypothesis. Every conclusion.evidence_id must also "\n            "appear in that hypothesis supporting_evidence_ids and must not be "\n            "conflicting evidence for that same hypothesis.\\n"\n            "Conclusion confidence must not materially exceed the confidence of "\n            "the positively supported hypothesis.\\n"\n            "A symptom or failure-mode observation such as CrashLoopBackOff, "\n            "restart count, unready state, high latency, or high error rate can "\n            "confirm that a failure exists, but does not by itself establish the "\n            "specific underlying cause that produced it.\\n"\n            "If several underlying causes remain plausible and current allowed "\n            "probes cannot discriminate among them, keep the required "\n            "root-cause evidence in hypothesis.missing_evidence and stop with "\n            "insufficient_evidence or no_safe_probe.\\n"\n            "Use hypothesis.missing_evidence only for evidence that is REQUIRED "\n            "before the specific root cause can be accepted. Use "\n            "hypothesis.optional_evidence for corroboration that may increase "\n            "confidence or describe frequency/severity but is not required to "\n            "establish the root cause.\\n"\n            "Do not put the same evidence need in both missing_evidence and "\n            "optional_evidence.\\n"\n            "Do not clear missing_evidence merely because all allowed probes "\n            "have been attempted. For sufficient_evidence, the positively "\n            "supported hypothesis used by the conclusion must have an empty "\n            "missing_evidence list. optional_evidence may remain non-empty.\\n"\n            "Treat event evidence separately from mechanism evidence. For example, "\n            "OOMKilled proves that an OOM termination occurred, but does not by "\n            "itself prove that a configured container memory limit was exceeded.\\n"\n            "A point-in-time or sampled metric cannot establish an unobserved "\n            "transient peak, historical trend, or threshold crossing. Never invent "\n            "an unseen spike to make a hypothesis fit.\\n"\n            "For quantitative threshold causes, supporting evidence must be "\n            "directionally consistent with the claimed mechanism. If a sampled "\n            "working value is below the sampled limit, that sample is not positive "\n            "support for the claim that the limit was exceeded.\\n"\n            "If an event is confirmed but the available sampled metrics do not "\n            "explain its mechanism, keep the required historical/range/peak "\n            "evidence in missing_evidence and stop with insufficient_evidence "\n            "unless another direct causal observation establishes the cause.\\n"\n            "If the available evidence only rejects hypotheses and does not "\n            "positively establish a root cause, stop with insufficient_evidence.\\n"\n            "If the available evidence only rejects hypotheses or confirms a "\n            "symptom/failure mode without establishing its cause, stop with "\n            "insufficient_evidence.\\n"\n            "For a conclusion, evidence_ids must be non-empty and copied "\n            "exactly from trusted_evidence_ids.\\n"\n            "Allowed model terminal stop_reason values are exactly: "\n            "sufficient_evidence, insufficient_evidence, no_safe_probe.\\n"\n            "Do not emit internal coordinator reasons such as timeout, "\n            "max_iterations, max_tool_calls, duplicate_probe or reasoner_error.\\n"\n            "\\n"\n            "Continuing shape:\\n"\n            "{\\n"\n            \'  "hypotheses": [{"hypothesis_id": "h1", "cause": "candidate", \'\n            \'"confidence": 0.5, "supporting_evidence_ids": [], \'\n            \'"conflicting_evidence_ids": [], "missing_evidence": ["required evidence"], "optional_evidence": ["non-blocking corroboration"]}],\\n\'\n            \'  "rationale_summary": "why this probe is most discriminative",\\n\'\n            \'  "stop": false,\\n\'\n            \'  "stop_reason": null,\\n\'\n            \'  "next_probe": "one unattempted value from allowed_probes",\\n\'\n            \'  "conclusion": null\\n\'\n            "}\\n"\n            "\\n"\n            "Terminal sufficient-evidence shape:\\n"\n            "{\\n"\n            \'  "hypotheses": [{"hypothesis_id": "h1", "cause": "supported cause", \'\n            \'"confidence": 0.9, "supporting_evidence_ids": ["copy exact trusted id"], \'\n            \'"conflicting_evidence_ids": [], "missing_evidence": [], "optional_evidence": []}],\\n\'\n            \'  "rationale_summary": "why trusted evidence is sufficient",\\n\'\n            \'  "stop": true,\\n\'\n            \'  "stop_reason": "sufficient_evidence",\\n\'\n            \'  "next_probe": null,\\n\'\n            \'  "conclusion": {"root_cause": "bounded root cause", \'\n            \'"confidence": 0.9, "evidence_ids": ["copy exact trusted id"], \'\n            \'"remaining_uncertainties": []}\\n\'\n            "}\\n"\n            "\\n"\n            "Terminal insufficient/no-safe-probe shape:\\n"\n            "{\\n"\n            \'  "hypotheses": [{"hypothesis_id": "h1", "cause": "candidate", \'\n            \'"confidence": 0.3, "supporting_evidence_ids": [], \'\n            \'"conflicting_evidence_ids": [], "missing_evidence": ["required missing evidence"], "optional_evidence": ["non-blocking evidence if useful"]}],\\n\'\n            \'  "rationale_summary": "why current bounded evidence cannot support an RCA",\\n\'\n            \'  "stop": true,\\n\'\n            \'  "stop_reason": "insufficient_evidence",\\n\'\n            \'  "next_probe": null,\\n\'\n            \'  "conclusion": null\\n\'\n            "}\\n"\n            "\\n"\n            "If no useful unattempted allowed probe can resolve the remaining "\n            "uncertainty, stop with insufficient_evidence or no_safe_probe.\\n"\n            "State:\\n"\n            f"{json.dumps(state_payload, ensure_ascii=True, sort_keys=True)}"\n        )\n\n\n__all__ = [\n    "BaseInvestigationReasoner",\n    "InvestigationReasonerError",\n    "LLMInvestigationReasoner",\n]\n'
TEST_SOURCE = 'from __future__ import annotations\n\nimport json\n\nimport pytest\n\nfrom services.agent_runtime.app.investigation.llm_gateway_adapter import (\n    BaseInvestigationLLM,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    InvestigationLimits,\n    InvestigationProbe,\n    InvestigationScope,\n    InvestigationState,\n    InvestigationStopReason,\n)\nfrom services.agent_runtime.app.investigation.reasoner import (\n    LLMInvestigationReasoner,\n)\n\n\nclass SequenceLLM(\n    BaseInvestigationLLM\n):\n    def __init__(\n        self,\n        values,\n    ) -> None:\n        self.values = list(\n            values\n        )\n        self.calls = []\n\n    async def complete(\n        self,\n        *,\n        system_prompt: str,\n        prompt: str,\n    ) -> str:\n        self.calls.append(\n            {\n                "system_prompt": system_prompt,\n                "prompt": prompt,\n            }\n        )\n\n        return self.values.pop(\n            0\n        )\n\n\ndef scope() -> InvestigationScope:\n    return InvestigationScope(\n        alert_name="PodRestartHigh",\n        alert_message="payment-api is restarting",\n        resource="payment-api",\n        namespace="payment",\n        cluster="benchmark-lab",\n    )\n\n\ndef continue_decision(\n    probe: str,\n) -> str:\n    return json.dumps(\n        {\n            "hypotheses": [\n                {\n                    "hypothesis_id": "h1",\n                    "cause": "unresolved restart mechanism",\n                    "confidence": 0.4,\n                    "supporting_evidence_ids": [],\n                    "conflicting_evidence_ids": [],\n                    "missing_evidence": [\n                        "direct root-cause mechanism evidence"\n                    ],\n                    "optional_evidence": [\n                        "restart frequency"\n                    ],\n                }\n            ],\n            "rationale_summary": (\n                "collect one more corroborative probe"\n            ),\n            "stop": False,\n            "stop_reason": None,\n            "next_probe": probe,\n            "conclusion": None,\n        }\n    )\n\n\ndef abstain_decision() -> str:\n    return json.dumps(\n        {\n            "hypotheses": [\n                {\n                    "hypothesis_id": "h1",\n                    "cause": "unresolved restart mechanism",\n                    "confidence": 0.3,\n                    "supporting_evidence_ids": [],\n                    "conflicting_evidence_ids": [],\n                    "missing_evidence": [\n                        "direct root-cause mechanism evidence"\n                    ],\n                    "optional_evidence": [\n                        "restart frequency"\n                    ],\n                }\n            ],\n            "rationale_summary": (\n                "bounded evidence cannot establish the mechanism"\n            ),\n            "stop": True,\n            "stop_reason": (\n                InvestigationStopReason\n                .INSUFFICIENT_EVIDENCE\n                .value\n            ),\n            "next_probe": None,\n            "conclusion": None,\n        }\n    )\n\n\ndef budget_exhausted_state() -> InvestigationState:\n    available = [\n        InvestigationProbe.KUBERNETES_POD_STATE,\n        (\n            InvestigationProbe\n            .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n        ),\n        (\n            InvestigationProbe\n            .KUBERNETES_WORKLOAD_CHANGE\n        ),\n        (\n            InvestigationProbe\n            .PROMETHEUS_MEMORY_WORKING_SET\n        ),\n        (\n            InvestigationProbe\n            .PROMETHEUS_MEMORY_LIMIT\n        ),\n        (\n            InvestigationProbe\n            .PROMETHEUS_RESTART_COUNT\n        ),\n    ]\n\n    return InvestigationState(\n        scope=scope(),\n        limits=InvestigationLimits(\n            max_iterations=6,\n            max_tool_calls=5,\n        ),\n        iteration_count=5,\n        tool_call_count=5,\n        available_probes=available,\n        attempted_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n            (\n                InvestigationProbe\n                .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n            ),\n            (\n                InvestigationProbe\n                .KUBERNETES_WORKLOAD_CHANGE\n            ),\n            (\n                InvestigationProbe\n                .PROMETHEUS_MEMORY_WORKING_SET\n            ),\n            (\n                InvestigationProbe\n                .PROMETHEUS_MEMORY_LIMIT\n            ),\n        ],\n    )\n\n\ndef test_prompt_exposes_explicit_terminal_budget_state():\n    state = budget_exhausted_state()\n\n    prompt = (\n        LLMInvestigationReasoner\n        ._build_prompt(\n            scope=state.scope,\n            state=state,\n        )\n    )\n\n    assert (\n        \'"remaining_tool_calls": 0\'\n        in prompt\n    )\n\n    assert (\n        \'"remaining_reasoning_iterations": 1\'\n        in prompt\n    )\n\n    assert (\n        \'"continuation_allowed": false\'\n        in prompt\n    )\n\n    assert (\n        "you MUST return a terminal decision"\n        in prompt\n    )\n\n    assert (\n        "restart count is corroborative"\n        in prompt\n    )\n\n\n@pytest.mark.asyncio\nasync def test_no_budget_continue_is_repaired_into_terminal_abstention():\n    state = budget_exhausted_state()\n\n    llm = SequenceLLM(\n        [\n            continue_decision(\n                (\n                    InvestigationProbe\n                    .PROMETHEUS_RESTART_COUNT\n                    .value\n                )\n            ),\n            abstain_decision(),\n        ]\n    )\n\n    reasoner = LLMInvestigationReasoner(\n        llm\n    )\n\n    decision = await reasoner.decide(\n        state.scope,\n        state,\n    )\n\n    assert len(\n        llm.calls\n    ) == 2\n\n    assert decision.stop is True\n\n    assert (\n        decision.stop_reason\n        == InvestigationStopReason.INSUFFICIENT_EVIDENCE\n    )\n\n    assert decision.next_probe is None\n\n\n@pytest.mark.asyncio\nasync def test_last_reasoning_iteration_is_reserved_for_synthesis():\n    state = InvestigationState(\n        scope=scope(),\n        limits=InvestigationLimits(\n            max_iterations=6,\n            max_tool_calls=10,\n        ),\n        iteration_count=5,\n        tool_call_count=2,\n        available_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n            (\n                InvestigationProbe\n                .PROMETHEUS_RESTART_COUNT\n            ),\n        ],\n        attempted_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n        ],\n    )\n\n    llm = SequenceLLM(\n        [\n            continue_decision(\n                (\n                    InvestigationProbe\n                    .PROMETHEUS_RESTART_COUNT\n                    .value\n                )\n            ),\n            abstain_decision(),\n        ]\n    )\n\n    reasoner = LLMInvestigationReasoner(\n        llm\n    )\n\n    decision = await reasoner.decide(\n        state.scope,\n        state,\n    )\n\n    assert decision.stop is True\n    assert decision.next_probe is None\n\n\n@pytest.mark.asyncio\nasync def test_continue_remains_valid_when_probe_and_synthesis_budget_exist():\n    state = InvestigationState(\n        scope=scope(),\n        limits=InvestigationLimits(\n            max_iterations=6,\n            max_tool_calls=5,\n        ),\n        iteration_count=2,\n        tool_call_count=2,\n        available_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n            (\n                InvestigationProbe\n                .PROMETHEUS_RESTART_COUNT\n            ),\n        ],\n        attempted_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n        ],\n    )\n\n    llm = SequenceLLM(\n        [\n            continue_decision(\n                (\n                    InvestigationProbe\n                    .PROMETHEUS_RESTART_COUNT\n                    .value\n                )\n            ),\n        ]\n    )\n\n    reasoner = LLMInvestigationReasoner(\n        llm\n    )\n\n    decision = await reasoner.decide(\n        state.scope,\n        state,\n    )\n\n    assert len(\n        llm.calls\n    ) == 1\n\n    assert decision.stop is False\n\n    assert (\n        decision.next_probe\n        == InvestigationProbe.PROMETHEUS_RESTART_COUNT\n    )\n'


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
        "Repository root not found."
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
                "Refusing stale budget-discipline installation."
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

    test_file = (
        root
        / "services"
        / "agent_runtime"
        / "tests"
        / "test_investigation_final_synthesis_budget_discipline.py"
    )

    sources = {
        reasoner_file: REASONER_SOURCE,
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
        "Investigation Final-Synthesis Budget Discipline v1",
        f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
        "",
        "Observed Change Benchmark result:",
        "- 15 scenario runs",
        "- 9/15 correct outcomes",
        "- five of six incorrect outcomes ended in max_tool_calls",
        "- no unsupported Change RCA was ultimately accepted",
        "",
        "Change:",
        "- expose max/remaining tool-call budget to the Reasoner",
        "- expose max/remaining reasoning-iteration budget to the Reasoner",
        "- reserve one final reasoning iteration for synthesis",
        "- continuing decisions are contract-invalid when no probe-plus-synthesis budget remains",
        "- existing bounded Decision Repair may convert such a decision into a terminal result",
        "- no Tool budget is increased",
        "- no iteration budget is increased",
        "- no additional probe is executed by the repair",
        "- corroborative-only evidence must not consume the final useful budget when it cannot resolve root-cause mechanism",
        "",
        "Unchanged:",
        "- Change Tool and Kubernetes read-only boundary",
        "- Epistemic Guard",
        "- LLM Gateway transport policy",
        "- Action / Approval / Verification",
        "- Benchmark hidden labels",
        "",
        "Installer sends no external LLM/Kubernetes/Prometheus request.",
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

        for path, source in sources.items():
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
                    for path in targets
                ],
            ],
        )

        add_command(
            report,
            syntax,
        )

        if syntax.returncode != 0:
            raise RuntimeError(
                "Budget discipline syntax verification failed"
            )

        focused = run_command(
            root=root,
            name="Final-synthesis budget focused suite",
            command=[
                "uv",
                "run",
                "pytest",
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_final_synthesis_budget_discipline.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_reasoner.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_decision_robustness.py"
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
            focused,
        )

        if focused.returncode != 0:
            raise RuntimeError(
                "Final-synthesis budget focused tests failed"
            )

        compatibility = run_command(
            root=root,
            name="Investigation / Change compatibility suite",
            command=[
                "uv",
                "run",
                "pytest",
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_coordinator.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_change_capability.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_change_intelligence_benchmark.py"
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
            compatibility,
        )

        if compatibility.returncode != 0:
            raise RuntimeError(
                "Investigation / Change compatibility tests failed"
            )

        preflight = run_command(
            root=root,
            name="Budget semantics preflight",
            command=[
                "uv",
                "run",
                "python",
                "-c",
                (
                    "from pathlib import Path; "
                    "s=Path(r'services/agent_runtime/app/investigation/reasoner.py')"
                    ".read_text(encoding='utf-8'); "
                    "print('remaining_tool_calls='+str('remaining_tool_calls' in s)); "
                    "print('remaining_reasoning_iterations='+str('remaining_reasoning_iterations' in s)); "
                    "print('continuation_allowed='+str('continuation_allowed' in s)); "
                    "assert 'remaining_tool_calls' in s; "
                    "assert 'remaining_reasoning_iterations' in s; "
                    "assert 'continuation_allowed' in s"
                ),
            ],
        )

        add_command(
            report,
            preflight,
        )

        if preflight.returncode != 0:
            raise RuntimeError(
                "Budget semantics preflight failed"
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
                    "s=Path(r'services/agent_runtime/app/investigation/reasoner.py')"
                    ".read_text(encoding='utf-8'); "
                    "bad=[x for x in ['ActionRuntime','ApprovalService',"
                    "'VerificationRuntime','kubectl','.post(','.patch(','.put(','.delete('] "
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
                "Budget discipline authority boundary failed"
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
                    for path in targets
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
                "Investigation Final-Synthesis Budget Discipline v1 is installed.",
                "",
                "Expected live behavior:",
                "- the final bounded reasoning slot is used for synthesis, not a probe that can never be interpreted",
                "- max_tool_calls/max_iterations remain hard Coordinator safety limits",
                "- LLM structured-output repair may correct an over-budget continue decision without any Tool call",
                "- safe abstention remains preferred over budget exhaustion when causal mechanism evidence is unavailable",
                "",
                "Next:",
                "rerun the same Change Intelligence 5 scenarios x3 through batch v2.",
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
            "INVESTIGATION FINAL-SYNTHESIS BUDGET DISCIPLINE V1 PASSED"
        )
        print("=" * 72)
        print("")
        print(
            "No real LLM/Kubernetes/Prometheus request was sent."
        )
        print("")
        print("Upload only:")
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
                        "ROLLBACK REMOVE FAILED "
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
                    "Investigation Final-Synthesis Budget Discipline v1 FAILED",
                    f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
                    "",
                    f"{type(exc).__name__}: {exc}",
                    "",
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
            "INVESTIGATION FINAL-SYNTHESIS BUDGET DISCIPLINE V1 FAILED"
        )
        print("=" * 72)
        print("")
        print(
            "Modified files were rolled back where possible."
        )
        print("")
        print("Upload only:")
        print(error)

        return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
