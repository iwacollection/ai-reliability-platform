from __future__ import annotations

import hashlib
import shutil
import subprocess
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


VERSION = "llm-gateway-transport-reliability-v2"

AFTER_NAME = (
    "llm_gateway_transport_reliability_v2_after.txt"
)

ERROR_NAME = (
    "llm_gateway_transport_reliability_v2_error.txt"
)

EXPECTED_HASHES = {'services/agent_runtime/app/investigation/reasoner.py': '8e90e58d9217a0710ea13c9f867dd3d543aefc31038a35a154d502c58662a902', 'services/agent_runtime/app/llm/providers/bailian_compatible.py': 'a05e73d4267605a2c788a3614b7a537f84bad8d33641f45b85fe1a2967ae2f72', 'services/agent_runtime/app/llm/providers/openai_compatible.py': '7723f8e5ffd8831c2b817cabe9160d2ad76ac9075a94512635ec72984d83c422', 'services/agent_runtime/tests/test_investigation_execution_resilience.py': 'a2abaf7d3d4d0562b925eb40abef6fadd19472799b61496d2e5524493b17d5ff', 'services/agent_runtime/tests/test_llm_gateway_transport_reliability.py': 'c3c462dc31abdeb17c5db80c3d76fd4e0870f8154ee4c46bc7307949ead74280'}

