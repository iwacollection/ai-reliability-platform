from __future__ import annotations

import hashlib
import shutil
import subprocess
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


VERSION = "investigation-execution-resilience-v1"

AFTER_NAME = (
    "investigation_execution_resilience_v1_after.txt"
)

ERROR_NAME = (
    "investigation_execution_resilience_v1_error.txt"
)

EXPECTED_HASHES = {'services/agent_runtime/app/investigation/reasoner.py': '6a05024cb71f1be5dbefc36e9e006e53c28c19a20e844ddf6b9507c8f9d65cbf'}

REASONER_SOURCE = 'import json\nfrom abc import ABC, abstractmethod\n\nfrom pydantic import ValidationError\n\nfrom services.agent_runtime.app.investigation.llm_gateway_adapter import (\n    BaseInvestigationLLM,\n    InvestigationLLMExecutionError,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    InvestigationDecision,\n    InvestigationProbe,\n    InvestigationScope,\n    InvestigationState,\n)\n\n\nclass InvestigationReasonerError(RuntimeError):\n    """\n    Sanitized reasoner failure.\n    """\n\n\nclass InvestigationReasonerJSONError(\n    InvestigationReasonerError\n):\n    """\n    Primary decision was not valid JSON.\n    """\n\n\nclass InvestigationReasonerValidationError(\n    InvestigationReasonerError\n):\n    """\n    Primary JSON did not satisfy InvestigationDecision.\n    """\n\n\nclass InvestigationReasonerRepairJSONError(\n    InvestigationReasonerError\n):\n    """\n    One bounded repair attempt still did not return valid JSON.\n    """\n\n\nclass InvestigationReasonerRepairValidationError(\n    InvestigationReasonerError\n):\n    """\n    One bounded repair attempt still violated InvestigationDecision.\n    """\n\n\nclass InvestigationReasonerExecutionRetryError(\n    InvestigationReasonerError\n):\n    """\n    The sanitized LLM execution failed twice for the same reasoning request.\n    """\n\n\nclass BaseInvestigationReasoner(ABC):\n    """\n    Select the next symbolic read-only probe or stop with a conclusion.\n    """\n\n    @abstractmethod\n    async def decide(\n        self,\n        scope: InvestigationScope,\n        state: InvestigationState,\n    ) -> InvestigationDecision:\n        ...\n\n\nclass LLMInvestigationReasoner(\n    BaseInvestigationReasoner\n):\n    """\n    Structured LLM reasoner for the bounded InvestigationCoordinator.\n\n    The reasoner depends only on the Investigation-owned LLM abstraction.\n    Gateway routing, provider selection, fallback, rate limiting and circuit\n    breaking remain outside this class.\n\n    A sanitized InvestigationLLMExecutionError may be retried exactly once\n    for the same reasoning request. This retry adds no Evidence and no Tool\n    call; route/fallback policy remains Gateway-owned. Rate-limit/unavailable\n    classes are not retried here.\n\n    It can select only an InvestigationProbe enum value. It cannot construct\n    tool calls, resource scope, PromQL, URLs or credentials.\n    """\n\n    _SYSTEM_PROMPT = (\n        "You are a bounded SRE investigation reasoner. "\n        "Maintain competing hypotheses, use only supplied "\n        "evidence, and select only one allowed symbolic "\n        "read-only probe. Never propose or execute a write."\n    )\n\n    def __init__(\n        self,\n        investigation_llm: BaseInvestigationLLM,\n    ) -> None:\n        if not isinstance(\n            investigation_llm,\n            BaseInvestigationLLM,\n        ):\n            raise TypeError(\n                "Investigation LLM adapter is invalid"\n            )\n\n        self.investigation_llm = (\n            investigation_llm\n        )\n\n    async def decide(\n        self,\n        scope: InvestigationScope,\n        state: InvestigationState,\n    ) -> InvestigationDecision:\n        prompt = self._build_prompt(\n            scope=scope,\n            state=state,\n        )\n\n        content = await self._complete_with_execution_retry(\n            system_prompt=self._SYSTEM_PROMPT,\n            prompt=prompt,\n        )\n\n        if not isinstance(\n            content,\n            str,\n        ):\n            raise InvestigationReasonerError(\n                "Investigation reasoner returned no JSON"\n            )\n\n        try:\n            decision = self._parse_decision(\n                content,\n                repair=False,\n            )\n\n            self._validate_decision_against_state(\n                decision=decision,\n                state=state,\n                repair=False,\n            )\n\n            return decision\n\n        except (\n            InvestigationReasonerJSONError,\n            InvestigationReasonerValidationError,\n        ) as primary_error:\n            repair_content = await self._complete_with_execution_retry(\n                system_prompt=(\n                    self._SYSTEM_PROMPT\n                    + " Repair the decision contract only; "\n                    "do not invent new evidence."\n                ),\n                prompt=self._build_repair_prompt(\n                    scope=scope,\n                    state=state,\n                    primary_error=primary_error,\n                ),\n            )\n\n            if not isinstance(\n                repair_content,\n                str,\n            ):\n                raise InvestigationReasonerError(\n                    "Investigation reasoner repair returned no JSON"\n                ) from primary_error\n\n            try:\n                decision = self._parse_decision(\n                    repair_content,\n                    repair=True,\n                )\n\n                self._validate_decision_against_state(\n                    decision=decision,\n                    state=state,\n                    repair=True,\n                )\n\n                return decision\n\n            except InvestigationReasonerError as repair_error:\n                raise repair_error from primary_error\n\n    async def _complete_with_execution_retry(\n        self,\n        *,\n        system_prompt: str,\n        prompt: str,\n    ) -> str:\n        try:\n            return await self.investigation_llm.complete(\n                system_prompt=system_prompt,\n                prompt=prompt,\n            )\n\n        except InvestigationLLMExecutionError as first_error:\n            try:\n                return await self.investigation_llm.complete(\n                    system_prompt=system_prompt,\n                    prompt=prompt,\n                )\n\n            except InvestigationLLMExecutionError as second_error:\n                raise InvestigationReasonerExecutionRetryError(\n                    "Investigation LLM execution failed after one bounded retry"\n                ) from second_error\n\n    @staticmethod\n    def _validate_decision_against_state(\n        *,\n        decision: InvestigationDecision,\n        state: InvestigationState,\n        repair: bool,\n    ) -> None:\n        probe = decision.next_probe\n\n        if (\n            probe is not None\n            and probe in state.attempted_probes\n        ):\n            error_type = (\n                InvestigationReasonerRepairValidationError\n                if repair\n                else InvestigationReasonerValidationError\n            )\n\n            raise error_type(\n                "Investigation reasoner selected an already-attempted probe"\n            )\n\n    @staticmethod\n    def _parse_decision(\n        content: str,\n        *,\n        repair: bool,\n    ) -> InvestigationDecision:\n        try:\n            payload = json.loads(\n                content\n            )\n\n        except json.JSONDecodeError as exc:\n            error_type = (\n                InvestigationReasonerRepairJSONError\n                if repair\n                else InvestigationReasonerJSONError\n            )\n\n            raise error_type(\n                (\n                    "Investigation reasoner repair returned invalid JSON"\n                    if repair\n                    else "Investigation reasoner returned invalid JSON"\n                )\n            ) from exc\n\n        try:\n            return InvestigationDecision.model_validate(\n                payload\n            )\n\n        except (\n            ValidationError,\n            TypeError,\n            ValueError,\n        ) as exc:\n            error_type = (\n                InvestigationReasonerRepairValidationError\n                if repair\n                else InvestigationReasonerValidationError\n            )\n\n            raise error_type(\n                (\n                    "Investigation reasoner repair returned an invalid decision"\n                    if repair\n                    else "Investigation reasoner returned an invalid decision"\n                )\n            ) from exc\n\n    @classmethod\n    def _build_repair_prompt(\n        cls,\n        *,\n        scope: InvestigationScope,\n        state: InvestigationState,\n        primary_error: InvestigationReasonerError,\n    ) -> str:\n        failure_kind = type(\n            primary_error\n        ).__name__\n\n        return (\n            "Your previous decision failed the bounded structured-output "\n            f"contract with failure type {failure_kind}.\\n"\n            "Do not repeat or explain the invalid response.\\n"\n            "Re-evaluate the SAME supplied state. Do not invent evidence, "\n            "do not add a tool call outside allowed_probes, and do not "\n            "change resource scope.\\n"\n            "Return exactly one corrected JSON decision that satisfies every "\n            "shape and evidence rule below.\\n\\n"\n            + cls._build_prompt(\n                scope=scope,\n                state=state,\n            )\n        )\n\n    @staticmethod\n    def _build_prompt(\n        scope: InvestigationScope,\n        state: InvestigationState,\n    ) -> str:\n        trusted_evidence_ids = [\n            item.evidence_id\n            for item in state.evidence\n            if (\n                item.success\n                and item.trusted\n                and item.production_signal\n            )\n        ]\n\n        attempted_probe_set = set(\n            state.attempted_probes\n        )\n\n        state_payload = {\n            "scope": scope.model_dump(\n                mode="json"\n            ),\n            "iteration_count": state.iteration_count,\n            "tool_call_count": state.tool_call_count,\n            "attempted_probes": [\n                probe.value\n                for probe in state.attempted_probes\n            ],\n            "failed_probes": [\n                item.probe.value\n                for item in state.evidence\n                if not item.success\n            ],\n            "hypotheses": [\n                item.model_dump(mode="json")\n                for item in state.hypotheses\n            ],\n            "evidence": [\n                item.model_dump(mode="json")\n                for item in state.evidence\n            ],\n            "trusted_evidence_ids": trusted_evidence_ids,\n            "allowed_probes": [\n                probe.value\n                for probe in InvestigationProbe\n                if probe not in attempted_probe_set\n            ],\n        }\n\n        return (\n            "Investigate the incident using the bounded state below.\\n"\n            "Return exactly one JSON object only. Do not return markdown.\\n"\n            "Maintain competing hypotheses and update confidence from evidence.\\n"\n            "Probe affordances:\\n"\n            "- kubernetes_pod_state: current pod/container state, restart "\n            "indicators, and last termination reasons.\\n"\n            "- kubernetes_previous_container_logs: bounded previous-container "\n            "output; high-information evidence for unexplained restart, startup, "\n            "panic, configuration, dependency, or crash symptoms.\\n"\n            "- prometheus_memory_working_set: sampled container memory usage.\\n"\n            "- prometheus_memory_limit: configured container memory limit.\\n"\n            "- prometheus_restart_count: sampled restart frequency/corroboration.\\n"\n            "If trusted evidence falsifies the current leading hypothesis but "\n            "the observed incident symptom remains unexplained, do not stop "\n            "solely because that hypothesis was rejected. Replan with at least "\n            "one evidence-plausible alternative hypothesis when an unattempted "\n            "allowed probe can materially discriminate plausible causes.\\n"\n            "Use insufficient_evidence only when no unattempted safe probe can "\n            "materially discriminate the remaining plausible causes, or when "\n            "required evidence is unavailable.\\n"\n            "State.allowed_probes already excludes every attempted probe. "\n            "Select next_probe only from State.allowed_probes.\\n"\n            "A failed probe is still an attempted probe. Do not retry it inside "\n            "the same investigation; keep its required evidence missing and "\n            "use another unattempted discriminative probe or safely abstain.\\n"\n            "Never repeat a probe already listed in attempted_probes.\\n"\n            "Never cite an evidence ID that is absent from State.evidence.\\n"\n            "Conflicting evidence can weaken a hypothesis but cannot by itself "\n            "positively establish an unrelated root cause.\\n"\n            "Ruling out one hypothesis is not sufficient evidence for a different "\n            "causal claim.\\n"\n            "Current-state evidence does not by itself prove that a historical "\n            "event never occurred.\\n"\n            "For sufficient_evidence, the conclusion must affirm a positively "\n            "supported current hypothesis. Every conclusion.evidence_id must also "\n            "appear in that hypothesis supporting_evidence_ids and must not be "\n            "conflicting evidence for that same hypothesis.\\n"\n            "Conclusion confidence must not materially exceed the confidence of "\n            "the positively supported hypothesis.\\n"\n            "A symptom or failure-mode observation such as CrashLoopBackOff, "\n            "restart count, unready state, high latency, or high error rate can "\n            "confirm that a failure exists, but does not by itself establish the "\n            "specific underlying cause that produced it.\\n"\n            "If several underlying causes remain plausible and current allowed "\n            "probes cannot discriminate among them, keep the required "\n            "root-cause evidence in hypothesis.missing_evidence and stop with "\n            "insufficient_evidence or no_safe_probe.\\n"\n            "Use hypothesis.missing_evidence only for evidence that is REQUIRED "\n            "before the specific root cause can be accepted. Use "\n            "hypothesis.optional_evidence for corroboration that may increase "\n            "confidence or describe frequency/severity but is not required to "\n            "establish the root cause.\\n"\n            "Do not put the same evidence need in both missing_evidence and "\n            "optional_evidence.\\n"\n            "Do not clear missing_evidence merely because all allowed probes "\n            "have been attempted. For sufficient_evidence, the positively "\n            "supported hypothesis used by the conclusion must have an empty "\n            "missing_evidence list. optional_evidence may remain non-empty.\\n"\n            "Treat event evidence separately from mechanism evidence. For example, "\n            "OOMKilled proves that an OOM termination occurred, but does not by "\n            "itself prove that a configured container memory limit was exceeded.\\n"\n            "A point-in-time or sampled metric cannot establish an unobserved "\n            "transient peak, historical trend, or threshold crossing. Never invent "\n            "an unseen spike to make a hypothesis fit.\\n"\n            "For quantitative threshold causes, supporting evidence must be "\n            "directionally consistent with the claimed mechanism. If a sampled "\n            "working value is below the sampled limit, that sample is not positive "\n            "support for the claim that the limit was exceeded.\\n"\n            "If an event is confirmed but the available sampled metrics do not "\n            "explain its mechanism, keep the required historical/range/peak "\n            "evidence in missing_evidence and stop with insufficient_evidence "\n            "unless another direct causal observation establishes the cause.\\n"\n            "If the available evidence only rejects hypotheses and does not "\n            "positively establish a root cause, stop with insufficient_evidence.\\n"\n            "If the available evidence only rejects hypotheses or confirms a "\n            "symptom/failure mode without establishing its cause, stop with "\n            "insufficient_evidence.\\n"\n            "For a conclusion, evidence_ids must be non-empty and copied "\n            "exactly from trusted_evidence_ids.\\n"\n            "Allowed model terminal stop_reason values are exactly: "\n            "sufficient_evidence, insufficient_evidence, no_safe_probe.\\n"\n            "Do not emit internal coordinator reasons such as timeout, "\n            "max_iterations, max_tool_calls, duplicate_probe or reasoner_error.\\n"\n            "\\n"\n            "Continuing shape:\\n"\n            "{\\n"\n            \'  "hypotheses": [{"hypothesis_id": "h1", "cause": "candidate", \'\n            \'"confidence": 0.5, "supporting_evidence_ids": [], \'\n            \'"conflicting_evidence_ids": [], "missing_evidence": ["required evidence"], "optional_evidence": ["non-blocking corroboration"]}],\\n\'\n            \'  "rationale_summary": "why this probe is most discriminative",\\n\'\n            \'  "stop": false,\\n\'\n            \'  "stop_reason": null,\\n\'\n            \'  "next_probe": "one unattempted value from allowed_probes",\\n\'\n            \'  "conclusion": null\\n\'\n            "}\\n"\n            "\\n"\n            "Terminal sufficient-evidence shape:\\n"\n            "{\\n"\n            \'  "hypotheses": [{"hypothesis_id": "h1", "cause": "supported cause", \'\n            \'"confidence": 0.9, "supporting_evidence_ids": ["copy exact trusted id"], \'\n            \'"conflicting_evidence_ids": [], "missing_evidence": [], "optional_evidence": []}],\\n\'\n            \'  "rationale_summary": "why trusted evidence is sufficient",\\n\'\n            \'  "stop": true,\\n\'\n            \'  "stop_reason": "sufficient_evidence",\\n\'\n            \'  "next_probe": null,\\n\'\n            \'  "conclusion": {"root_cause": "bounded root cause", \'\n            \'"confidence": 0.9, "evidence_ids": ["copy exact trusted id"], \'\n            \'"remaining_uncertainties": []}\\n\'\n            "}\\n"\n            "\\n"\n            "Terminal insufficient/no-safe-probe shape:\\n"\n            "{\\n"\n            \'  "hypotheses": [{"hypothesis_id": "h1", "cause": "candidate", \'\n            \'"confidence": 0.3, "supporting_evidence_ids": [], \'\n            \'"conflicting_evidence_ids": [], "missing_evidence": ["required missing evidence"], "optional_evidence": ["non-blocking evidence if useful"]}],\\n\'\n            \'  "rationale_summary": "why current bounded evidence cannot support an RCA",\\n\'\n            \'  "stop": true,\\n\'\n            \'  "stop_reason": "insufficient_evidence",\\n\'\n            \'  "next_probe": null,\\n\'\n            \'  "conclusion": null\\n\'\n            "}\\n"\n            "\\n"\n            "If no useful unattempted allowed probe can resolve the remaining "\n            "uncertainty, stop with insufficient_evidence or no_safe_probe.\\n"\n            "State:\\n"\n            f"{json.dumps(state_payload, ensure_ascii=True, sort_keys=True)}"\n        )\n\n\n__all__ = [\n    "BaseInvestigationReasoner",\n    "InvestigationReasonerError",\n    "LLMInvestigationReasoner",\n]\n'
BATCH_RUNNER_SOURCE = 'from __future__ import annotations\n\nimport argparse\nimport json\nimport subprocess\nimport sys\nfrom datetime import datetime, timezone\nfrom pathlib import Path\n\n\nREPORT_JSON = Path("investigation_intelligence_benchmark_v1_report.json")\nREPORT_TXT = Path("investigation_intelligence_benchmark_v1_report.txt")\nERROR_TXT = Path("investigation_intelligence_benchmark_v1_error.txt")\n\nDEFAULT_OUTPUT = Path("investigation_benchmark_batch_bundle.json")\n\n\ndef cleanup_temp() -> None:\n    for path in (\n        REPORT_JSON,\n        REPORT_TXT,\n        ERROR_TXT,\n    ):\n        try:\n            path.unlink()\n        except FileNotFoundError:\n            pass\n\n\ndef run_once(\n    *,\n    runner: Path,\n    provider: str,\n    scenario: str | None,\n    mode: str | None,\n) -> dict:\n    cleanup_temp()\n\n    command = [\n        "uv",\n        "run",\n        "python",\n        str(runner),\n        "--provider",\n        provider,\n    ]\n\n    if scenario:\n        command.extend(\n            [\n                "--scenario",\n                scenario,\n            ]\n        )\n    elif mode:\n        command.extend(\n            [\n                "--mode",\n                mode,\n            ]\n        )\n    else:\n        raise RuntimeError(\n            "Either scenario or mode is required."\n        )\n\n    process = subprocess.run(\n        command,\n        capture_output=True,\n        text=True,\n        encoding="utf-8",\n        errors="replace",\n        check=False,\n    )\n\n    result = {\n        "command": command,\n        "exit_code": process.returncode,\n        "stdout": process.stdout,\n        "stderr": process.stderr,\n        "report_json": None,\n        "report_text": None,\n        "error_text": None,\n    }\n\n    if REPORT_JSON.exists():\n        try:\n            result["report_json"] = json.loads(\n                REPORT_JSON.read_text(\n                    encoding="utf-8"\n                )\n            )\n        except Exception as exc:\n            result["report_json_parse_error"] = (\n                f"{type(exc).__name__}: {exc}"\n            )\n            result["report_json_raw"] = (\n                REPORT_JSON.read_text(\n                    encoding="utf-8",\n                    errors="replace",\n                )\n            )\n\n    if REPORT_TXT.exists():\n        result["report_text"] = (\n            REPORT_TXT.read_text(\n                encoding="utf-8",\n                errors="replace",\n            )\n        )\n\n    if ERROR_TXT.exists():\n        result["error_text"] = (\n            ERROR_TXT.read_text(\n                encoding="utf-8",\n                errors="replace",\n            )\n        )\n\n    cleanup_temp()\n\n    return result\n\n\ndef summarize_run(\n    payload: dict,\n) -> dict:\n    report = payload.get(\n        "report_json"\n    )\n\n    if not isinstance(\n        report,\n        dict,\n    ):\n        return {\n            "exit_code": payload.get(\n                "exit_code"\n            ),\n            "status": "execution_error",\n        }\n\n    scenarios = report.get(\n        "scenarios"\n    ) or []\n\n    first = (\n        scenarios[0]\n        if len(\n            scenarios\n        ) == 1\n        else None\n    )\n\n    summary = {\n        "exit_code": payload.get(\n            "exit_code"\n        ),\n        "overall_score": report.get(\n            "overall_score"\n        ),\n        "outcome_accuracy": report.get(\n            "outcome_accuracy"\n        ),\n        "abstention_accuracy": report.get(\n            "abstention_accuracy"\n        ),\n        "sufficient_evidence_accuracy": report.get(\n            "sufficient_evidence_accuracy"\n        ),\n        "average_tool_calls": report.get(\n            "average_tool_calls"\n        ),\n        "guard_rescue_count": report.get(\n            "guard_rescue_count"\n        ),\n        "guard_rescue_rate": report.get(\n            "guard_rescue_rate"\n        ),\n    }\n\n    if isinstance(\n        first,\n        dict,\n    ):\n        summary.update(\n            {\n                "scenario_key": first.get(\n                    "scenario_key"\n                ),\n                "score": first.get(\n                    "score"\n                ),\n                "final_status": first.get(\n                    "final_status"\n                ),\n                "final_stop_reason": first.get(\n                    "final_stop_reason"\n                ),\n                "failure_code": first.get(\n                    "failure_code"\n                ),\n                "epistemic_guard_code": first.get(\n                    "epistemic_guard_code"\n                ),\n                "guard_rescued": first.get(\n                    "guard_rescued"\n                ),\n                "tool_call_count": first.get(\n                    "tool_call_count"\n                ),\n                "outcome_correct": first.get(\n                    "outcome_correct"\n                ),\n            }\n        )\n\n    return summary\n\n\ndef main() -> int:\n    parser = argparse.ArgumentParser(\n        description=(\n            "Run Investigation Intelligence Benchmark multiple times "\n            "and emit exactly one merged JSON bundle."\n        )\n    )\n\n    parser.add_argument(\n        "--provider",\n        default="bailian",\n    )\n\n    parser.add_argument(\n        "--scenario",\n        action="append",\n        default=[],\n        help=(\n            "Scenario key. Repeat --scenario for multiple scenarios."\n        ),\n    )\n\n    parser.add_argument(\n        "--mode",\n        choices=[\n            "smoke",\n            "full",\n        ],\n        default=None,\n    )\n\n    parser.add_argument(\n        "--repeat",\n        type=int,\n        default=1,\n    )\n\n    parser.add_argument(\n        "--output",\n        default=str(\n            DEFAULT_OUTPUT\n        ),\n    )\n\n    parser.add_argument(\n        "--runner",\n        default=(\n            "scripts/dev/"\n            "run_investigation_intelligence_benchmark_v1.py"\n        ),\n    )\n\n    args = parser.parse_args()\n\n    if args.repeat < 1:\n        parser.error(\n            "--repeat must be >= 1"\n        )\n\n    if bool(\n        args.scenario\n    ) == bool(\n        args.mode\n    ):\n        parser.error(\n            "Use either --scenario ... or --mode ..., not both."\n        )\n\n    runner = Path(\n        args.runner\n    )\n\n    if not runner.exists():\n        raise SystemExit(\n            f"Benchmark runner not found: {runner}"\n        )\n\n    output = Path(\n        args.output\n    )\n\n    targets = (\n        [\n            {\n                "kind": "scenario",\n                "value": scenario,\n            }\n            for scenario in args.scenario\n        ]\n        if args.scenario\n        else [\n            {\n                "kind": "mode",\n                "value": args.mode,\n            }\n        ]\n    )\n\n    bundle = {\n        "schema_version": "investigation-benchmark-batch-v1",\n        "generated_at": datetime.now(\n            timezone.utc\n        ).isoformat(),\n        "provider": args.provider,\n        "repeat": args.repeat,\n        "targets": targets,\n        "runs": [],\n    }\n\n    total = (\n        len(\n            targets\n        )\n        * args.repeat\n    )\n\n    current = 0\n\n    for target in targets:\n        for run_no in range(\n            1,\n            args.repeat + 1,\n        ):\n            current += 1\n\n            print(\n                "=" * 72\n            )\n            print(\n                (\n                    f"[{current}/{total}] "\n                    f"{target[\'kind\']}={target[\'value\']} "\n                    f"run={run_no}/{args.repeat}"\n                )\n            )\n            print(\n                "=" * 72\n            )\n\n            payload = run_once(\n                runner=runner,\n                provider=args.provider,\n                scenario=(\n                    target["value"]\n                    if target[\n                        "kind"\n                    ] == "scenario"\n                    else None\n                ),\n                mode=(\n                    target["value"]\n                    if target[\n                        "kind"\n                    ] == "mode"\n                    else None\n                ),\n            )\n\n            bundle[\n                "runs"\n            ].append(\n                {\n                    "target_kind": target[\n                        "kind"\n                    ],\n                    "target": target[\n                        "value"\n                    ],\n                    "run": run_no,\n                    "summary": summarize_run(\n                        payload\n                    ),\n                    "raw": payload,\n                }\n            )\n\n    summaries = [\n        run[\n            "summary"\n        ]\n        for run in bundle[\n            "runs"\n        ]\n    ]\n\n    bundle[\n        "aggregate"\n    ] = {\n        "run_count": len(\n            summaries\n        ),\n        "execution_error_count": sum(\n            1\n            for item in summaries\n            if item.get(\n                "status"\n            ) == "execution_error"\n            or item.get(\n                "failure_code"\n            )\n            in {\n                "InvestigationLLMExecutionError",\n            }\n        ),\n        "outcome_correct_count": sum(\n            1\n            for item in summaries\n            if item.get(\n                "outcome_correct"\n            )\n            is True\n        ),\n        "guard_rescued_count": sum(\n            1\n            for item in summaries\n            if item.get(\n                "guard_rescued"\n            )\n            is True\n        ),\n    }\n\n    output.write_text(\n        json.dumps(\n            bundle,\n            ensure_ascii=False,\n            indent=2,\n        ),\n        encoding="utf-8",\n        newline="\\n",\n    )\n\n    cleanup_temp()\n\n    print("")\n    print(\n        "=" * 72\n    )\n    print(\n        "BATCH BENCHMARK FINISHED"\n    )\n    print(\n        "=" * 72\n    )\n    print(\n        f"Runs: {len(bundle[\'runs\'])}"\n    )\n    print(\n        f"Output: {output}"\n    )\n    print("")\n    print(\n        "Only upload this one bundle file."\n    )\n\n    return 0\n\n\nif __name__ == "__main__":\n    raise SystemExit(\n        main()\n    )\n'
TEST_SOURCE = 'from __future__ import annotations\n\nimport json\n\nimport pytest\n\nfrom services.agent_runtime.app.investigation.llm_gateway_adapter import (\n    BaseInvestigationLLM,\n    InvestigationLLMExecutionError,\n    InvestigationLLMUnavailableError,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    EvidenceItem,\n    IncidentHypothesis,\n    InvestigationDecision,\n    InvestigationProbe,\n    InvestigationScope,\n    InvestigationState,\n)\nfrom services.agent_runtime.app.investigation.reasoner import (\n    InvestigationReasonerExecutionRetryError,\n    InvestigationReasonerRepairValidationError,\n    LLMInvestigationReasoner,\n)\n\n\nclass SequenceInvestigationLLM(\n    BaseInvestigationLLM\n):\n    def __init__(\n        self,\n        values,\n    ) -> None:\n        self.values = list(\n            values\n        )\n        self.calls = []\n\n    async def complete(\n        self,\n        *,\n        system_prompt: str,\n        prompt: str,\n    ) -> str:\n        self.calls.append(\n            {\n                "system_prompt": system_prompt,\n                "prompt": prompt,\n            }\n        )\n\n        value = self.values.pop(\n            0\n        )\n\n        if isinstance(\n            value,\n            BaseException,\n        ):\n            raise value\n\n        return value\n\n\ndef scope() -> InvestigationScope:\n    return InvestigationScope(\n        alert_name="PodRestartHigh",\n        alert_message="payment-api restarts are increasing",\n        resource="payment-api",\n        namespace="payment",\n        cluster="benchmark-lab",\n    )\n\n\ndef valid_probe_decision(\n    probe: str,\n) -> str:\n    return json.dumps(\n        {\n            "hypotheses": [\n                {\n                    "hypothesis_id": "h1",\n                    "cause": "unresolved restart cause",\n                    "confidence": 0.4,\n                    "supporting_evidence_ids": [],\n                    "conflicting_evidence_ids": [],\n                    "missing_evidence": [\n                        "root-cause evidence"\n                    ],\n                    "optional_evidence": [],\n                }\n            ],\n            "rationale_summary": (\n                "select the next unattempted discriminative probe"\n            ),\n            "stop": False,\n            "stop_reason": None,\n            "next_probe": probe,\n            "conclusion": None,\n        }\n    )\n\n\n@pytest.mark.asyncio\nasync def test_transient_execution_error_retries_once_same_request():\n    llm = SequenceInvestigationLLM(\n        [\n            InvestigationLLMExecutionError(\n                "sanitized execution failure"\n            ),\n            valid_probe_decision(\n                "kubernetes_pod_state"\n            ),\n        ]\n    )\n\n    reasoner = LLMInvestigationReasoner(\n        llm\n    )\n\n    current = InvestigationState(\n        scope=scope()\n    )\n\n    decision = await reasoner.decide(\n        current.scope,\n        current,\n    )\n\n    assert (\n        decision.next_probe\n        == InvestigationProbe.KUBERNETES_POD_STATE\n    )\n\n    assert len(\n        llm.calls\n    ) == 2\n\n    assert (\n        llm.calls[0]["system_prompt"]\n        == llm.calls[1]["system_prompt"]\n    )\n\n    assert (\n        llm.calls[0]["prompt"]\n        == llm.calls[1]["prompt"]\n    )\n\n\n@pytest.mark.asyncio\nasync def test_two_execution_errors_fail_closed_with_precise_code():\n    llm = SequenceInvestigationLLM(\n        [\n            InvestigationLLMExecutionError(\n                "first"\n            ),\n            InvestigationLLMExecutionError(\n                "second"\n            ),\n        ]\n    )\n\n    reasoner = LLMInvestigationReasoner(\n        llm\n    )\n\n    current = InvestigationState(\n        scope=scope()\n    )\n\n    with pytest.raises(\n        InvestigationReasonerExecutionRetryError,\n    ):\n        await reasoner.decide(\n            current.scope,\n            current,\n        )\n\n    assert len(\n        llm.calls\n    ) == 2\n\n\n@pytest.mark.asyncio\nasync def test_unavailable_error_is_not_retried():\n    llm = SequenceInvestigationLLM(\n        [\n            InvestigationLLMUnavailableError(\n                "unavailable"\n            ),\n        ]\n    )\n\n    reasoner = LLMInvestigationReasoner(\n        llm\n    )\n\n    current = InvestigationState(\n        scope=scope()\n    )\n\n    with pytest.raises(\n        InvestigationLLMUnavailableError,\n    ):\n        await reasoner.decide(\n            current.scope,\n            current,\n        )\n\n    assert len(\n        llm.calls\n    ) == 1\n\n\n@pytest.mark.asyncio\nasync def test_duplicate_probe_decision_gets_one_contract_repair():\n    llm = SequenceInvestigationLLM(\n        [\n            valid_probe_decision(\n                "kubernetes_previous_container_logs"\n            ),\n            valid_probe_decision(\n                "kubernetes_pod_state"\n            ),\n        ]\n    )\n\n    reasoner = LLMInvestigationReasoner(\n        llm\n    )\n\n    current = InvestigationState(\n        scope=scope(),\n        attempted_probes=[\n            InvestigationProbe.KUBERNETES_PREVIOUS_CONTAINER_LOGS\n        ],\n    )\n\n    decision = await reasoner.decide(\n        current.scope,\n        current,\n    )\n\n    assert (\n        decision.next_probe\n        == InvestigationProbe.KUBERNETES_POD_STATE\n    )\n\n    assert len(\n        llm.calls\n    ) == 2\n\n    assert (\n        "kubernetes_previous_container_logs"\n        in llm.calls[1]["prompt"]\n    )\n\n    assert (\n        \'"allowed_probes": ["kubernetes_pod_state"\'\n        in llm.calls[1]["prompt"]\n    )\n\n\n@pytest.mark.asyncio\nasync def test_duplicate_probe_after_repair_still_fails_closed():\n    duplicate = valid_probe_decision(\n        "kubernetes_previous_container_logs"\n    )\n\n    llm = SequenceInvestigationLLM(\n        [\n            duplicate,\n            duplicate,\n        ]\n    )\n\n    reasoner = LLMInvestigationReasoner(\n        llm\n    )\n\n    current = InvestigationState(\n        scope=scope(),\n        attempted_probes=[\n            InvestigationProbe.KUBERNETES_PREVIOUS_CONTAINER_LOGS\n        ],\n    )\n\n    with pytest.raises(\n        InvestigationReasonerRepairValidationError,\n    ):\n        await reasoner.decide(\n            current.scope,\n            current,\n        )\n\n    assert len(\n        llm.calls\n    ) == 2\n\n\ndef test_prompt_allowed_probes_excludes_attempted_failed_probe():\n    current = InvestigationState(\n        scope=scope(),\n        attempted_probes=[\n            InvestigationProbe.KUBERNETES_PREVIOUS_CONTAINER_LOGS\n        ],\n        evidence=[\n            EvidenceItem(\n                evidence_id="failed-log",\n                probe=(\n                    InvestigationProbe\n                    .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n                ),\n                source="kubernetes",\n                success=False,\n                trusted=False,\n                production_signal=False,\n                reliability=0.0,\n                observed_at=__import__("datetime").datetime.now(\n                    __import__("datetime").UTC\n                ),\n                facts={},\n                error_code="RuntimeError",\n            )\n        ],\n    )\n\n    prompt = (\n        LLMInvestigationReasoner\n        ._build_prompt(\n            scope=current.scope,\n            state=current,\n        )\n    )\n\n    state_json = prompt.split(\n        "State:\\\\n",\n        1,\n    )[1]\n\n    payload = json.loads(\n        state_json\n    )\n\n    assert (\n        "kubernetes_previous_container_logs"\n        not in payload[\n            "allowed_probes"\n        ]\n    )\n\n    assert (\n        "kubernetes_previous_container_logs"\n        in payload[\n            "failed_probes"\n        ]\n    )\n\n    assert (\n        "A failed probe is still an attempted probe"\n        in prompt\n    )\n'


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

    reasoner_file = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "investigation"
        / "reasoner.py"
    )

    batch_runner_file = (
        root
        / "scripts"
        / "dev"
        / "run_investigation_benchmark_batch_v1.py"
    )

    test_file = (
        root
        / "services"
        / "agent_runtime"
        / "tests"
        / "test_investigation_execution_resilience.py"
    )

    sources = {
        reasoner_file: REASONER_SOURCE,
        batch_runner_file: BATCH_RUNNER_SOURCE,
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
        "Investigation Execution Resilience v1",
        f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
        "",
        "Observed stability findings addressed:",
        "- sanitized InvestigationLLMExecutionError occurred intermittently during live Bailian runs",
        "- one backend-failure run selected a previously failed Logs probe again and exhausted with duplicate_probe",
        "- multiple Benchmark reports are operationally cumbersome and are now bundled by a repository runner",
        "",
        "LLM execution reliability:",
        "- retry exactly once only for InvestigationLLMExecutionError",
        "- retry uses the identical system prompt and identical state-derived prompt",
        "- retry adds no Evidence and no Tool call",
        "- a second execution failure becomes InvestigationReasonerExecutionRetryError",
        "- rate-limit/unavailable errors remain fail-fast and are not retried here",
        "",
        "Probe-attempt semantics:",
        "- State.allowed_probes now contains only unattempted probes",
        "- failed probes remain attempted and are listed separately as failed_probes",
        "- selecting an attempted probe becomes a contract validation error",
        "- existing one-shot Decision repair may correct that selection",
        "- persistent duplicate selection still fails closed",
        "",
        "Developer workflow:",
        "- installs scripts/dev/run_investigation_benchmark_batch_v1.py",
        "- batch runner absorbs per-run txt/json/error files into one JSON bundle",
        "- temporary benchmark report files are automatically removed",
        "",
        "No Coordinator, Tool, Kubernetes, Prometheus, Guard, Action, Approval or Verification authority is changed.",
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
            name="Execution Resilience focused regression suite",
            command=[
                "uv",
                "run",
                "pytest",
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_execution_resilience.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_decision_robustness.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_evidence_consistency.py"
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
                    "test_investigation_reasoner.py"
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
                "Execution Resilience focused tests failed"
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
                    "test_investigation_coordinator.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_models.py"
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
                    "test_investigation_probes.py"
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

        batch_help = run_command(
            root=root,
            name="Batch benchmark runner help smoke",
            command=[
                "uv",
                "run",
                "python",
                str(
                    batch_runner_file.relative_to(
                        root
                    )
                ),
                "--help",
            ],
        )

        add_command(
            report,
            batch_help,
        )

        if batch_help.returncode != 0:
            raise RuntimeError(
                "Batch benchmark runner help smoke failed"
            )

        preflight = run_command(
            root=root,
            name="Execution resilience preflight",
            command=[
                "uv",
                "run",
                "python",
                "-c",
                (
                    "from pathlib import Path; "
                    "p=Path(r'services/agent_runtime/app/investigation/reasoner.py')"
                    ".read_text(encoding='utf-8'); "
                    "print('execution_retry='+str('_complete_with_execution_retry' in p)); "
                    "print('state_validation='+str('_validate_decision_against_state' in p)); "
                    "print('failed_probes='+str('failed_probes' in p)); "
                    "print('filtered_allowed='+str('if probe not in attempted_probe_set' in p)); "
                    "assert '_complete_with_execution_retry' in p; "
                    "assert '_validate_decision_against_state' in p; "
                    "assert 'failed_probes' in p; "
                    "assert 'if probe not in attempted_probe_set' in p"
                ),
            ],
        )

        add_command(
            report,
            preflight,
        )

        if preflight.returncode != 0:
            raise RuntimeError(
                "Execution resilience preflight failed"
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
                "Execution Resilience authority boundary failed"
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
                "Execution Resilience v1 is installed.",
                "",
                "Expected live behavior:",
                "- one transient InvestigationLLMExecutionError is retried transparently",
                "- two consecutive execution failures still fail closed with a precise reasoner failure code",
                "- failed probes cannot remain in allowed_probes",
                "- accidental duplicate-probe decisions receive one bounded contract repair opportunity",
                "- persistent duplicate selection still fails closed",
                "- multi-run Benchmark validation now produces one uploadable JSON bundle",
                "",
                "Next acceptance command:",
                (
                    "uv run python scripts/dev/"
                    "run_investigation_benchmark_batch_v1.py "
                    "--provider bailian "
                    "--scenario probe_backend_failure "
                    "--scenario oom_without_explanatory_metrics "
                    "--repeat 3"
                ),
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
            "INVESTIGATION EXECUTION RESILIENCE V1 PASSED"
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
                    "Investigation Execution Resilience v1 FAILED",
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
            "INVESTIGATION EXECUTION RESILIENCE V1 FAILED"
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