REASONER_SOURCE = 'import json\nfrom abc import ABC, abstractmethod\n\nfrom pydantic import ValidationError\n\nfrom services.agent_runtime.app.investigation.llm_gateway_adapter import (\n    BaseInvestigationLLM,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    InvestigationDecision,\n    InvestigationProbe,\n    InvestigationScope,\n    InvestigationState,\n)\n\n\nclass InvestigationReasonerError(RuntimeError):\n    """\n    Sanitized reasoner failure.\n    """\n\n\nclass InvestigationReasonerJSONError(\n    InvestigationReasonerError\n):\n    """\n    Primary decision was not valid JSON.\n    """\n\n\nclass InvestigationReasonerValidationError(\n    InvestigationReasonerError\n):\n    """\n    Primary JSON did not satisfy InvestigationDecision.\n    """\n\n\nclass InvestigationReasonerRepairJSONError(\n    InvestigationReasonerError\n):\n    """\n    One bounded repair attempt still did not return valid JSON.\n    """\n\n\nclass InvestigationReasonerRepairValidationError(\n    InvestigationReasonerError\n):\n    """\n    One bounded repair attempt still violated InvestigationDecision.\n    """\n\n\nclass InvestigationReasonerExecutionRetryError(\n    InvestigationReasonerError\n):\n    """\n    The sanitized LLM execution failed twice for the same reasoning request.\n    """\n\n\nclass BaseInvestigationReasoner(ABC):\n    """\n    Select the next symbolic read-only probe or stop with a conclusion.\n    """\n\n    @abstractmethod\n    async def decide(\n        self,\n        scope: InvestigationScope,\n        state: InvestigationState,\n    ) -> InvestigationDecision:\n        ...\n\n\nclass LLMInvestigationReasoner(\n    BaseInvestigationReasoner\n):\n    """\n    Structured LLM reasoner for the bounded InvestigationCoordinator.\n\n    The reasoner depends only on the Investigation-owned LLM abstraction.\n    Gateway routing, provider selection, fallback, rate limiting and circuit\n    breaking remain outside this class.\n\n    Transport execution retry ownership remains entirely in the shared\n    LLM Gateway. The Reasoner does not repeat a failed Gateway request.\n    Its only bounded second model call is structured Decision-contract repair\n    after a model response was successfully received but failed validation.\n\n    It can select only an InvestigationProbe enum value. It cannot construct\n    tool calls, resource scope, PromQL, URLs or credentials.\n    """\n\n    _SYSTEM_PROMPT = (\n        "You are a bounded SRE investigation reasoner. "\n        "Maintain competing hypotheses, use only supplied "\n        "evidence, and select only one allowed symbolic "\n        "read-only probe. Never propose or execute a write."\n    )\n\n    def __init__(\n        self,\n        investigation_llm: BaseInvestigationLLM,\n    ) -> None:\n        if not isinstance(\n            investigation_llm,\n            BaseInvestigationLLM,\n        ):\n            raise TypeError(\n                "Investigation LLM adapter is invalid"\n            )\n\n        self.investigation_llm = (\n            investigation_llm\n        )\n\n    async def decide(\n        self,\n        scope: InvestigationScope,\n        state: InvestigationState,\n    ) -> InvestigationDecision:\n        prompt = self._build_prompt(\n            scope=scope,\n            state=state,\n        )\n\n        content = await self.investigation_llm.complete(\n            system_prompt=self._SYSTEM_PROMPT,\n            prompt=prompt,\n        )\n\n        if not isinstance(\n            content,\n            str,\n        ):\n            raise InvestigationReasonerError(\n                "Investigation reasoner returned no JSON"\n            )\n\n        try:\n            decision = self._parse_decision(\n                content,\n                repair=False,\n            )\n\n            self._validate_decision_against_state(\n                decision=decision,\n                state=state,\n                repair=False,\n            )\n\n            return decision\n\n        except (\n            InvestigationReasonerJSONError,\n            InvestigationReasonerValidationError,\n        ) as primary_error:\n            repair_content = await self.investigation_llm.complete(\n                system_prompt=(\n                    self._SYSTEM_PROMPT\n                    + " Repair the decision contract only; "\n                    "do not invent new evidence."\n                ),\n                prompt=self._build_repair_prompt(\n                    scope=scope,\n                    state=state,\n                    primary_error=primary_error,\n                ),\n            )\n\n            if not isinstance(\n                repair_content,\n                str,\n            ):\n                raise InvestigationReasonerError(\n                    "Investigation reasoner repair returned no JSON"\n                ) from primary_error\n\n            try:\n                decision = self._parse_decision(\n                    repair_content,\n                    repair=True,\n                )\n\n                self._validate_decision_against_state(\n                    decision=decision,\n                    state=state,\n                    repair=True,\n                )\n\n                return decision\n\n            except InvestigationReasonerError as repair_error:\n                raise repair_error from primary_error\n\n    @staticmethod\n    def _validate_decision_against_state(\n        *,\n        decision: InvestigationDecision,\n        state: InvestigationState,\n        repair: bool,\n    ) -> None:\n        probe = decision.next_probe\n\n        if (\n            probe is not None\n            and probe in state.attempted_probes\n        ):\n            error_type = (\n                InvestigationReasonerRepairValidationError\n                if repair\n                else InvestigationReasonerValidationError\n            )\n\n            raise error_type(\n                "Investigation reasoner selected an already-attempted probe"\n            )\n\n    @staticmethod\n    def _parse_decision(\n        content: str,\n        *,\n        repair: bool,\n    ) -> InvestigationDecision:\n        try:\n            payload = json.loads(\n                content\n            )\n\n        except json.JSONDecodeError as exc:\n            error_type = (\n                InvestigationReasonerRepairJSONError\n                if repair\n                else InvestigationReasonerJSONError\n            )\n\n            raise error_type(\n                (\n                    "Investigation reasoner repair returned invalid JSON"\n                    if repair\n                    else "Investigation reasoner returned invalid JSON"\n                )\n            ) from exc\n\n        try:\n            return InvestigationDecision.model_validate(\n                payload\n            )\n\n        except (\n            ValidationError,\n            TypeError,\n            ValueError,\n        ) as exc:\n            error_type = (\n                InvestigationReasonerRepairValidationError\n                if repair\n                else InvestigationReasonerValidationError\n            )\n\n            raise error_type(\n                (\n                    "Investigation reasoner repair returned an invalid decision"\n                    if repair\n                    else "Investigation reasoner returned an invalid decision"\n                )\n            ) from exc\n\n    @classmethod\n    def _build_repair_prompt(\n        cls,\n        *,\n        scope: InvestigationScope,\n        state: InvestigationState,\n        primary_error: InvestigationReasonerError,\n    ) -> str:\n        failure_kind = type(\n            primary_error\n        ).__name__\n\n        return (\n            "Your previous decision failed the bounded structured-output "\n            f"contract with failure type {failure_kind}.\\n"\n            "Do not repeat or explain the invalid response.\\n"\n            "Re-evaluate the SAME supplied state. Do not invent evidence, "\n            "do not add a tool call outside allowed_probes, and do not "\n            "change resource scope.\\n"\n            "Return exactly one corrected JSON decision that satisfies every "\n            "shape and evidence rule below.\\n\\n"\n            + cls._build_prompt(\n                scope=scope,\n                state=state,\n            )\n        )\n\n    @staticmethod\n    def _build_prompt(\n        scope: InvestigationScope,\n        state: InvestigationState,\n    ) -> str:\n        trusted_evidence_ids = [\n            item.evidence_id\n            for item in state.evidence\n            if (\n                item.success\n                and item.trusted\n                and item.production_signal\n            )\n        ]\n\n        attempted_probe_set = set(\n            state.attempted_probes\n        )\n\n        state_payload = {\n            "scope": scope.model_dump(\n                mode="json"\n            ),\n            "iteration_count": state.iteration_count,\n            "tool_call_count": state.tool_call_count,\n            "attempted_probes": [\n                probe.value\n                for probe in state.attempted_probes\n            ],\n            "failed_probes": [\n                item.probe.value\n                for item in state.evidence\n                if not item.success\n            ],\n            "hypotheses": [\n                item.model_dump(mode="json")\n                for item in state.hypotheses\n            ],\n            "evidence": [\n                item.model_dump(mode="json")\n                for item in state.evidence\n            ],\n            "trusted_evidence_ids": trusted_evidence_ids,\n            "allowed_probes": [\n                probe.value\n                for probe in InvestigationProbe\n                if probe not in attempted_probe_set\n            ],\n        }\n\n        return (\n            "Investigate the incident using the bounded state below.\\n"\n            "Return exactly one JSON object only. Do not return markdown.\\n"\n            "Maintain competing hypotheses and update confidence from evidence.\\n"\n            "Probe affordances:\\n"\n            "- kubernetes_pod_state: current pod/container state, restart "\n            "indicators, and last termination reasons.\\n"\n            "- kubernetes_previous_container_logs: bounded previous-container "\n            "output; high-information evidence for unexplained restart, startup, "\n            "panic, configuration, dependency, or crash symptoms.\\n"\n            "- prometheus_memory_working_set: sampled container memory usage.\\n"\n            "- prometheus_memory_limit: configured container memory limit.\\n"\n            "- prometheus_restart_count: sampled restart frequency/corroboration.\\n"\n            "If trusted evidence falsifies the current leading hypothesis but "\n            "the observed incident symptom remains unexplained, do not stop "\n            "solely because that hypothesis was rejected. Replan with at least "\n            "one evidence-plausible alternative hypothesis when an unattempted "\n            "allowed probe can materially discriminate plausible causes.\\n"\n            "Use insufficient_evidence only when no unattempted safe probe can "\n            "materially discriminate the remaining plausible causes, or when "\n            "required evidence is unavailable.\\n"\n            "State.allowed_probes already excludes every attempted probe. "\n            "Select next_probe only from State.allowed_probes.\\n"\n            "A failed probe is still an attempted probe. Do not retry it inside "\n            "the same investigation; keep its required evidence missing and "\n            "use another unattempted discriminative probe or safely abstain.\\n"\n            "Never repeat a probe already listed in attempted_probes.\\n"\n            "Never cite an evidence ID that is absent from State.evidence.\\n"\n            "Conflicting evidence can weaken a hypothesis but cannot by itself "\n            "positively establish an unrelated root cause.\\n"\n            "Ruling out one hypothesis is not sufficient evidence for a different "\n            "causal claim.\\n"\n            "Current-state evidence does not by itself prove that a historical "\n            "event never occurred.\\n"\n            "For sufficient_evidence, the conclusion must affirm a positively "\n            "supported current hypothesis. Every conclusion.evidence_id must also "\n            "appear in that hypothesis supporting_evidence_ids and must not be "\n            "conflicting evidence for that same hypothesis.\\n"\n            "Conclusion confidence must not materially exceed the confidence of "\n            "the positively supported hypothesis.\\n"\n            "A symptom or failure-mode observation such as CrashLoopBackOff, "\n            "restart count, unready state, high latency, or high error rate can "\n            "confirm that a failure exists, but does not by itself establish the "\n            "specific underlying cause that produced it.\\n"\n            "If several underlying causes remain plausible and current allowed "\n            "probes cannot discriminate among them, keep the required "\n            "root-cause evidence in hypothesis.missing_evidence and stop with "\n            "insufficient_evidence or no_safe_probe.\\n"\n            "Use hypothesis.missing_evidence only for evidence that is REQUIRED "\n            "before the specific root cause can be accepted. Use "\n            "hypothesis.optional_evidence for corroboration that may increase "\n            "confidence or describe frequency/severity but is not required to "\n            "establish the root cause.\\n"\n            "Do not put the same evidence need in both missing_evidence and "\n            "optional_evidence.\\n"\n            "Do not clear missing_evidence merely because all allowed probes "\n            "have been attempted. For sufficient_evidence, the positively "\n            "supported hypothesis used by the conclusion must have an empty "\n            "missing_evidence list. optional_evidence may remain non-empty.\\n"\n            "Treat event evidence separately from mechanism evidence. For example, "\n            "OOMKilled proves that an OOM termination occurred, but does not by "\n            "itself prove that a configured container memory limit was exceeded.\\n"\n            "A point-in-time or sampled metric cannot establish an unobserved "\n            "transient peak, historical trend, or threshold crossing. Never invent "\n            "an unseen spike to make a hypothesis fit.\\n"\n            "For quantitative threshold causes, supporting evidence must be "\n            "directionally consistent with the claimed mechanism. If a sampled "\n            "working value is below the sampled limit, that sample is not positive "\n            "support for the claim that the limit was exceeded.\\n"\n            "If an event is confirmed but the available sampled metrics do not "\n            "explain its mechanism, keep the required historical/range/peak "\n            "evidence in missing_evidence and stop with insufficient_evidence "\n            "unless another direct causal observation establishes the cause.\\n"\n            "If the available evidence only rejects hypotheses and does not "\n            "positively establish a root cause, stop with insufficient_evidence.\\n"\n            "If the available evidence only rejects hypotheses or confirms a "\n            "symptom/failure mode without establishing its cause, stop with "\n            "insufficient_evidence.\\n"\n            "For a conclusion, evidence_ids must be non-empty and copied "\n            "exactly from trusted_evidence_ids.\\n"\n            "Allowed model terminal stop_reason values are exactly: "\n            "sufficient_evidence, insufficient_evidence, no_safe_probe.\\n"\n            "Do not emit internal coordinator reasons such as timeout, "\n            "max_iterations, max_tool_calls, duplicate_probe or reasoner_error.\\n"\n            "\\n"\n            "Continuing shape:\\n"\n            "{\\n"\n            \'  "hypotheses": [{"hypothesis_id": "h1", "cause": "candidate", \'\n            \'"confidence": 0.5, "supporting_evidence_ids": [], \'\n            \'"conflicting_evidence_ids": [], "missing_evidence": ["required evidence"], "optional_evidence": ["non-blocking corroboration"]}],\\n\'\n            \'  "rationale_summary": "why this probe is most discriminative",\\n\'\n            \'  "stop": false,\\n\'\n            \'  "stop_reason": null,\\n\'\n            \'  "next_probe": "one unattempted value from allowed_probes",\\n\'\n            \'  "conclusion": null\\n\'\n            "}\\n"\n            "\\n"\n            "Terminal sufficient-evidence shape:\\n"\n            "{\\n"\n            \'  "hypotheses": [{"hypothesis_id": "h1", "cause": "supported cause", \'\n            \'"confidence": 0.9, "supporting_evidence_ids": ["copy exact trusted id"], \'\n            \'"conflicting_evidence_ids": [], "missing_evidence": [], "optional_evidence": []}],\\n\'\n            \'  "rationale_summary": "why trusted evidence is sufficient",\\n\'\n            \'  "stop": true,\\n\'\n            \'  "stop_reason": "sufficient_evidence",\\n\'\n            \'  "next_probe": null,\\n\'\n            \'  "conclusion": {"root_cause": "bounded root cause", \'\n            \'"confidence": 0.9, "evidence_ids": ["copy exact trusted id"], \'\n            \'"remaining_uncertainties": []}\\n\'\n            "}\\n"\n            "\\n"\n            "Terminal insufficient/no-safe-probe shape:\\n"\n            "{\\n"\n            \'  "hypotheses": [{"hypothesis_id": "h1", "cause": "candidate", \'\n            \'"confidence": 0.3, "supporting_evidence_ids": [], \'\n            \'"conflicting_evidence_ids": [], "missing_evidence": ["required missing evidence"], "optional_evidence": ["non-blocking evidence if useful"]}],\\n\'\n            \'  "rationale_summary": "why current bounded evidence cannot support an RCA",\\n\'\n            \'  "stop": true,\\n\'\n            \'  "stop_reason": "insufficient_evidence",\\n\'\n            \'  "next_probe": null,\\n\'\n            \'  "conclusion": null\\n\'\n            "}\\n"\n            "\\n"\n            "If no useful unattempted allowed probe can resolve the remaining "\n            "uncertainty, stop with insufficient_evidence or no_safe_probe.\\n"\n            "State:\\n"\n            f"{json.dumps(state_payload, ensure_ascii=True, sort_keys=True)}"\n        )\n\n\n__all__ = [\n    "BaseInvestigationReasoner",\n    "InvestigationReasonerError",\n    "LLMInvestigationReasoner",\n]\n'
BAILIAN_SOURCE = 'from __future__ import annotations\n\nimport asyncio\nimport os\nfrom typing import Any\nfrom urllib.parse import urlparse\n\nimport httpx\n\nfrom services.agent_runtime.app.llm.base import (\n    BaseLLMProvider,\n)\nfrom services.agent_runtime.app.llm.models import (\n    ChatRequest,\n    ChatResponse,\n)\n\n\nclass BailianCompatibleProvider(\n    BaseLLMProvider\n):\n    """\n    Alibaba Cloud Model Studio (Bailian) OpenAI-compatible provider.\n\n    One provider instance owns one lazily-created persistent AsyncClient.\n    Requests therefore reuse the HTTP connection pool and keep-alive\n    connections instead of rebuilding the transport for every chat() call.\n\n    Configuration:\n    - BAILIAN_BASE_URL\n    - DASHSCOPE_API_KEY\n    - BAILIAN_MODEL\n\n    BAILIAN_BASE_URL must already include /compatible-mode/v1.\n    Configuration is validated only when chat() is invoked so registry\n    construction never breaks the safe default mock development path.\n    """\n\n    @property\n    def name(\n        self,\n    ) -> str:\n        return "bailian"\n\n    def __init__(\n        self,\n        *,\n        http_client: (\n            httpx.AsyncClient\n            | None\n        ) = None,\n    ) -> None:\n        self.base_url = os.getenv(\n            "BAILIAN_BASE_URL",\n            "",\n        ).strip().rstrip("/")\n\n        self.api_key = os.getenv(\n            "DASHSCOPE_API_KEY",\n            "",\n        ).strip()\n\n        self.model = os.getenv(\n            "BAILIAN_MODEL",\n            "",\n        ).strip()\n\n        self._http_client = (\n            http_client\n        )\n\n        self._owns_http_client = (\n            http_client is None\n        )\n\n        self._client_lock = (\n            asyncio.Lock()\n        )\n\n    def validate_configuration(\n        self,\n    ) -> None:\n        if not self.base_url:\n            raise RuntimeError(\n                "BAILIAN_BASE_URL is not configured"\n            )\n\n        parsed = urlparse(\n            self.base_url\n        )\n\n        if (\n            parsed.scheme != "https"\n            or not parsed.netloc\n            or parsed.username is not None\n            or parsed.password is not None\n            or parsed.query\n            or parsed.fragment\n        ):\n            raise RuntimeError(\n                "BAILIAN_BASE_URL must be a clean HTTPS URL"\n            )\n\n        if not (\n            parsed.path.rstrip("/")\n            .endswith(\n                "/compatible-mode/v1"\n            )\n        ):\n            raise RuntimeError(\n                "BAILIAN_BASE_URL must end with /compatible-mode/v1"\n            )\n\n        if not self.api_key:\n            raise RuntimeError(\n                "DASHSCOPE_API_KEY is not configured"\n            )\n\n        if not self.model:\n            raise RuntimeError(\n                "BAILIAN_MODEL is not configured"\n            )\n\n    async def _get_http_client(\n        self,\n    ) -> httpx.AsyncClient:\n        if self._http_client is not None:\n            return self._http_client\n\n        async with self._client_lock:\n            if self._http_client is None:\n                self._http_client = (\n                    httpx.AsyncClient(\n                        timeout=httpx.Timeout(\n                            30.0,\n                            connect=10.0,\n                            pool=5.0,\n                        ),\n                        limits=httpx.Limits(\n                            max_connections=20,\n                            max_keepalive_connections=10,\n                            keepalive_expiry=30.0,\n                        ),\n                    )\n                )\n\n        return self._http_client\n\n    async def _invalidate_http_client(\n        self,\n        client: httpx.AsyncClient,\n    ) -> None:\n        """\n        Drop an owned persistent pool after protocol/read/write corruption.\n\n        Executor-level retry remains the only retry owner. This method merely\n        ensures that the next attempt cannot reuse the same potentially stale\n        keep-alive pool.\n        """\n\n        if not self._owns_http_client:\n            return\n\n        should_close = False\n\n        async with self._client_lock:\n            if self._http_client is client:\n                self._http_client = None\n                should_close = True\n\n        if should_close:\n            close = getattr(\n                client,\n                "aclose",\n                None,\n            )\n\n            if callable(\n                close\n            ):\n                await close()\n\n    async def aclose(\n        self,\n    ) -> None:\n        if not self._owns_http_client:\n            return\n\n        async with self._client_lock:\n            client = self._http_client\n            self._http_client = None\n\n            if client is not None:\n                close = getattr(\n                    client,\n                    "aclose",\n                    None,\n                )\n\n                if callable(\n                    close\n                ):\n                    await close()\n\n    async def chat(\n        self,\n        request: ChatRequest,\n    ) -> ChatResponse:\n        self.validate_configuration()\n\n        messages: list[\n            dict[str, Any]\n        ] = []\n\n        if request.system_prompt:\n            messages.append(\n                {\n                    "role": "system",\n                    "content": request.system_prompt,\n                }\n            )\n\n        messages.append(\n            {\n                "role": "user",\n                "content": request.user_prompt,\n            }\n        )\n\n        payload = {\n            "model": self.model,\n            "messages": messages,\n            "temperature": request.temperature,\n        }\n\n        headers = {\n            "Content-Type": "application/json",\n            "Authorization": (\n                f"Bearer {self.api_key}"\n            ),\n        }\n\n        client = await self._get_http_client()\n\n        try:\n            response = await client.post(\n                (\n                    f"{self.base_url}"\n                    "/chat/completions"\n                ),\n                json=payload,\n                headers=headers,\n            )\n\n        except (\n            httpx.RemoteProtocolError,\n            httpx.ReadError,\n            httpx.WriteError,\n        ):\n            await self._invalidate_http_client(\n                client\n            )\n            raise\n\n        response.raise_for_status()\n\n        data = response.json()\n\n        choices = data.get(\n            "choices"\n        )\n\n        if (\n            not isinstance(\n                choices,\n                list,\n            )\n            or not choices\n            or not isinstance(\n                choices[0],\n                dict,\n            )\n        ):\n            raise RuntimeError(\n                "Bailian response choices are invalid"\n            )\n\n        message = choices[0].get(\n            "message"\n        )\n\n        if not isinstance(\n            message,\n            dict,\n        ):\n            raise RuntimeError(\n                "Bailian response message is invalid"\n            )\n\n        content = message.get(\n            "content"\n        )\n\n        if (\n            not isinstance(\n                content,\n                str,\n            )\n            or not content.strip()\n        ):\n            raise RuntimeError(\n                "Bailian response content is invalid"\n            )\n\n        usage = data.get(\n            "usage",\n            {},\n        )\n\n        if not isinstance(\n            usage,\n            dict,\n        ):\n            usage = {}\n\n        return ChatResponse(\n            content=content,\n            model=data.get(\n                "model",\n                self.model,\n            ),\n            prompt_tokens=usage.get(\n                "prompt_tokens",\n                0,\n            ),\n            completion_tokens=usage.get(\n                "completion_tokens",\n                0,\n            ),\n            total_tokens=usage.get(\n                "total_tokens",\n                0,\n            ),\n        )\n\n\n__all__ = [\n    "BailianCompatibleProvider",\n]\n'
OPENAI_SOURCE = 'from __future__ import annotations\n\nimport asyncio\nimport os\nfrom typing import Any\n\nimport httpx\n\nfrom services.agent_runtime.app.llm.base import (\n    BaseLLMProvider,\n)\nfrom services.agent_runtime.app.llm.models import (\n    ChatRequest,\n    ChatResponse,\n)\n\n\nclass OpenAICompatibleProvider(\n    BaseLLMProvider\n):\n    """\n    OpenAI-compatible LLM provider.\n\n    One provider instance owns one lazily-created persistent AsyncClient so\n    repeated calls reuse the HTTP connection pool.\n\n    Compatible with:\n    - OpenAI\n    - vLLM\n    - Ollama\n    - other OpenAI API compatible servers\n    """\n\n    @property\n    def name(\n        self,\n    ) -> str:\n        return "openai"\n\n    def __init__(\n        self,\n        *,\n        http_client: (\n            httpx.AsyncClient\n            | None\n        ) = None,\n    ) -> None:\n        self.base_url = os.getenv(\n            "OPENAI_BASE_URL",\n            "https://api.openai.com/v1",\n        ).strip().rstrip("/")\n\n        self.api_key = os.getenv(\n            "OPENAI_API_KEY",\n            "",\n        ).strip()\n\n        self.model = os.getenv(\n            "OPENAI_MODEL",\n            "gpt-5",\n        ).strip()\n\n        self._http_client = (\n            http_client\n        )\n\n        self._owns_http_client = (\n            http_client is None\n        )\n\n        self._client_lock = (\n            asyncio.Lock()\n        )\n\n    async def _get_http_client(\n        self,\n    ) -> httpx.AsyncClient:\n        if self._http_client is not None:\n            return self._http_client\n\n        async with self._client_lock:\n            if self._http_client is None:\n                self._http_client = (\n                    httpx.AsyncClient(\n                        timeout=httpx.Timeout(\n                            30.0,\n                            connect=10.0,\n                            pool=5.0,\n                        ),\n                        limits=httpx.Limits(\n                            max_connections=20,\n                            max_keepalive_connections=10,\n                            keepalive_expiry=30.0,\n                        ),\n                    )\n                )\n\n        return self._http_client\n\n    async def _invalidate_http_client(\n        self,\n        client: httpx.AsyncClient,\n    ) -> None:\n        """\n        Drop an owned persistent pool after protocol/read/write corruption.\n\n        Executor-level retry remains the only retry owner. This method merely\n        ensures that the next attempt cannot reuse the same potentially stale\n        keep-alive pool.\n        """\n\n        if not self._owns_http_client:\n            return\n\n        should_close = False\n\n        async with self._client_lock:\n            if self._http_client is client:\n                self._http_client = None\n                should_close = True\n\n        if should_close:\n            close = getattr(\n                client,\n                "aclose",\n                None,\n            )\n\n            if callable(\n                close\n            ):\n                await close()\n\n    async def aclose(\n        self,\n    ) -> None:\n        if not self._owns_http_client:\n            return\n\n        async with self._client_lock:\n            client = self._http_client\n            self._http_client = None\n\n            if client is not None:\n                close = getattr(\n                    client,\n                    "aclose",\n                    None,\n                )\n\n                if callable(\n                    close\n                ):\n                    await close()\n\n    async def chat(\n        self,\n        request: ChatRequest,\n    ) -> ChatResponse:\n        messages: list[\n            dict[str, Any]\n        ] = []\n\n        if request.system_prompt:\n            messages.append(\n                {\n                    "role": "system",\n                    "content": request.system_prompt,\n                }\n            )\n\n        messages.append(\n            {\n                "role": "user",\n                "content": request.user_prompt,\n            }\n        )\n\n        payload = {\n            "model": self.model,\n            "messages": messages,\n            "temperature": request.temperature,\n        }\n\n        headers = {\n            "Content-Type": "application/json",\n        }\n\n        if self.api_key:\n            headers[\n                "Authorization"\n            ] = (\n                f"Bearer {self.api_key}"\n            )\n\n        client = await self._get_http_client()\n\n        try:\n            response = await client.post(\n                (\n                    f"{self.base_url}"\n                    "/chat/completions"\n                ),\n                json=payload,\n                headers=headers,\n            )\n\n        except (\n            httpx.RemoteProtocolError,\n            httpx.ReadError,\n            httpx.WriteError,\n        ):\n            await self._invalidate_http_client(\n                client\n            )\n            raise\n\n        response.raise_for_status()\n\n        data = response.json()\n\n        message = (\n            data[\n                "choices"\n            ][\n                0\n            ][\n                "message"\n            ][\n                "content"\n            ]\n        )\n\n        usage = data.get(\n            "usage",\n            {},\n        )\n\n        return ChatResponse(\n            content=message,\n            model=data.get(\n                "model",\n                self.model,\n            ),\n            prompt_tokens=usage.get(\n                "prompt_tokens",\n                0,\n            ),\n            completion_tokens=usage.get(\n                "completion_tokens",\n                0,\n            ),\n            total_tokens=usage.get(\n                "total_tokens",\n                0,\n            ),\n        )\n\n\n__all__ = [\n    "OpenAICompatibleProvider",\n]\n'
EXECUTION_TEST_SOURCE = 'from __future__ import annotations\n\nimport json\n\nimport pytest\n\nfrom services.agent_runtime.app.investigation.llm_gateway_adapter import (\n    BaseInvestigationLLM,\n    InvestigationLLMExecutionError,\n    InvestigationLLMUnavailableError,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    EvidenceItem,\n    IncidentHypothesis,\n    InvestigationDecision,\n    InvestigationProbe,\n    InvestigationScope,\n    InvestigationState,\n)\nfrom services.agent_runtime.app.investigation.reasoner import (\n    InvestigationReasonerRepairValidationError,\n    LLMInvestigationReasoner,\n)\n\n\nclass SequenceInvestigationLLM(\n    BaseInvestigationLLM\n):\n    def __init__(\n        self,\n        values,\n    ) -> None:\n        self.values = list(\n            values\n        )\n        self.calls = []\n\n    async def complete(\n        self,\n        *,\n        system_prompt: str,\n        prompt: str,\n    ) -> str:\n        self.calls.append(\n            {\n                "system_prompt": system_prompt,\n                "prompt": prompt,\n            }\n        )\n\n        value = self.values.pop(\n            0\n        )\n\n        if isinstance(\n            value,\n            BaseException,\n        ):\n            raise value\n\n        return value\n\n\ndef scope() -> InvestigationScope:\n    return InvestigationScope(\n        alert_name="PodRestartHigh",\n        alert_message="payment-api restarts are increasing",\n        resource="payment-api",\n        namespace="payment",\n        cluster="benchmark-lab",\n    )\n\n\ndef valid_probe_decision(\n    probe: str,\n) -> str:\n    return json.dumps(\n        {\n            "hypotheses": [\n                {\n                    "hypothesis_id": "h1",\n                    "cause": "unresolved restart cause",\n                    "confidence": 0.4,\n                    "supporting_evidence_ids": [],\n                    "conflicting_evidence_ids": [],\n                    "missing_evidence": [\n                        "root-cause evidence"\n                    ],\n                    "optional_evidence": [],\n                }\n            ],\n            "rationale_summary": (\n                "select the next unattempted discriminative probe"\n            ),\n            "stop": False,\n            "stop_reason": None,\n            "next_probe": probe,\n            "conclusion": None,\n        }\n    )\n\n\n@pytest.mark.asyncio\nasync def test_execution_error_is_not_retried_by_reasoner():\n    llm = SequenceInvestigationLLM(\n        [\n            InvestigationLLMExecutionError(\n                "sanitized execution failure"\n            ),\n            valid_probe_decision(\n                "kubernetes_pod_state"\n            ),\n        ]\n    )\n\n    reasoner = LLMInvestigationReasoner(\n        llm\n    )\n\n    current = InvestigationState(\n        scope=scope()\n    )\n\n    with pytest.raises(\n        InvestigationLLMExecutionError,\n    ):\n        await reasoner.decide(\n            current.scope,\n            current,\n        )\n\n    # Gateway/Executor owns transport retry. Reasoner must not multiply\n    # transport attempts or circuit-breaker failure accounting.\n    assert len(\n        llm.calls\n    ) == 1\n\n\n@pytest.mark.asyncio\nasync def test_unavailable_error_is_not_retried():\n    llm = SequenceInvestigationLLM(\n        [\n            InvestigationLLMUnavailableError(\n                "unavailable"\n            ),\n        ]\n    )\n\n    reasoner = LLMInvestigationReasoner(\n        llm\n    )\n\n    current = InvestigationState(\n        scope=scope()\n    )\n\n    with pytest.raises(\n        InvestigationLLMUnavailableError,\n    ):\n        await reasoner.decide(\n            current.scope,\n            current,\n        )\n\n    assert len(\n        llm.calls\n    ) == 1\n\n\n@pytest.mark.asyncio\nasync def test_duplicate_probe_decision_gets_one_contract_repair():\n    llm = SequenceInvestigationLLM(\n        [\n            valid_probe_decision(\n                "kubernetes_previous_container_logs"\n            ),\n            valid_probe_decision(\n                "kubernetes_pod_state"\n            ),\n        ]\n    )\n\n    reasoner = LLMInvestigationReasoner(\n        llm\n    )\n\n    current = InvestigationState(\n        scope=scope(),\n        attempted_probes=[\n            InvestigationProbe.KUBERNETES_PREVIOUS_CONTAINER_LOGS\n        ],\n    )\n\n    decision = await reasoner.decide(\n        current.scope,\n        current,\n    )\n\n    assert (\n        decision.next_probe\n        == InvestigationProbe.KUBERNETES_POD_STATE\n    )\n\n    assert len(\n        llm.calls\n    ) == 2\n\n    assert (\n        "kubernetes_previous_container_logs"\n        in llm.calls[1]["prompt"]\n    )\n\n    assert (\n        \'"allowed_probes": ["kubernetes_pod_state"\'\n        in llm.calls[1]["prompt"]\n    )\n\n\n@pytest.mark.asyncio\nasync def test_duplicate_probe_after_repair_still_fails_closed():\n    duplicate = valid_probe_decision(\n        "kubernetes_previous_container_logs"\n    )\n\n    llm = SequenceInvestigationLLM(\n        [\n            duplicate,\n            duplicate,\n        ]\n    )\n\n    reasoner = LLMInvestigationReasoner(\n        llm\n    )\n\n    current = InvestigationState(\n        scope=scope(),\n        attempted_probes=[\n            InvestigationProbe.KUBERNETES_PREVIOUS_CONTAINER_LOGS\n        ],\n    )\n\n    with pytest.raises(\n        InvestigationReasonerRepairValidationError,\n    ):\n        await reasoner.decide(\n            current.scope,\n            current,\n        )\n\n    assert len(\n        llm.calls\n    ) == 2\n\n\ndef test_prompt_allowed_probes_excludes_attempted_failed_probe():\n    current = InvestigationState(\n        scope=scope(),\n        attempted_probes=[\n            InvestigationProbe.KUBERNETES_PREVIOUS_CONTAINER_LOGS\n        ],\n        evidence=[\n            EvidenceItem(\n                evidence_id="failed-log",\n                probe=(\n                    InvestigationProbe\n                    .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n                ),\n                source="kubernetes",\n                success=False,\n                trusted=False,\n                production_signal=False,\n                reliability=0.0,\n                observed_at=__import__("datetime").datetime.now(\n                    __import__("datetime").UTC\n                ),\n                facts={},\n                error_code="RuntimeError",\n            )\n        ],\n    )\n\n    prompt = (\n        LLMInvestigationReasoner\n        ._build_prompt(\n            scope=current.scope,\n            state=current,\n        )\n    )\n\n    parts = prompt.rsplit(\n        "State:\\n",\n        1,\n    )\n\n    assert len(\n        parts\n    ) == 2\n\n    state_json = parts[\n        1\n    ]\n\n    payload = json.loads(\n        state_json\n    )\n\n    assert (\n        "kubernetes_previous_container_logs"\n        not in payload[\n            "allowed_probes"\n        ]\n    )\n\n    assert (\n        "kubernetes_previous_container_logs"\n        in payload[\n            "failed_probes"\n        ]\n    )\n\n    assert (\n        "A failed probe is still an attempted probe"\n        in prompt\n    )\n'
TRANSPORT_TEST_SOURCE = 'from __future__ import annotations\n\nfrom types import SimpleNamespace\n\nimport httpx\nimport pytest\n\nimport services.agent_runtime.app.llm.gateway.gateway as gateway_module\n\nfrom services.agent_runtime.app.llm.gateway.executor import (\n    LLMExecutionError,\n    LLMExecutor,\n)\nfrom services.agent_runtime.app.llm.gateway.gateway import (\n    LLMGateway,\n)\nfrom services.agent_runtime.app.llm.gateway.models import (\n    LLMGatewayRequest,\n    LLMInvocationContext,\n    LLMPriority,\n    LLMTaskType,\n)\nfrom services.agent_runtime.app.llm.gateway.provider_health import (\n    ProviderHealthManager,\n)\nfrom services.agent_runtime.app.llm.models import (\n    ChatRequest,\n    ChatResponse,\n)\nfrom services.agent_runtime.app.llm.providers.bailian_compatible import (\n    BailianCompatibleProvider,\n)\nfrom services.agent_runtime.app.llm.providers.openai_compatible import (\n    OpenAICompatibleProvider,\n)\n\n\ndef ok_response(\n    content: str = "{}",\n) -> ChatResponse:\n    return ChatResponse(\n        content=content,\n        model="unit-model",\n        prompt_tokens=1,\n        completion_tokens=1,\n        total_tokens=2,\n    )\n\n\nclass SequenceClient:\n    def __init__(\n        self,\n        values,\n    ) -> None:\n        self.values = list(\n            values\n        )\n\n        self.calls = 0\n\n    async def chat(\n        self,\n        request,\n    ):\n        self.calls += 1\n\n        value = self.values.pop(\n            0\n        )\n\n        if isinstance(\n            value,\n            BaseException,\n        ):\n            raise value\n\n        return value\n\n\ndef status_error(\n    status_code: int,\n) -> httpx.HTTPStatusError:\n    request = httpx.Request(\n        "POST",\n        "https://unit.invalid/v1/chat/completions",\n    )\n\n    response = httpx.Response(\n        status_code,\n        request=request,\n    )\n\n    return httpx.HTTPStatusError(\n        "upstream status",\n        request=request,\n        response=response,\n    )\n\n\n@pytest.mark.asyncio\nasync def test_executor_retries_transport_error_with_exponential_backoff():\n    delays = []\n\n    async def sleep(\n        delay: float,\n    ) -> None:\n        delays.append(\n            delay\n        )\n\n    client = SequenceClient(\n        [\n            httpx.RemoteProtocolError(\n                "server disconnected"\n            ),\n            httpx.ConnectError(\n                "connect reset"\n            ),\n            ok_response(),\n        ]\n    )\n\n    executor = LLMExecutor(\n        retry_attempts=3,\n        timeout=30,\n        retry_base_delay=0.25,\n        retry_max_delay=2.0,\n        retry_jitter_ratio=0.2,\n        sleep_func=sleep,\n        random_func=lambda: 0.5,\n    )\n\n    response = await executor.execute(\n        client,\n        ChatRequest(\n            system_prompt="system",\n            user_prompt="user",\n            temperature=0.0,\n        ),\n    )\n\n    assert response.model == "unit-model"\n    assert client.calls == 3\n\n    assert delays == [\n        0.25,\n        0.5,\n    ]\n\n\n@pytest.mark.asyncio\nasync def test_executor_retries_503_but_not_401():\n    delays = []\n\n    async def sleep(\n        delay: float,\n    ) -> None:\n        delays.append(\n            delay\n        )\n\n    retry_client = SequenceClient(\n        [\n            status_error(\n                503\n            ),\n            ok_response(),\n        ]\n    )\n\n    executor = LLMExecutor(\n        retry_attempts=3,\n        sleep_func=sleep,\n        random_func=lambda: 0.5,\n    )\n\n    await executor.execute(\n        retry_client,\n        ChatRequest(\n            system_prompt="s",\n            user_prompt="u",\n            temperature=0.0,\n        ),\n    )\n\n    assert retry_client.calls == 2\n    assert delays == [\n        0.25\n    ]\n\n    non_retry_client = SequenceClient(\n        [\n            status_error(\n                401\n            ),\n            ok_response(),\n        ]\n    )\n\n    with pytest.raises(\n        LLMExecutionError,\n    ) as exc_info:\n        await executor.execute(\n            non_retry_client,\n            ChatRequest(\n                system_prompt="s",\n                user_prompt="u",\n                temperature=0.0,\n            ),\n        )\n\n    assert non_retry_client.calls == 1\n    assert exc_info.value.retryable is False\n    assert exc_info.value.code == "http_401"\n    assert exc_info.value.attempts == 1\n\n\n@pytest.mark.asyncio\nasync def test_executor_logs_are_sanitized(\n    capsys,\n):\n    secret = (\n        "https://secret-host.invalid/"\n        "?token=do-not-log"\n    )\n\n    client = SequenceClient(\n        [\n            httpx.ConnectError(\n                secret\n            ),\n        ]\n    )\n\n    executor = LLMExecutor(\n        retry_attempts=1,\n    )\n\n    with pytest.raises(\n        LLMExecutionError,\n    ):\n        await executor.execute(\n            client,\n            ChatRequest(\n                system_prompt="s",\n                user_prompt="u",\n                temperature=0.0,\n            ),\n        )\n\n    output = capsys.readouterr().out\n\n    assert (\n        "do-not-log"\n        not in output\n    )\n\n    assert (\n        "secret-host"\n        not in output\n    )\n\n    assert (\n        "transport_error"\n        in output\n    )\n\n\ndef configure_bailian(\n    monkeypatch,\n) -> None:\n    monkeypatch.setenv(\n        "BAILIAN_BASE_URL",\n        (\n            "https://example.aliyuncs.com"\n            "/compatible-mode/v1"\n        ),\n    )\n\n    monkeypatch.setenv(\n        "DASHSCOPE_API_KEY",\n        "unit-secret",\n    )\n\n    monkeypatch.setenv(\n        "BAILIAN_MODEL",\n        "qwen-plus",\n    )\n\n\nclass FakeResponse:\n    def raise_for_status(\n        self,\n    ) -> None:\n        return None\n\n    def json(\n        self,\n    ):\n        return {\n            "model": "unit-model",\n            "choices": [\n                {\n                    "message": {\n                        "content": "{}",\n                    }\n                }\n            ],\n            "usage": {},\n        }\n\n\nclass CountingFakeClient:\n    def __init__(\n        self,\n        *args,\n        **kwargs,\n    ) -> None:\n        self.posts = 0\n        self.closed = False\n\n    async def post(\n        self,\n        *args,\n        **kwargs,\n    ):\n        self.posts += 1\n        return FakeResponse()\n\n    async def aclose(\n        self,\n    ) -> None:\n        self.closed = True\n\n\n@pytest.mark.asyncio\nasync def test_bailian_provider_reuses_one_http_client(\n    monkeypatch,\n):\n    configure_bailian(\n        monkeypatch\n    )\n\n    created = []\n\n    def factory(\n        *args,\n        **kwargs,\n    ):\n        client = CountingFakeClient(\n            *args,\n            **kwargs,\n        )\n\n        created.append(\n            client\n        )\n\n        return client\n\n    monkeypatch.setattr(\n        httpx,\n        "AsyncClient",\n        factory,\n    )\n\n    provider = (\n        BailianCompatibleProvider()\n    )\n\n    request = ChatRequest(\n        system_prompt="s",\n        user_prompt="u",\n        temperature=0.0,\n    )\n\n    await provider.chat(\n        request\n    )\n\n    await provider.chat(\n        request\n    )\n\n    assert len(\n        created\n    ) == 1\n\n    assert created[\n        0\n    ].posts == 2\n\n    await provider.aclose()\n\n    assert created[\n        0\n    ].closed is True\n\n\n@pytest.mark.asyncio\nasync def test_openai_provider_reuses_one_http_client(\n    monkeypatch,\n):\n    monkeypatch.setenv(\n        "OPENAI_BASE_URL",\n        "https://api.example.invalid/v1",\n    )\n\n    monkeypatch.setenv(\n        "OPENAI_API_KEY",\n        "unit-secret",\n    )\n\n    monkeypatch.setenv(\n        "OPENAI_MODEL",\n        "unit-model",\n    )\n\n    created = []\n\n    def factory(\n        *args,\n        **kwargs,\n    ):\n        client = CountingFakeClient(\n            *args,\n            **kwargs,\n        )\n\n        created.append(\n            client\n        )\n\n        return client\n\n    monkeypatch.setattr(\n        httpx,\n        "AsyncClient",\n        factory,\n    )\n\n    provider = (\n        OpenAICompatibleProvider()\n    )\n\n    request = ChatRequest(\n        system_prompt="s",\n        user_prompt="u",\n        temperature=0.0,\n    )\n\n    await provider.chat(\n        request\n    )\n\n    await provider.chat(\n        request\n    )\n\n    assert len(\n        created\n    ) == 1\n\n    assert created[\n        0\n    ].posts == 2\n\n    await provider.aclose()\n\n    assert created[\n        0\n    ].closed is True\n\n\ndef gateway_settings():\n    return SimpleNamespace(\n        llm=SimpleNamespace(\n            gateway=SimpleNamespace(\n                retry_attempts=3,\n                request_timeout=30,\n                rate_limit=SimpleNamespace(\n                    enabled=False,\n                    requests_per_minute=60,\n                ),\n            )\n        )\n    )\n\n\nclass FixedRouter:\n    def __init__(\n        self,\n        provider: str = "openai",\n    ) -> None:\n        self.provider = provider\n\n    def route(\n        self,\n        context,\n    ):\n        return SimpleNamespace(\n            provider=self.provider,\n        )\n\n\nclass RaisingExecutor:\n    def __init__(\n        self,\n        error,\n    ) -> None:\n        self.error = error\n        self.calls = 0\n\n    async def execute(\n        self,\n        client,\n        request,\n    ):\n        self.calls += 1\n        raise self.error\n\n\nclass NeverFallback:\n    def __init__(\n        self,\n    ) -> None:\n        self.calls = 0\n\n    def get_fallback(\n        self,\n        failed_provider,\n    ):\n        self.calls += 1\n        return None\n\n\ndef request(\n    *,\n    enable_fallback: bool = True,\n) -> LLMGatewayRequest:\n    return LLMGatewayRequest(\n        system_prompt="s",\n        prompt="u",\n        context=LLMInvocationContext(\n            agent="unit",\n            task=LLMTaskType.ANALYSIS,\n            priority=LLMPriority.HIGH,\n            require_json=True,\n            preferred_provider=None,\n            preferred_model=None,\n            enable_fallback=enable_fallback,\n        ),\n        temperature=0.0,\n    )\n\n\n@pytest.mark.asyncio\nasync def test_gateway_does_not_mark_provider_unhealthy_for_non_retryable_error(\n    monkeypatch,\n):\n    monkeypatch.setattr(\n        gateway_module,\n        "get_settings",\n        gateway_settings,\n    )\n\n    health = ProviderHealthManager(\n        [\n            "openai"\n        ]\n    )\n\n    fallback = NeverFallback()\n\n    gateway = LLMGateway(\n        clients={\n            "openai": object(),\n        },\n        router=FixedRouter(),\n        executor=RaisingExecutor(\n            LLMExecutionError(\n                "bad request",\n                code="http_401",\n                retryable=False,\n                attempts=1,\n            )\n        ),\n        fallback_manager=fallback,\n        health_manager=health,\n    )\n\n    with pytest.raises(\n        LLMExecutionError,\n    ):\n        await gateway.chat(\n            request()\n        )\n\n    assert health.is_healthy(\n        "openai"\n    )\n\n    assert (\n        gateway\n        .circuit_breaker\n        .failure_count\n        == 0\n    )\n\n    assert fallback.calls == 0\n\n\n@pytest.mark.asyncio\nasync def test_gateway_marks_transient_provider_failure_and_respects_fallback_flag(\n    monkeypatch,\n):\n    monkeypatch.setattr(\n        gateway_module,\n        "get_settings",\n        gateway_settings,\n    )\n\n    health = ProviderHealthManager(\n        [\n            "openai"\n        ]\n    )\n\n    fallback = NeverFallback()\n\n    gateway = LLMGateway(\n        clients={\n            "openai": object(),\n        },\n        router=FixedRouter(),\n        executor=RaisingExecutor(\n            LLMExecutionError(\n                "transport",\n                code="transport_error",\n                retryable=True,\n                attempts=3,\n            )\n        ),\n        fallback_manager=fallback,\n        health_manager=health,\n    )\n\n    with pytest.raises(\n        LLMExecutionError,\n    ):\n        await gateway.chat(\n            request(\n                enable_fallback=False\n            )\n        )\n\n    assert not health.is_healthy(\n        "openai"\n    )\n\n    assert (\n        gateway\n        .circuit_breaker\n        .failure_count\n        == 1\n    )\n\n    assert fallback.calls == 0\n\nclass ResettableFakeClient:\n    def __init__(\n        self,\n        *,\n        fail_protocol: bool,\n    ) -> None:\n        self.fail_protocol = fail_protocol\n        self.closed = False\n\n    async def post(\n        self,\n        *args,\n        **kwargs,\n    ):\n        if self.fail_protocol:\n            raise httpx.RemoteProtocolError(\n                "stale keep-alive connection"\n            )\n\n        return FakeResponse()\n\n    async def aclose(\n        self,\n    ) -> None:\n        self.closed = True\n\n\n@pytest.mark.asyncio\nasync def test_bailian_protocol_error_discards_owned_pool_before_next_attempt(\n    monkeypatch,\n):\n    configure_bailian(\n        monkeypatch\n    )\n\n    created = []\n\n    def factory(\n        *args,\n        **kwargs,\n    ):\n        client = ResettableFakeClient(\n            fail_protocol=(\n                len(created)\n                == 0\n            )\n        )\n\n        created.append(\n            client\n        )\n\n        return client\n\n    monkeypatch.setattr(\n        httpx,\n        "AsyncClient",\n        factory,\n    )\n\n    provider = (\n        BailianCompatibleProvider()\n    )\n\n    request_value = ChatRequest(\n        system_prompt="s",\n        user_prompt="u",\n        temperature=0.0,\n    )\n\n    with pytest.raises(\n        httpx.RemoteProtocolError,\n    ):\n        await provider.chat(\n            request_value\n        )\n\n    assert len(\n        created\n    ) == 1\n\n    assert created[\n        0\n    ].closed is True\n\n    response = await provider.chat(\n        request_value\n    )\n\n    assert response.model == "unit-model"\n    assert len(\n        created\n    ) == 2\n\n\n@pytest.mark.asyncio\nasync def test_openai_protocol_error_discards_owned_pool_before_next_attempt(\n    monkeypatch,\n):\n    monkeypatch.setenv(\n        "OPENAI_BASE_URL",\n        "https://api.example.invalid/v1",\n    )\n\n    monkeypatch.setenv(\n        "OPENAI_API_KEY",\n        "unit-secret",\n    )\n\n    monkeypatch.setenv(\n        "OPENAI_MODEL",\n        "unit-model",\n    )\n\n    created = []\n\n    def factory(\n        *args,\n        **kwargs,\n    ):\n        client = ResettableFakeClient(\n            fail_protocol=(\n                len(created)\n                == 0\n            )\n        )\n\n        created.append(\n            client\n        )\n\n        return client\n\n    monkeypatch.setattr(\n        httpx,\n        "AsyncClient",\n        factory,\n    )\n\n    provider = (\n        OpenAICompatibleProvider()\n    )\n\n    request_value = ChatRequest(\n        system_prompt="s",\n        user_prompt="u",\n        temperature=0.0,\n    )\n\n    with pytest.raises(\n        httpx.RemoteProtocolError,\n    ):\n        await provider.chat(\n            request_value\n        )\n\n    assert len(\n        created\n    ) == 1\n\n    assert created[\n        0\n    ].closed is True\n\n    response = await provider.chat(\n        request_value\n    )\n\n    assert response.model == "unit-model"\n    assert len(\n        created\n    ) == 2\n\n'


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

    bailian_file = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "llm"
        / "providers"
        / "bailian_compatible.py"
    )

    openai_file = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "llm"
        / "providers"
        / "openai_compatible.py"
    )

    execution_test_file = (
        root
        / "services"
        / "agent_runtime"
        / "tests"
        / "test_investigation_execution_resilience.py"
    )

    transport_test_file = (
        root
        / "services"
        / "agent_runtime"
        / "tests"
        / "test_llm_gateway_transport_reliability.py"
    )

    sources = {
        reasoner_file: REASONER_SOURCE,
        bailian_file: BAILIAN_SOURCE,
        openai_file: OPENAI_SOURCE,
        execution_test_file: EXECUTION_TEST_SOURCE,
        transport_test_file: TRANSPORT_TEST_SOURCE,
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
        "LLM Gateway Transport Reliability v2",
        f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
        "",
        "Full x3 result that motivated v2:",
        "- transport v1 reduced raw retry log volume but did not improve final stability",
        "- post-v1 Full x3 produced 15/21 correct outcomes",
        "- five scenarios still ended in reasoner_error",
        "- failures clustered after repeated transport errors and then CircuitBreakerOpen",
        "",
        "Retry ownership correction:",
        "- shared LLM Gateway/Executor is now the sole transport-retry owner",
        "- Investigation Reasoner no longer repeats a failed Gateway execution",
        "- one logical reasoning request can therefore consume at most the Gateway retry_attempts ceiling",
        "- structured Decision-contract repair remains unchanged and still allows one second model call only after a response was received but invalid",
        "",
        "Persistent-pool recovery:",
        "- Bailian/OpenAI providers still reuse one persistent AsyncClient during healthy operation",
        "- RemoteProtocolError/ReadError/WriteError invalidates the owned pool before the error is re-raised",
        "- the next Executor retry therefore creates a fresh connection pool",
        "- ConnectError itself does not churn the pool because it already represents connection establishment failure",
        "",
        "Circuit impact:",
        "- one exhausted logical Gateway request records at most one circuit failure",
        "- Reasoner no longer doubles circuit failure accounting for the same decision",
        "- CircuitBreaker implementation and thresholds are not changed in v2",
        "",
        "Unchanged:",
        "- no Investigation evidence/Guard/Probe behavior changes",
        "- no Tool/Kubernetes/Prometheus changes",
        "- no Action/Approval/Verification changes",
        "- no retry_attempts increase",
        "- no app.yaml/settings schema change",
        "- installer sends no external request",
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
                "Python syntax verification failed"
            )

        focused = run_command(
            root=root,
            name="Transport Reliability v2 focused suite",
            command=[
                "uv",
                "run",
                "pytest",
                (
                    "services/agent_runtime/tests/"
                    "test_llm_gateway_transport_reliability.py"
                ),
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
                    "test_bailian_provider.py"
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
                "Transport Reliability v2 focused tests failed"
            )

        compatibility = run_command(
            root=root,
            name="Gateway / Investigation compatibility suite",
            command=[
                "uv",
                "run",
                "pytest",
                (
                    "services/agent_runtime/tests/"
                    "test_shared_llm_gateway_ownership.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_llm_shadow_execution.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_real_llm_historical_run.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_intelligence_benchmark.py"
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
                "Gateway / Investigation compatibility tests failed"
            )

        preflight = run_command(
            root=root,
            name="Retry ownership / pool reset preflight",
            command=[
                "uv",
                "run",
                "python",
                "-c",
                (
                    "from pathlib import Path; "
                    "r=Path(r'services/agent_runtime/app/investigation/reasoner.py')"
                    ".read_text(encoding='utf-8'); "
                    "b=Path(r'services/agent_runtime/app/llm/providers/bailian_compatible.py')"
                    ".read_text(encoding='utf-8'); "
                    "o=Path(r'services/agent_runtime/app/llm/providers/openai_compatible.py')"
                    ".read_text(encoding='utf-8'); "
                    "print('reasoner_outer_retry='+str('_complete_with_execution_retry' in r)); "
                    "print('bailian_pool_invalidation='+str('_invalidate_http_client' in b)); "
                    "print('openai_pool_invalidation='+str('_invalidate_http_client' in o)); "
                    "assert '_complete_with_execution_retry' not in r; "
                    "assert '_invalidate_http_client' in b; "
                    "assert '_invalidate_http_client' in o"
                ),
            ],
        )

        add_command(
            report,
            preflight,
        )

        if preflight.returncode != 0:
            raise RuntimeError(
                "Retry ownership / pool reset preflight failed"
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
                    "Path(r'services/agent_runtime/app/llm/providers/bailian_compatible.py'),"
                    "Path(r'services/agent_runtime/app/llm/providers/openai_compatible.py')"
                    "]; "
                    "s='\\n'.join(x.read_text(encoding='utf-8') for x in files); "
                    "bad=[x for x in ['ActionRuntime','ApprovalService',"
                    "'VerificationRuntime','kubectl'] if x in s]; "
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
                "Transport Reliability v2 authority boundary failed"
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
                "LLM Gateway Transport Reliability v2 is installed.",
                "",
                "Expected live behavior:",
                "- one Investigation Decision no longer multiplies Gateway transport retries",
                "- transient protocol corruption rotates the persistent provider pool before Executor retry",
                "- circuit failure accounting is one logical Gateway failure per failed reasoning call",
                "- healthy traffic still benefits from persistent keep-alive reuse",
                "",
                "Next acceptance:",
                "rerun Full 7 scenarios x3 through the existing single-bundle runner.",
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
            "LLM GATEWAY TRANSPORT RELIABILITY V2 PASSED"
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
                    "LLM Gateway Transport Reliability v2 FAILED",
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
            "LLM GATEWAY TRANSPORT RELIABILITY V2 FAILED"
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
