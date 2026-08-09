from __future__ import annotations

import hashlib
import shutil
import subprocess
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


VERSION = "logs-investigation-v2"

AFTER_NAME = (
    "logs_investigation_v2_after.txt"
)

ERROR_NAME = (
    "logs_investigation_v2_error.txt"
)

EXPECTED_HASHES = {'services/agent_runtime/app/investigation/models.py': '7681a529ff50c4178337dd170d77bfb2c25759d7667b7bd2646b31c7a563c7b7', 'services/agent_runtime/app/investigation/probes.py': '93029638722b3ab450c2e1b931442bad0214b054e077efbe81baf8c4abfb3030', 'services/agent_runtime/app/investigation/evidence_time.py': 'f4acf25e602cb9a9f7084a9047142edb5f7817cc4067348c4bd396083d721bc5', 'services/agent_runtime/app/tools/kubernetes/tool.py': 'e4aa6ab87f61260453227f2ece44419e148d7fad8d4e9023a023a6e8d78ffb20', 'services/agent_runtime/app/evaluation/intelligence_benchmark/engine.py': 'e2ce1bb788c96aed8222c02cf8521fffc1e3b0a8e4479bd79f352564313359d5', 'services/agent_runtime/app/evaluation/intelligence_benchmark/scenarios.py': 'e3c08d4ce08bd3cdecc8533932fbccf2f7f9b63607e68eae90cee3a9dd42bd7f'}

MODELS_SOURCE = 'from datetime import UTC, datetime\nfrom enum import Enum\nfrom typing import Annotated, Literal\nfrom uuid import uuid4\n\nfrom pydantic import (\n    BaseModel,\n    ConfigDict,\n    Field,\n    StringConstraints,\n    model_validator,\n)\n\n\nShortText = Annotated[\n    str,\n    StringConstraints(\n        strip_whitespace=True,\n        min_length=1,\n        max_length=256,\n    ),\n]\n\nLongText = Annotated[\n    str,\n    StringConstraints(\n        strip_whitespace=True,\n        min_length=1,\n        max_length=2000,\n    ),\n]\n\nEvidenceScalar = bool | int | float | str | None\n\n\nclass InvestigationProbe(str, Enum):\n    """\n    Closed set of read-only probes selectable by a reasoner.\n\n    The reasoner selects only a symbolic probe. It never supplies a tool\n    name, Kubernetes verb, resource target, URL, credential or PromQL.\n    """\n\n    KUBERNETES_POD_STATE = "kubernetes_pod_state"\n    KUBERNETES_PREVIOUS_CONTAINER_LOGS = (\n        "kubernetes_previous_container_logs"\n    )\n    PROMETHEUS_MEMORY_WORKING_SET = (\n        "prometheus_memory_working_set"\n    )\n    PROMETHEUS_MEMORY_LIMIT = "prometheus_memory_limit"\n    PROMETHEUS_RESTART_COUNT = "prometheus_restart_count"\n\n\nclass InvestigationStatus(str, Enum):\n    PENDING = "pending"\n    RUNNING = "running"\n    CONCLUDED = "concluded"\n    EXHAUSTED = "exhausted"\n    FAILED = "failed"\n\n\nclass InvestigationStopReason(str, Enum):\n    SUFFICIENT_EVIDENCE = "sufficient_evidence"\n    INSUFFICIENT_EVIDENCE = "insufficient_evidence"\n    MAX_ITERATIONS = "max_iterations"\n    MAX_TOOL_CALLS = "max_tool_calls"\n    TIMEOUT = "timeout"\n    DUPLICATE_PROBE = "duplicate_probe"\n    NO_SAFE_PROBE = "no_safe_probe"\n    REASONER_ERROR = "reasoner_error"\n    INVALID_SCOPE = "invalid_scope"\n\n\nclass InvestigationLimits(BaseModel):\n    """\n    Hard execution limits for one read-only investigation.\n    """\n\n    model_config = ConfigDict(\n        frozen=True,\n        extra="forbid",\n    )\n\n    max_iterations: int = Field(\n        default=6,\n        ge=1,\n        le=10,\n    )\n    max_tool_calls: int = Field(\n        default=10,\n        ge=1,\n        le=20,\n    )\n    timeout_seconds: float = Field(\n        default=30.0,\n        ge=1.0,\n        le=60.0,\n    )\n\n\nclass InvestigationScope(BaseModel):\n    """\n    Trusted scope derived from StandardEvent, never from LLM output.\n    """\n\n    model_config = ConfigDict(\n        frozen=True,\n        extra="forbid",\n    )\n\n    alert_name: ShortText\n    alert_message: str = Field(\n        default="",\n        max_length=2000,\n    )\n    event_occurred_at: datetime | None = None\n    resource: ShortText\n    namespace: ShortText = "default"\n    cluster: ShortText | None = None\n\n\nclass EvidenceItem(BaseModel):\n    """\n    Bounded evidence retained by the Shadow loop.\n\n    Raw Kubernetes or Prometheus payloads are not stored here. facts accepts\n    scalar values only. Kubernetes log evidence is retained only as a bounded,\n    redacted excerpt, which prevents nested responses or raw log streams from\n    becoming an unbounded or sensitive reasoning transcript.\n    """\n\n    model_config = ConfigDict(\n        frozen=True,\n        extra="forbid",\n    )\n\n    evidence_id: str = Field(\n        default_factory=lambda: str(uuid4()),\n        min_length=1,\n        max_length=64,\n    )\n    probe: InvestigationProbe\n    source: ShortText\n    success: bool\n    trusted: bool\n    production_signal: bool\n    reliability: float = Field(\n        ge=0.0,\n        le=1.0,\n    )\n    observed_at: datetime\n    facts: dict[str, EvidenceScalar] = Field(\n        default_factory=dict,\n        max_length=32,\n    )\n    error_code: ShortText | None = None\n\n    @model_validator(mode="after")\n    def validate_trust_boundary(self):\n        if self.trusted and (\n            not self.success\n            or not self.production_signal\n        ):\n            raise ValueError(\n                "trusted evidence requires a successful production signal"\n            )\n\n        if not self.success and self.error_code is None:\n            raise ValueError(\n                "failed evidence requires an error code"\n            )\n\n        return self\n\n\nclass IncidentHypothesis(BaseModel):\n    """\n    One current incident explanation maintained by the reasoner.\n    """\n\n    model_config = ConfigDict(\n        extra="forbid",\n    )\n\n    hypothesis_id: ShortText\n    cause: LongText\n    confidence: float = Field(\n        ge=0.0,\n        le=1.0,\n    )\n    supporting_evidence_ids: list[ShortText] = Field(\n        default_factory=list,\n        max_length=32,\n    )\n    conflicting_evidence_ids: list[ShortText] = Field(\n        default_factory=list,\n        max_length=32,\n    )\n    missing_evidence: list[ShortText] = Field(\n        default_factory=list,\n        max_length=16,\n    )\n\n\nclass InvestigationConclusion(BaseModel):\n    """\n    Structured diagnosis output. It contains no remediation authorization.\n    """\n\n    model_config = ConfigDict(\n        extra="forbid",\n    )\n\n    root_cause: LongText\n    confidence: float = Field(\n        ge=0.0,\n        le=1.0,\n    )\n    evidence_ids: list[ShortText] = Field(\n        min_length=1,\n        max_length=32,\n    )\n    remaining_uncertainties: list[ShortText] = Field(\n        default_factory=list,\n        max_length=16,\n    )\n\n\nclass InvestigationDecision(BaseModel):\n    """\n    One bounded reasoner decision.\n\n    A non-terminal decision must select exactly one symbolic read-only probe.\n    A terminal decision cannot select a probe. Sufficient-evidence stops must\n    include a structured conclusion.\n    """\n\n    model_config = ConfigDict(\n        extra="forbid",\n    )\n\n    hypotheses: list[IncidentHypothesis] = Field(\n        min_length=1,\n        max_length=8,\n    )\n    rationale_summary: LongText\n    stop: bool = False\n    stop_reason: InvestigationStopReason | None = None\n    next_probe: InvestigationProbe | None = None\n    conclusion: InvestigationConclusion | None = None\n\n    @model_validator(mode="after")\n    def validate_decision_shape(self):\n        if self.stop:\n            if self.next_probe is not None:\n                raise ValueError(\n                    "terminal decision cannot select a probe"\n                )\n            if self.stop_reason is None:\n                raise ValueError(\n                    "terminal decision requires a stop reason"\n                )\n            if self.stop_reason not in {\n                InvestigationStopReason.SUFFICIENT_EVIDENCE,\n                InvestigationStopReason.INSUFFICIENT_EVIDENCE,\n                InvestigationStopReason.NO_SAFE_PROBE,\n            }:\n                raise ValueError(\n                    "reasoner cannot select an internal stop reason"\n                )\n            if (\n                self.stop_reason\n                == InvestigationStopReason.SUFFICIENT_EVIDENCE\n                and self.conclusion is None\n            ):\n                raise ValueError(\n                    "sufficient evidence requires a conclusion"\n                )\n            if (\n                self.stop_reason\n                != InvestigationStopReason.SUFFICIENT_EVIDENCE\n                and self.conclusion is not None\n            ):\n                raise ValueError(\n                    "insufficient evidence cannot include a conclusion"\n                )\n        else:\n            if self.next_probe is None:\n                raise ValueError(\n                    "continuing decision requires a probe"\n                )\n            if self.stop_reason is not None:\n                raise ValueError(\n                    "continuing decision cannot have a stop reason"\n                )\n            if self.conclusion is not None:\n                raise ValueError(\n                    "continuing decision cannot have a conclusion"\n                )\n\n        return self\n\n\nclass InvestigationState(BaseModel):\n    """\n    Complete bounded state of one Shadow investigation.\n    """\n\n    model_config = ConfigDict(\n        extra="forbid",\n    )\n\n    investigation_id: str = Field(\n        default_factory=lambda: str(uuid4()),\n        min_length=1,\n        max_length=64,\n    )\n    shadow_mode: Literal[True] = True\n    read_only: Literal[True] = True\n    status: InvestigationStatus = InvestigationStatus.PENDING\n    scope: InvestigationScope\n    limits: InvestigationLimits = Field(\n        default_factory=InvestigationLimits\n    )\n    started_at: datetime = Field(\n        default_factory=lambda: datetime.now(UTC)\n    )\n    updated_at: datetime = Field(\n        default_factory=lambda: datetime.now(UTC)\n    )\n    iteration_count: int = Field(\n        default=0,\n        ge=0,\n        le=10,\n    )\n    tool_call_count: int = Field(\n        default=0,\n        ge=0,\n        le=20,\n    )\n    hypotheses: list[IncidentHypothesis] = Field(\n        default_factory=list,\n        max_length=8,\n    )\n    evidence: list[EvidenceItem] = Field(\n        default_factory=list,\n        max_length=20,\n    )\n    attempted_probes: list[InvestigationProbe] = Field(\n        default_factory=list,\n        max_length=20,\n    )\n    decision_summaries: list[LongText] = Field(\n        default_factory=list,\n        max_length=10,\n    )\n    stop_reason: InvestigationStopReason | None = None\n    failure_code: ShortText | None = None\n    epistemic_guard_code: ShortText | None = None\n    conclusion: InvestigationConclusion | None = None\n'
PROBES_SOURCE = 'import re\nfrom collections.abc import Mapping\nfrom datetime import UTC, datetime\nfrom math import isfinite\nfrom typing import Any\n\nfrom services.agent_runtime.app.investigation.evidence_time import (\n    InvestigationEvidenceTimeError,\n    InvestigationEvidenceTimePolicy,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    EvidenceItem,\n    InvestigationProbe,\n    InvestigationScope,\n)\n\n\nclass InvestigationProbeError(RuntimeError):\n    """\n    Base error for the bounded read-only probe adapter.\n    """\n\n\nclass InvestigationToolUnavailableError(\n    InvestigationProbeError\n):\n    """\n    Runtime ToolManager is unavailable.\n    """\n\n\nclass InvestigationProbeResponseError(\n    InvestigationProbeError\n):\n    """\n    A read-only tool returned evidence that cannot cross the\n    Investigation trust boundary.\n    """\n\n\nclass ReadOnlyInvestigationProbeExecutor:\n    """\n    Translate symbolic Investigation probes into exact read-only tool calls.\n\n    The reasoner selects only an InvestigationProbe enum value.\n\n    This adapter owns:\n\n    - fixed Kubernetes read-only actions;\n    - fixed bounded previous-container log collection;\n    - fixed Prometheus query templates;\n    - provider/source validation;\n    - read-only mode validation;\n    - production-signal validation;\n    - observed-at validation;\n    - bounded evidence normalization.\n\n    The reasoner cannot provide Kubernetes verbs, resource kinds, PromQL,\n    URLs, credentials or raw tool arguments.\n    """\n\n    _TRUSTED_MODE = "read_only"\n    _MAX_LOG_TOOL_CHARS = 4000\n    _MAX_LOG_EVIDENCE_CHARS = 1800\n    _MAX_LOG_LINES = 80\n\n    def __init__(\n        self,\n        time_policy: (\n            InvestigationEvidenceTimePolicy\n            | None\n        ) = None,\n    ) -> None:\n        self.time_policy = (\n            time_policy\n            if time_policy is not None\n            else InvestigationEvidenceTimePolicy()\n        )\n\n    async def collect(\n        self,\n        context,\n        scope: InvestigationScope,\n        probe: InvestigationProbe,\n    ) -> EvidenceItem:\n        tools = getattr(\n            context,\n            "tools",\n            None,\n        )\n\n        if tools is None:\n            raise InvestigationToolUnavailableError(\n                "Runtime tools are unavailable"\n            )\n\n        if (\n            probe\n            == InvestigationProbe.KUBERNETES_POD_STATE\n        ):\n            result = await tools.call(\n                "kubernetes",\n                context=context,\n                action="describe",\n                resource="pod",\n                target=scope.resource,\n                namespace=scope.namespace,\n            )\n\n            return self._normalize_kubernetes(\n                scope=scope,\n                probe=probe,\n                result=result,\n            )\n\n        if (\n            probe\n            == InvestigationProbe.KUBERNETES_PREVIOUS_CONTAINER_LOGS\n        ):\n            result = await tools.call(\n                "kubernetes",\n                context=context,\n                action="previous_logs",\n                resource="pod",\n                target=scope.resource,\n                namespace=scope.namespace,\n            )\n\n            return self._normalize_kubernetes_logs(\n                scope=scope,\n                probe=probe,\n                result=result,\n            )\n\n        query = self._prometheus_query(\n            scope=scope,\n            probe=probe,\n        )\n\n        query_time = self.time_policy.query_time(\n            scope=scope,\n            probe=probe,\n        )\n\n        call_arguments = {\n            "query": query,\n        }\n\n        if query_time is not None:\n            call_arguments["time"] = (\n                query_time\n            )\n\n        result = await tools.call(\n            "prometheus",\n            context=context,\n            **call_arguments,\n        )\n\n        return self._normalize_prometheus(\n            scope=scope,\n            probe=probe,\n            result=result,\n        )\n\n    @classmethod\n    def _prometheus_query(\n        cls,\n        scope: InvestigationScope,\n        probe: InvestigationProbe,\n    ) -> str:\n        labels = [\n            (\n                \'pod="\'\n                f\'{cls._escape_label(scope.resource)}\'\n                \'"\'\n            ),\n            (\n                \'namespace="\'\n                f\'{cls._escape_label(scope.namespace)}\'\n                \'"\'\n            ),\n        ]\n\n        if scope.cluster:\n            labels.append(\n                \'cluster="\'\n                f\'{cls._escape_label(scope.cluster)}\'\n                \'"\'\n            )\n\n        selector = ",".join(\n            labels\n        )\n\n        if (\n            probe\n            == InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET\n        ):\n            return (\n                "sum(container_memory_working_set_bytes{"\n                f\'{selector},container!="POD",container!="",image!=""\'\n                "})"\n            )\n\n        if (\n            probe\n            == InvestigationProbe.PROMETHEUS_MEMORY_LIMIT\n        ):\n            return (\n                "sum(kube_pod_container_resource_limits{"\n                f\'{selector},resource="memory",unit="byte"\'\n                "})"\n            )\n\n        if (\n            probe\n            == InvestigationProbe.PROMETHEUS_RESTART_COUNT\n        ):\n            return (\n                "sum(kube_pod_container_status_restarts_total{"\n                f"{selector}"\n                "})"\n            )\n\n        raise InvestigationProbeError(\n            "Unsupported investigation probe"\n        )\n\n    def _normalize_kubernetes(\n        self,\n        scope: InvestigationScope,\n        probe: InvestigationProbe,\n        result: Any,\n    ) -> EvidenceItem:\n        data, observed_at = (\n            self._validate_tool_evidence(\n                result=result,\n                expected_source="kubernetes",\n            )\n        )\n\n        if "phase" not in data:\n            raise InvestigationProbeResponseError(\n                "Kubernetes evidence phase is missing"\n            )\n\n        containers = data.get(\n            "containers"\n        )\n\n        if not isinstance(\n            containers,\n            list,\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes evidence containers are invalid"\n            )\n\n        restart_counts: list[int] = []\n        state_reasons: set[str] = set()\n        termination_reasons: set[str] = set()\n\n        for container in containers[:32]:\n            if not isinstance(\n                container,\n                Mapping,\n            ):\n                continue\n\n            restart_count = container.get(\n                "restart_count"\n            )\n\n            if isinstance(\n                restart_count,\n                int,\n            ):\n                restart_counts.append(\n                    restart_count\n                )\n\n            state_reason = container.get(\n                "state_reason"\n            )\n\n            if (\n                isinstance(\n                    state_reason,\n                    str,\n                )\n                and state_reason\n            ):\n                state_reasons.add(\n                    state_reason[:128]\n                )\n\n            termination_reason = container.get(\n                "last_termination_reason"\n            )\n\n            if (\n                isinstance(\n                    termination_reason,\n                    str,\n                )\n                and termination_reason\n            ):\n                termination_reasons.add(\n                    termination_reason[:128]\n                )\n\n        facts = {\n            "temporal_basis": (\n                self.time_policy.temporal_basis(\n                    scope=scope,\n                    probe=probe,\n                )\n            ),\n            "phase": cls_scalar(\n                data.get("phase")\n            ),\n            "ready": cls_scalar(\n                data.get("ready")\n            ),\n            "scheduled": cls_scalar(\n                data.get("scheduled")\n            ),\n            "oom_killed": cls_scalar(\n                data.get("oom_killed")\n            ),\n            "max_restart_count": (\n                max(restart_counts)\n                if restart_counts\n                else None\n            ),\n            "state_reasons": (\n                ",".join(\n                    sorted(\n                        state_reasons\n                    )\n                )\n                if state_reasons\n                else None\n            ),\n            "last_termination_reasons": (\n                ",".join(\n                    sorted(\n                        termination_reasons\n                    )\n                )\n                if termination_reasons\n                else None\n            ),\n        }\n\n        return EvidenceItem(\n            probe=probe,\n            source="kubernetes",\n            success=True,\n            trusted=True,\n            production_signal=True,\n            reliability=1.0,\n            observed_at=observed_at,\n            facts=facts,\n        )\n\n    def _normalize_kubernetes_logs(\n        self,\n        scope: InvestigationScope,\n        probe: InvestigationProbe,\n        result: Any,\n    ) -> EvidenceItem:\n        data, observed_at = (\n            self._validate_tool_evidence(\n                result=result,\n                expected_source="kubernetes",\n            )\n        )\n\n        if (\n            data.get(\n                "previous"\n            )\n            is not True\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes log evidence is not previous-container output"\n            )\n\n        container_value = data.get(\n            "container_name"\n        )\n\n        if not isinstance(\n            container_value,\n            str,\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes log evidence container is invalid"\n            )\n\n        container_name = (\n            container_value\n            .strip()\n        )\n\n        if (\n            not container_name\n            or len(\n                container_name\n            )\n            > 128\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes log evidence container is invalid"\n            )\n\n        line_count = data.get(\n            "line_count"\n        )\n\n        if (\n            not isinstance(\n                line_count,\n                int,\n            )\n            or isinstance(\n                line_count,\n                bool,\n            )\n            or line_count < 0\n            or line_count > self._MAX_LOG_LINES\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes log evidence line count is invalid"\n            )\n\n        truncated = data.get(\n            "truncated"\n        )\n\n        if not isinstance(\n            truncated,\n            bool,\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes log evidence truncation flag is invalid"\n            )\n\n        redaction_count = data.get(\n            "redaction_count"\n        )\n\n        if (\n            not isinstance(\n                redaction_count,\n                int,\n            )\n            or isinstance(\n                redaction_count,\n                bool,\n            )\n            or redaction_count < 0\n            or redaction_count > 10000\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes log evidence redaction count is invalid"\n            )\n\n        excerpt_value = data.get(\n            "excerpt"\n        )\n\n        if not isinstance(\n            excerpt_value,\n            str,\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes log evidence excerpt is invalid"\n            )\n\n        if len(\n            excerpt_value\n        ) > self._MAX_LOG_TOOL_CHARS:\n            raise InvestigationProbeResponseError(\n                "Kubernetes log evidence excerpt is too large"\n            )\n\n        excerpt, local_redactions = (\n            redact_log_excerpt(\n                excerpt_value\n            )\n        )\n\n        redaction_count = (\n            redaction_count\n            + local_redactions\n        )\n\n        evidence_truncated = (\n            len(\n                excerpt\n            )\n            > self._MAX_LOG_EVIDENCE_CHARS\n        )\n\n        if evidence_truncated:\n            excerpt = excerpt[\n                -self._MAX_LOG_EVIDENCE_CHARS:\n            ]\n\n        facts = {\n            "temporal_basis": (\n                self.time_policy.temporal_basis(\n                    scope=scope,\n                    probe=probe,\n                )\n            ),\n            "container_name": container_name,\n            "previous": True,\n            "log_line_count": line_count,\n            "tool_truncated": truncated,\n            "evidence_truncated": (\n                evidence_truncated\n            ),\n            "redaction_count": (\n                redaction_count\n            ),\n            "log_excerpt": (\n                excerpt\n                if excerpt\n                else None\n            ),\n        }\n\n        return EvidenceItem(\n            probe=probe,\n            source="kubernetes",\n            success=True,\n            trusted=True,\n            production_signal=True,\n            reliability=1.0,\n            observed_at=observed_at,\n            facts=facts,\n        )\n\n    def _normalize_prometheus(\n        self,\n        scope: InvestigationScope,\n        probe: InvestigationProbe,\n        result: Any,\n    ) -> EvidenceItem:\n        data, observed_at = (\n            self._validate_tool_evidence(\n                result=result,\n                expected_source="prometheus",\n            )\n        )\n\n        result_type_value = data.get(\n            "resultType"\n        )\n\n        if (\n            not isinstance(\n                result_type_value,\n                str,\n            )\n            or result_type_value\n            not in {\n                "vector",\n                "matrix",\n                "scalar",\n                "string",\n            }\n        ):\n            raise InvestigationProbeResponseError(\n                "Prometheus evidence result type is invalid"\n            )\n\n        result_type = (\n            result_type_value[:64]\n        )\n\n        samples = extract_numeric_samples(\n            result_type=result_type,\n            value=data.get(\n                "result"\n            ),\n        )\n\n        if not samples:\n            raise InvestigationProbeResponseError(\n                "Prometheus evidence contains no numeric samples"\n            )\n\n        try:\n            event_offset_seconds = (\n                self.time_policy.validate_observed_at(\n                    scope=scope,\n                    probe=probe,\n                    observed_at=observed_at,\n                )\n            )\n        except InvestigationEvidenceTimeError as exc:\n            raise InvestigationProbeResponseError(\n                "Prometheus evidence is not "\n                "temporally relevant"\n            ) from exc\n\n        facts = {\n            "temporal_basis": (\n                self.time_policy.temporal_basis(\n                    scope=scope,\n                    probe=probe,\n                )\n            ),\n            "event_offset_seconds": (\n                event_offset_seconds\n            ),\n            "result_type": result_type,\n            "sample_count": len(\n                samples\n            ),\n            "value_sum": sum(\n                samples\n            ),\n            "value_min": min(\n                samples\n            ),\n            "value_max": max(\n                samples\n            ),\n        }\n\n        return EvidenceItem(\n            probe=probe,\n            source="prometheus",\n            success=True,\n            trusted=True,\n            production_signal=True,\n            reliability=1.0,\n            observed_at=observed_at,\n            facts=facts,\n        )\n\n    @classmethod\n    def _validate_tool_evidence(\n        cls,\n        *,\n        result: Any,\n        expected_source: str,\n    ) -> tuple[\n        Mapping[str, Any],\n        datetime,\n    ]:\n        if not isinstance(\n            result,\n            Mapping,\n        ):\n            raise InvestigationProbeResponseError(\n                "Investigation tool result is invalid"\n            )\n\n        if (\n            result.get(\n                "success"\n            )\n            is not True\n        ):\n            raise InvestigationProbeResponseError(\n                "Investigation tool result was unsuccessful"\n            )\n\n        source_value = result.get(\n            "source"\n        )\n\n        if not isinstance(\n            source_value,\n            str,\n        ):\n            raise InvestigationProbeResponseError(\n                "Investigation evidence source is invalid"\n            )\n\n        source = (\n            source_value\n            .strip()\n            .lower()\n        )\n\n        if source != expected_source:\n            raise InvestigationProbeResponseError(\n                "Investigation evidence source is untrusted"\n            )\n\n        mode_value = result.get(\n            "mode"\n        )\n\n        if not isinstance(\n            mode_value,\n            str,\n        ):\n            raise InvestigationProbeResponseError(\n                "Investigation evidence mode is invalid"\n            )\n\n        mode = (\n            mode_value\n            .strip()\n            .lower()\n        )\n\n        if mode != cls._TRUSTED_MODE:\n            raise InvestigationProbeResponseError(\n                "Investigation evidence mode is not read-only"\n            )\n\n        if (\n            result.get(\n                "production_signal"\n            )\n            is not True\n        ):\n            raise InvestigationProbeResponseError(\n                "Investigation evidence is not a production signal"\n            )\n\n        observed_at = parse_observed_at(\n            result.get(\n                "observed_at"\n            )\n        )\n\n        data = result.get(\n            "data"\n        )\n\n        if not isinstance(\n            data,\n            Mapping,\n        ):\n            raise InvestigationProbeResponseError(\n                "Investigation evidence data is invalid"\n            )\n\n        return (\n            data,\n            observed_at,\n        )\n\n    @staticmethod\n    def _escape_label(\n        value: str,\n    ) -> str:\n        return (\n            value\n            .replace(\n                "\\\\",\n                "\\\\\\\\",\n            )\n            .replace(\n                "\\n",\n                "\\\\n",\n            )\n            .replace(\n                "\\r",\n                "\\\\r",\n            )\n            .replace(\n                \'"\',\n                \'\\\\"\',\n            )\n        )\n\n\ndef redact_log_excerpt(\n    value: str,\n) -> tuple[str, int]:\n    """\n    Defense-in-depth redaction at the Investigation trust boundary.\n\n    KubernetesTool redacts before ToolManager tracing. This second pass keeps\n    injected or forged ToolManager responses from placing obvious credentials\n    into bounded InvestigationState.\n    """\n\n    text = value\n    total = 0\n\n    patterns = [\n        (\n            re.compile(\n                (\n                    r"\\beyJ[A-Za-z0-9_-]{10,}"\n                    r"\\.[A-Za-z0-9_-]{10,}"\n                    r"\\.[A-Za-z0-9_-]{10,}\\b"\n                )\n            ),\n            "[REDACTED_JWT]",\n        ),\n        (\n            re.compile(\n                (\n                    r"(?i)\\b("\n                    r"bearer|basic"\n                    r")\\s+"\n                    r"[A-Za-z0-9._~+/=-]{8,}"\n                )\n            ),\n            None,\n        ),\n        (\n            re.compile(\n                (\n                    r"(?i)\\b("\n                    r"password|passwd|pwd|secret|token|"\n                    r"api[_-]?key|access[_-]?key|"\n                    r"client[_-]?secret"\n                    r")\\b"\n                    r"(\\s*[:=]\\s*)"\n                    r"([\\"\']?)"\n                    r"([^\\s,;\\"\']{4,})"\n                    r"([\\"\']?)"\n                )\n            ),\n            None,\n        ),\n    ]\n\n    text, count = patterns[0][0].subn(\n        patterns[0][1],\n        text,\n    )\n\n    total += count\n\n    text, count = patterns[1][0].subn(\n        lambda match: (\n            match.group(1)\n            + " [REDACTED]"\n        ),\n        text,\n    )\n\n    total += count\n\n    text, count = patterns[2][0].subn(\n        lambda match: (\n            match.group(1)\n            + match.group(2)\n            + "[REDACTED]"\n        ),\n        text,\n    )\n\n    total += count\n\n    return (\n        text,\n        total,\n    )\n\n\ndef cls_scalar(\n    value: Any,\n):\n    if (\n        value is None\n        or isinstance(\n            value,\n            (\n                bool,\n                int,\n                float,\n                str,\n            ),\n        )\n    ):\n        return value\n\n    return str(\n        value\n    )[:256]\n\n\ndef parse_observed_at(\n    value: Any,\n) -> datetime:\n    if isinstance(\n        value,\n        datetime,\n    ):\n        parsed = value\n\n    elif isinstance(\n        value,\n        str,\n    ):\n        text = value.strip()\n\n        if not text:\n            raise InvestigationProbeResponseError(\n                "Investigation evidence observed_at is invalid"\n            )\n\n        if text.endswith(\n            "Z"\n        ):\n            text = (\n                f"{text[:-1]}+00:00"\n            )\n\n        try:\n            parsed = datetime.fromisoformat(\n                text\n            )\n        except ValueError as exc:\n            raise InvestigationProbeResponseError(\n                "Investigation evidence observed_at is invalid"\n            ) from exc\n\n    else:\n        raise InvestigationProbeResponseError(\n            "Investigation evidence observed_at is invalid"\n        )\n\n    if parsed.tzinfo is None:\n        raise InvestigationProbeResponseError(\n            "Investigation evidence observed_at must be timezone-aware"\n        )\n\n    return parsed.astimezone(\n        UTC\n    )\n\n\ndef extract_numeric_samples(\n    result_type: str | None,\n    value: Any,\n) -> list[float]:\n    samples: list[float] = []\n\n    def add_sample(\n        sample: Any,\n    ) -> None:\n        if (\n            not isinstance(\n                sample,\n                list,\n            )\n            or len(sample) < 2\n            or len(samples) >= 32\n        ):\n            return\n\n        try:\n            numeric_value = float(\n                sample[1]\n            )\n        except (\n            TypeError,\n            ValueError,\n        ):\n            return\n\n        if not isfinite(\n            numeric_value\n        ):\n            return\n\n        samples.append(\n            numeric_value\n        )\n\n    if result_type in {\n        "scalar",\n        "string",\n    }:\n        add_sample(\n            value\n        )\n\n    elif (\n        result_type == "vector"\n        and isinstance(\n            value,\n            list,\n        )\n    ):\n        for item in value[:32]:\n            if isinstance(\n                item,\n                Mapping,\n            ):\n                add_sample(\n                    item.get(\n                        "value"\n                    )\n                )\n\n    elif (\n        result_type == "matrix"\n        and isinstance(\n            value,\n            list,\n        )\n    ):\n        for item in value[:32]:\n            if not isinstance(\n                item,\n                Mapping,\n            ):\n                continue\n\n            values = item.get(\n                "values"\n            )\n\n            if (\n                isinstance(\n                    values,\n                    list,\n                )\n                and values\n            ):\n                add_sample(\n                    values[-1]\n                )\n\n    return samples\n\n\n__all__ = [\n    "InvestigationProbeError",\n    "InvestigationProbeResponseError",\n    "InvestigationToolUnavailableError",\n    "ReadOnlyInvestigationProbeExecutor",\n    "extract_numeric_samples",\n    "parse_observed_at",\n]\n'
EVIDENCE_TIME_SOURCE = 'from dataclasses import dataclass\nfrom datetime import UTC, datetime, timedelta\n\nfrom services.agent_runtime.app.investigation.models import (\n    InvestigationProbe,\n    InvestigationScope,\n)\n\n\nclass InvestigationEvidenceTimeError(\n    RuntimeError\n):\n    """\n    Evidence timestamp is not temporally relevant to the incident.\n    """\n\n\n@dataclass(\n    frozen=True,\n    slots=True,\n)\nclass InvestigationEvidenceTimePolicy:\n    """\n    Temporal relevance policy for Investigation evidence.\n\n    Prometheus probes are incident-time probes when the trusted event\n    occurred_at timestamp is available.\n\n    Kubernetes Pod state remains current-state evidence because the Core API\n    does not provide historical Pod snapshots.\n\n    Kubernetes previous-container logs are a bounded historical artifact from\n    the container instance immediately preceding the current one. observed_at\n    remains the trusted collection time, while temporal_basis records this\n    distinct previous-container semantics.\n\n    The policy deliberately does not impose one global evidence TTL.\n    Different evidence sources have different temporal semantics.\n    """\n\n    prometheus_max_event_skew: timedelta = timedelta(\n        minutes=2\n    )\n\n    def __post_init__(\n        self,\n    ) -> None:\n        if (\n            self.prometheus_max_event_skew\n            <= timedelta(0)\n        ):\n            raise ValueError(\n                "prometheus_max_event_skew must be positive"\n            )\n\n    def query_time(\n        self,\n        *,\n        scope: InvestigationScope,\n        probe: InvestigationProbe,\n    ) -> datetime | None:\n        if not self._is_prometheus_probe(\n            probe\n        ):\n            return None\n\n        return self._event_time(\n            scope\n        )\n\n    def temporal_basis(\n        self,\n        *,\n        scope: InvestigationScope,\n        probe: InvestigationProbe,\n    ) -> str:\n        if (\n            probe\n            == InvestigationProbe.KUBERNETES_PREVIOUS_CONTAINER_LOGS\n        ):\n            return "previous_container"\n\n        if (\n            self._is_prometheus_probe(\n                probe\n            )\n            and scope.event_occurred_at\n            is not None\n        ):\n            return "incident_time"\n\n        return "current_state"\n\n    def validate_observed_at(\n        self,\n        *,\n        scope: InvestigationScope,\n        probe: InvestigationProbe,\n        observed_at: datetime,\n    ) -> float | None:\n        """\n        Return signed seconds between the evidence sample and incident time.\n\n        Positive means the sample is after the incident event.\n        Negative means it is before the incident event.\n        """\n\n        if not self._is_prometheus_probe(\n            probe\n        ):\n            return None\n\n        event_time = self._event_time(\n            scope\n        )\n\n        if event_time is None:\n            return None\n\n        normalized_observed = (\n            self._timezone_aware_utc(\n                observed_at,\n                name="observed_at",\n            )\n        )\n\n        offset = (\n            normalized_observed\n            - event_time\n        )\n\n        if abs(\n            offset\n        ) > self.prometheus_max_event_skew:\n            raise InvestigationEvidenceTimeError(\n                "Prometheus evidence is outside "\n                "the incident-time window"\n            )\n\n        return offset.total_seconds()\n\n    @staticmethod\n    def _is_prometheus_probe(\n        probe: InvestigationProbe,\n    ) -> bool:\n        return probe in {\n            InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,\n            InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,\n            InvestigationProbe.PROMETHEUS_RESTART_COUNT,\n        }\n\n    @classmethod\n    def _event_time(\n        cls,\n        scope: InvestigationScope,\n    ) -> datetime | None:\n        value = scope.event_occurred_at\n\n        if value is None:\n            return None\n\n        return cls._timezone_aware_utc(\n            value,\n            name="event_occurred_at",\n        )\n\n    @staticmethod\n    def _timezone_aware_utc(\n        value: datetime,\n        *,\n        name: str,\n    ) -> datetime:\n        if (\n            not isinstance(\n                value,\n                datetime,\n            )\n            or value.tzinfo is None\n        ):\n            raise InvestigationEvidenceTimeError(\n                f"{name} must be timezone-aware"\n            )\n\n        return value.astimezone(\n            UTC\n        )\n\n\n__all__ = [\n    "InvestigationEvidenceTimeError",\n    "InvestigationEvidenceTimePolicy",\n]\n'
KUBERNETES_TOOL_SOURCE = 'import os\nimport re\nimport ssl\nfrom collections.abc import Callable, Mapping\nfrom datetime import UTC, datetime\nfrom pathlib import Path\nfrom typing import Any\nfrom urllib.parse import quote, urlencode, urlparse\n\nimport httpx\n\nfrom services.agent_runtime.app.tools.base import (\n    BaseTool,\n)\n\n\nclass KubernetesToolError(RuntimeError):\n    """\n    Base error raised by KubernetesTool.\n    """\n\n\nclass KubernetesConfigurationError(\n    KubernetesToolError\n):\n    """\n    Kubernetes API configuration is invalid or unavailable.\n    """\n\n\nclass KubernetesQueryError(\n    KubernetesToolError\n):\n    """\n    Kubernetes API query failed.\n    """\n\n\nclass KubernetesAuthorizationError(\n    KubernetesQueryError\n):\n    """\n    Kubernetes rejected the configured identity.\n    """\n\n\nclass KubernetesResourceNotFoundError(\n    KubernetesQueryError\n):\n    """\n    Requested Kubernetes resource does not exist.\n    """\n\n\nclass KubernetesOperationNotAllowedError(\n    KubernetesToolError\n):\n    """\n    Operation is outside the read-only verification boundary.\n    """\n\n\nclass KubernetesTool(BaseTool):\n    """\n    Read-only Kubernetes Pod evidence tool.\n\n    Live mode uses the Kubernetes Core API directly through httpx.\n    Previous-container logs are collected through the Pod log subresource\n    using fixed platform-owned bounds and secret redaction before results\n    can enter ToolManager traces or Investigation evidence.\n    It supports an explicit API URL and in-cluster discovery.\n\n    A temporary dry-run fallback is retained for compatibility.\n    It is marked production_signal=False and is rejected by the\n    VerificationEvidenceCollector.\n    """\n\n    _READ_ONLY_ACTIONS = {\n        "describe",\n        "get",\n        "previous_logs",\n    }\n\n    _LOG_TAIL_LINES = 80\n    _LOG_LIMIT_BYTES = 16384\n    _LOG_RETURN_MAX_CHARS = 4000\n\n    _POD_RESOURCES = {\n        "pod",\n        "pods",\n    }\n\n    _DEFAULT_TOKEN_FILE = Path(\n        "/var/run/secrets/kubernetes.io/"\n        "serviceaccount/token"\n    )\n\n    _DEFAULT_CA_FILE = Path(\n        "/var/run/secrets/kubernetes.io/"\n        "serviceaccount/ca.crt"\n    )\n\n    def __init__(\n        self,\n        api_url: str | None = None,\n        timeout_seconds: float | None = None,\n        verify_tls: bool | None = None,\n        bearer_token: str | None = None,\n        token_file: str | Path | None = None,\n        ca_file: str | Path | None = None,\n        cluster_name: str | None = None,\n        allow_dry_run_fallback: bool | None = None,\n        client: httpx.AsyncClient | None = None,\n        clock: Callable[[], datetime] | None = None,\n    ) -> None:\n        configured_url = (\n            api_url\n            if api_url is not None\n            else os.getenv("KUBERNETES_API_URL")\n        )\n\n        self.in_cluster = False\n\n        if not configured_url:\n            configured_url = (\n                self._discover_in_cluster_url()\n            )\n            self.in_cluster = bool(\n                configured_url\n            )\n\n        self.api_url = (\n            configured_url.rstrip("/")\n            if configured_url\n            else None\n        )\n\n        if self.api_url:\n            parsed_url = urlparse(\n                self.api_url\n            )\n            if parsed_url.scheme not in {\n                "http",\n                "https",\n            } or not parsed_url.netloc:\n                raise KubernetesConfigurationError(\n                    "Kubernetes API URL is invalid"\n                )\n\n        self.timeout_seconds = (\n            timeout_seconds\n            if timeout_seconds is not None\n            else self._read_positive_float(\n                "KUBERNETES_TIMEOUT_SECONDS",\n                default=5.0,\n            )\n        )\n\n        self.verify_tls = (\n            verify_tls\n            if verify_tls is not None\n            else self._read_bool(\n                "KUBERNETES_VERIFY_TLS",\n                default=True,\n            )\n        )\n\n        configured_token_file = (\n            token_file\n            if token_file is not None\n            else os.getenv("KUBERNETES_TOKEN_FILE")\n        )\n\n        if (\n            configured_token_file is None\n            and self.in_cluster\n            and self._DEFAULT_TOKEN_FILE.exists()\n        ):\n            configured_token_file = (\n                self._DEFAULT_TOKEN_FILE\n            )\n\n        self.token_file = (\n            Path(configured_token_file)\n            if configured_token_file\n            else None\n        )\n\n        self.bearer_token = (\n            bearer_token\n            if bearer_token is not None\n            else os.getenv(\n                "KUBERNETES_BEARER_TOKEN"\n            )\n        )\n\n        if (\n            not self.bearer_token\n            and self.token_file is not None\n        ):\n            self.bearer_token = self._read_token(\n                self.token_file\n            )\n\n        configured_ca_file = (\n            ca_file\n            if ca_file is not None\n            else os.getenv("KUBERNETES_CA_FILE")\n        )\n\n        if (\n            configured_ca_file is None\n            and self.in_cluster\n            and self._DEFAULT_CA_FILE.exists()\n        ):\n            configured_ca_file = (\n                self._DEFAULT_CA_FILE\n            )\n\n        self.ca_file = (\n            Path(configured_ca_file)\n            if configured_ca_file\n            else None\n        )\n\n        self.cluster_name = (\n            cluster_name\n            if cluster_name is not None\n            else os.getenv(\n                "KUBERNETES_CLUSTER_NAME"\n            )\n        )\n\n        self.allow_dry_run_fallback = (\n            allow_dry_run_fallback\n            if allow_dry_run_fallback is not None\n            else self._read_bool(\n                "KUBERNETES_ALLOW_DRY_RUN_FALLBACK",\n                default=True,\n            )\n        )\n\n        self.client = client\n        self._clock = clock or (\n            lambda: datetime.now(UTC)\n        )\n\n        if self.timeout_seconds <= 0:\n            raise KubernetesConfigurationError(\n                "Kubernetes timeout must be positive"\n            )\n\n    @property\n    def name(self) -> str:\n        return "kubernetes"\n\n    async def execute(\n        self,\n        action: str,\n        resource: str,\n        target: str,\n        namespace: str = "default",\n        **kwargs: Any,\n    ) -> dict[str, Any]:\n        normalized_action = self._required_text(\n            action,\n            "action",\n        ).lower()\n        normalized_resource = self._required_text(\n            resource,\n            "resource",\n        ).lower()\n        normalized_target = self._required_text(\n            target,\n            "target",\n        )\n        normalized_namespace = self._required_text(\n            namespace,\n            "namespace",\n        )\n\n        if normalized_action not in (\n            self._READ_ONLY_ACTIONS\n        ):\n            if self.allow_dry_run_fallback:\n                return self._dry_run_response(\n                    action=normalized_action,\n                    resource=normalized_resource,\n                    target=normalized_target,\n                    namespace=normalized_namespace,\n                )\n\n            raise KubernetesOperationNotAllowedError(\n                "KubernetesTool only allows bounded read-only "\n                "get, describe, and previous_logs actions"\n            )\n\n        if normalized_resource not in (\n            self._POD_RESOURCES\n        ):\n            raise KubernetesOperationNotAllowedError(\n                "KubernetesTool currently supports Pod "\n                "evidence only"\n            )\n\n        if self.api_url is None:\n            if not self.allow_dry_run_fallback:\n                raise KubernetesConfigurationError(\n                    "KUBERNETES_API_URL is not configured"\n                )\n\n            return self._dry_run_response(\n                action=normalized_action,\n                resource="pod",\n                target=normalized_target,\n                namespace=normalized_namespace,\n            )\n\n        payload = await self._get_pod(\n            namespace=normalized_namespace,\n            target=normalized_target,\n        )\n\n        if normalized_action == "previous_logs":\n            container_name = (\n                self._select_previous_log_container(\n                    payload\n                )\n            )\n\n            data = await (\n                self._get_previous_container_logs(\n                    namespace=normalized_namespace,\n                    target=normalized_target,\n                    container=container_name,\n                )\n            )\n        else:\n            data = self._normalize_pod(\n                payload\n            )\n\n        observed_at = self._now()\n\n        return {\n            "success": True,\n            "source": "kubernetes",\n            "mode": "read_only",\n            "production_signal": True,\n            "observed_at": observed_at.isoformat(),\n            "action": normalized_action,\n            "resource": "pod",\n            "target": normalized_target,\n            "namespace": normalized_namespace,\n            "cluster": self.cluster_name,\n            "data": data,\n        }\n\n    async def _get_pod(\n        self,\n        namespace: str,\n        target: str,\n    ) -> dict[str, Any]:\n        url = self._pod_url(\n            namespace=namespace,\n            target=target,\n        )\n\n        try:\n            if self.client is not None:\n                response = await self.client.get(\n                    url,\n                    headers=self._headers,\n                )\n            else:\n                async with httpx.AsyncClient(\n                    timeout=self.timeout_seconds,\n                    verify=self._httpx_verify,\n                    headers=self._headers,\n                ) as client:\n                    response = await client.get(\n                        url\n                    )\n\n            response.raise_for_status()\n        except httpx.TimeoutException as exc:\n            raise KubernetesQueryError(\n                "Kubernetes API query timed out"\n            ) from exc\n        except httpx.HTTPStatusError as exc:\n            status_code = exc.response.status_code\n\n            if status_code in {\n                401,\n                403,\n            }:\n                raise KubernetesAuthorizationError(\n                    "Kubernetes API authorization failed"\n                ) from exc\n\n            if status_code == 404:\n                raise KubernetesResourceNotFoundError(\n                    "Kubernetes Pod was not found"\n                ) from exc\n\n            raise KubernetesQueryError(\n                "Kubernetes API returned HTTP "\n                f"{status_code}"\n            ) from exc\n        except httpx.RequestError as exc:\n            raise KubernetesQueryError(\n                "Kubernetes API request failed"\n            ) from exc\n\n        try:\n            payload = response.json()\n        except ValueError as exc:\n            raise KubernetesQueryError(\n                "Kubernetes API returned invalid JSON"\n            ) from exc\n\n        if not isinstance(payload, dict):\n            raise KubernetesQueryError(\n                "Kubernetes API response is not an object"\n            )\n\n        if (\n            payload.get("kind") == "Status"\n            and payload.get("status") == "Failure"\n        ):\n            reason = payload.get(\n                "reason",\n                "Unknown",\n            )\n            raise KubernetesQueryError(\n                "Kubernetes API returned failure "\n                f"[{reason}]"\n            )\n\n        return payload\n\n    @classmethod\n    def _select_previous_log_container(\n        cls,\n        payload: Mapping[str, Any],\n    ) -> str:\n        status = payload.get(\n            "status"\n        )\n\n        if not isinstance(\n            status,\n            Mapping,\n        ):\n            raise KubernetesQueryError(\n                "Kubernetes Pod status is invalid"\n            )\n\n        statuses = status.get(\n            "containerStatuses"\n        )\n\n        if not isinstance(\n            statuses,\n            list,\n        ):\n            raise KubernetesQueryError(\n                "Kubernetes Pod container statuses are unavailable"\n            )\n\n        candidates = []\n\n        for item in statuses:\n            if not isinstance(\n                item,\n                Mapping,\n            ):\n                continue\n\n            name = item.get(\n                "name"\n            )\n\n            restart_count = cls._safe_int(\n                item.get(\n                    "restartCount"\n                )\n            )\n\n            last_state = item.get(\n                "lastState"\n            )\n\n            terminated = (\n                last_state.get(\n                    "terminated"\n                )\n                if isinstance(\n                    last_state,\n                    Mapping,\n                )\n                else None\n            )\n\n            if (\n                isinstance(\n                    name,\n                    str,\n                )\n                and name.strip()\n                and restart_count > 0\n                and isinstance(\n                    terminated,\n                    Mapping,\n                )\n            ):\n                candidates.append(\n                    name.strip()\n                )\n\n        unique = sorted(\n            set(\n                candidates\n            )\n        )\n\n        if len(\n            unique\n        ) != 1:\n            raise KubernetesQueryError(\n                "Kubernetes previous-log container selection is ambiguous"\n            )\n\n        return unique[0]\n\n    async def _get_previous_container_logs(\n        self,\n        *,\n        namespace: str,\n        target: str,\n        container: str,\n    ) -> dict[str, Any]:\n        url = self._pod_log_url(\n            namespace=namespace,\n            target=target,\n            container=container,\n        )\n\n        try:\n            if self.client is not None:\n                response = await self.client.get(\n                    url,\n                    headers=self._headers,\n                )\n            else:\n                async with httpx.AsyncClient(\n                    timeout=self.timeout_seconds,\n                    verify=self._httpx_verify,\n                    headers=self._headers,\n                ) as client:\n                    response = await client.get(\n                        url\n                    )\n\n            response.raise_for_status()\n\n        except httpx.TimeoutException as exc:\n            raise KubernetesQueryError(\n                "Kubernetes previous-log query timed out"\n            ) from exc\n\n        except httpx.HTTPStatusError as exc:\n            status_code = (\n                exc.response.status_code\n            )\n\n            if status_code in {\n                401,\n                403,\n            }:\n                raise KubernetesAuthorizationError(\n                    "Kubernetes previous-log authorization failed"\n                ) from exc\n\n            if status_code == 404:\n                raise KubernetesResourceNotFoundError(\n                    "Kubernetes previous container logs were not found"\n                ) from exc\n\n            raise KubernetesQueryError(\n                "Kubernetes previous-log API returned HTTP "\n                f"{status_code}"\n            ) from exc\n\n        except httpx.RequestError as exc:\n            raise KubernetesQueryError(\n                "Kubernetes previous-log request failed"\n            ) from exc\n\n        raw_text = response.text\n\n        if not isinstance(\n            raw_text,\n            str,\n        ):\n            raise KubernetesQueryError(\n                "Kubernetes previous-log response is invalid"\n            )\n\n        bounded_text, truncated = (\n            self._bound_log_text(\n                raw_text\n            )\n        )\n\n        redacted_text, redaction_count = (\n            self._redact_log_text(\n                bounded_text\n            )\n        )\n\n        if len(\n            redacted_text\n        ) > self._LOG_RETURN_MAX_CHARS:\n            redacted_text = redacted_text[\n                -self._LOG_RETURN_MAX_CHARS:\n            ]\n\n            truncated = True\n\n        lines = (\n            redacted_text.splitlines()\n            if redacted_text\n            else []\n        )\n\n        if len(\n            lines\n        ) > self._LOG_TAIL_LINES:\n            lines = lines[\n                -self._LOG_TAIL_LINES:\n            ]\n\n            redacted_text = "\\n".join(\n                lines\n            )\n\n            truncated = True\n\n        return {\n            "container_name": container,\n            "previous": True,\n            "line_count": len(\n                lines\n            ),\n            "truncated": (\n                truncated\n            ),\n            "redaction_count": (\n                redaction_count\n            ),\n            "excerpt": redacted_text,\n        }\n\n    @classmethod\n    def _bound_log_text(\n        cls,\n        value: str,\n    ) -> tuple[str, bool]:\n        normalized = (\n            value\n            .replace(\n                "\\r\\n",\n                "\\n",\n            )\n            .replace(\n                "\\r",\n                "\\n",\n            )\n            .replace(\n                "\\x00",\n                "",\n            )\n        )\n\n        encoded = normalized.encode(\n            "utf-8",\n            errors="replace",\n        )\n\n        truncated = (\n            len(\n                encoded\n            )\n            > cls._LOG_LIMIT_BYTES\n        )\n\n        if truncated:\n            encoded = encoded[\n                -cls._LOG_LIMIT_BYTES:\n            ]\n\n            normalized = encoded.decode(\n                "utf-8",\n                errors="replace",\n            )\n\n        return (\n            normalized,\n            truncated,\n        )\n\n    @staticmethod\n    def _redact_log_text(\n        value: str,\n    ) -> tuple[str, int]:\n        text = re.sub(\n            r"\\x1b\\[[0-?]*[ -/]*[@-~]",\n            "",\n            value,\n        )\n\n        total = 0\n\n        private_key_pattern = re.compile(\n            (\n                r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"\n                r".*?"\n                r"-----END [A-Z0-9 ]*PRIVATE KEY-----"\n            ),\n            re.IGNORECASE\n            | re.DOTALL,\n        )\n\n        text, count = (\n            private_key_pattern.subn(\n                "[REDACTED_PRIVATE_KEY]",\n                text,\n            )\n        )\n\n        total += count\n\n        jwt_pattern = re.compile(\n            (\n                r"\\beyJ[A-Za-z0-9_-]{10,}"\n                r"\\.[A-Za-z0-9_-]{10,}"\n                r"\\.[A-Za-z0-9_-]{10,}\\b"\n            )\n        )\n\n        text, count = jwt_pattern.subn(\n            "[REDACTED_JWT]",\n            text,\n        )\n\n        total += count\n\n        auth_pattern = re.compile(\n            (\n                r"(?i)\\b("\n                r"bearer|basic"\n                r")\\s+"\n                r"[A-Za-z0-9._~+/=-]{8,}"\n            )\n        )\n\n        text, count = auth_pattern.subn(\n            lambda match: (\n                match.group(1)\n                + " [REDACTED]"\n            ),\n            text,\n        )\n\n        total += count\n\n        key_value_pattern = re.compile(\n            (\n                r"(?i)\\b("\n                r"password|passwd|pwd|secret|token|"\n                r"api[_-]?key|access[_-]?key|"\n                r"client[_-]?secret"\n                r")\\b"\n                r"(\\s*[:=]\\s*)"\n                r"([\\"\']?)"\n                r"([^\\s,;\\"\']{4,})"\n                r"([\\"\']?)"\n            )\n        )\n\n        def replace_key_value(\n            match: re.Match[str],\n        ) -> str:\n            return (\n                match.group(1)\n                + match.group(2)\n                + "[REDACTED]"\n            )\n\n        text, count = (\n            key_value_pattern.subn(\n                replace_key_value,\n                text,\n            )\n        )\n\n        total += count\n\n        aws_key_pattern = re.compile(\n            r"\\bAKIA[0-9A-Z]{16}\\b"\n        )\n\n        text, count = (\n            aws_key_pattern.subn(\n                "[REDACTED_ACCESS_KEY]",\n                text,\n            )\n        )\n\n        total += count\n\n        return (\n            text,\n            total,\n        )\n\n    def _normalize_pod(\n        self,\n        payload: Mapping[str, Any],\n    ) -> dict[str, Any]:\n        metadata = payload.get("metadata")\n        status = payload.get("status")\n        spec = payload.get("spec")\n\n        if not isinstance(metadata, Mapping):\n            raise KubernetesQueryError(\n                "Kubernetes Pod metadata is invalid"\n            )\n\n        if not isinstance(status, Mapping):\n            raise KubernetesQueryError(\n                "Kubernetes Pod status is invalid"\n            )\n\n        if not isinstance(spec, Mapping):\n            spec = {}\n\n        conditions = self._normalize_conditions(\n            status.get("conditions")\n        )\n        containers = self._normalize_containers(\n            status.get("containerStatuses")\n        )\n\n        ready_condition = any(\n            condition["type"] == "Ready"\n            and condition["status"] == "True"\n            for condition in conditions\n        )\n        scheduled = any(\n            condition["type"] == "PodScheduled"\n            and condition["status"] == "True"\n            for condition in conditions\n        )\n        all_containers_ready = (\n            bool(containers)\n            and all(\n                container["ready"] is True\n                for container in containers\n            )\n        )\n        phase = status.get("phase")\n        ready = (\n            phase == "Running"\n            and ready_condition\n            and all_containers_ready\n        )\n        oom_killed = any(\n            container.get("state_reason")\n            == "OOMKilled"\n            or container.get(\n                "last_termination_reason"\n            )\n            == "OOMKilled"\n            for container in containers\n        )\n\n        return {\n            "api_version": payload.get(\n                "apiVersion"\n            ),\n            "kind": payload.get("kind"),\n            "uid": metadata.get("uid"),\n            "resource_version": metadata.get(\n                "resourceVersion"\n            ),\n            "creation_timestamp": metadata.get(\n                "creationTimestamp"\n            ),\n            "deletion_timestamp": metadata.get(\n                "deletionTimestamp"\n            ),\n            "labels": dict(\n                metadata.get("labels") or {}\n            ),\n            "phase": phase,\n            "ready": ready,\n            "scheduled": scheduled,\n            "oom_killed": oom_killed,\n            "pod_ip": status.get("podIP"),\n            "host_ip": status.get("hostIP"),\n            "node_name": spec.get("nodeName"),\n            "conditions": conditions,\n            "containers": containers,\n        }\n\n    @staticmethod\n    def _normalize_conditions(\n        value: Any,\n    ) -> list[dict[str, Any]]:\n        if not isinstance(value, list):\n            return []\n\n        conditions = []\n\n        for condition in value:\n            if not isinstance(condition, Mapping):\n                continue\n\n            conditions.append(\n                {\n                    "type": condition.get("type"),\n                    "status": condition.get("status"),\n                    "reason": condition.get("reason"),\n                    "message": condition.get("message"),\n                    "last_transition_time": (\n                        condition.get(\n                            "lastTransitionTime"\n                        )\n                    ),\n                }\n            )\n\n        return conditions\n\n    @classmethod\n    def _normalize_containers(\n        cls,\n        value: Any,\n    ) -> list[dict[str, Any]]:\n        if not isinstance(value, list):\n            return []\n\n        containers = []\n\n        for container in value:\n            if not isinstance(container, Mapping):\n                continue\n\n            state, state_reason = (\n                cls._container_state(\n                    container.get("state")\n                )\n            )\n            last_reason, last_finished_at = (\n                cls._last_termination(\n                    container.get("lastState")\n                )\n            )\n\n            containers.append(\n                {\n                    "name": container.get("name"),\n                    "ready": container.get("ready")\n                    is True,\n                    "restart_count": cls._safe_int(\n                        container.get("restartCount")\n                    ),\n                    "state": state,\n                    "state_reason": state_reason,\n                    "last_termination_reason": (\n                        last_reason\n                    ),\n                    "last_terminated_at": (\n                        last_finished_at\n                    ),\n                    "image": container.get("image"),\n                    "image_id": container.get(\n                        "imageID"\n                    ),\n                }\n            )\n\n        return containers\n\n    @staticmethod\n    def _container_state(\n        value: Any,\n    ) -> tuple[str | None, str | None]:\n        if not isinstance(value, Mapping):\n            return None, None\n\n        for name in (\n            "waiting",\n            "running",\n            "terminated",\n        ):\n            details = value.get(name)\n            if isinstance(details, Mapping):\n                return name, details.get("reason")\n\n        return None, None\n\n    @staticmethod\n    def _last_termination(\n        value: Any,\n    ) -> tuple[str | None, str | None]:\n        if not isinstance(value, Mapping):\n            return None, None\n\n        terminated = value.get("terminated")\n\n        if not isinstance(terminated, Mapping):\n            return None, None\n\n        return (\n            terminated.get("reason"),\n            terminated.get("finishedAt"),\n        )\n\n    @staticmethod\n    def _safe_int(\n        value: Any,\n    ) -> int:\n        try:\n            return int(value)\n        except (TypeError, ValueError):\n            return 0\n\n    def _pod_url(\n        self,\n        namespace: str,\n        target: str,\n    ) -> str:\n        if self.api_url is None:\n            raise KubernetesConfigurationError(\n                "KUBERNETES_API_URL is not configured"\n            )\n\n        safe_namespace = quote(\n            namespace,\n            safe="",\n        )\n        safe_target = quote(\n            target,\n            safe="",\n        )\n\n        return (\n            f"{self.api_url}/api/v1/namespaces/"\n            f"{safe_namespace}/pods/{safe_target}"\n        )\n\n    def _pod_log_url(\n        self,\n        *,\n        namespace: str,\n        target: str,\n        container: str,\n    ) -> str:\n        base = self._pod_url(\n            namespace=namespace,\n            target=target,\n        )\n\n        query = urlencode(\n            {\n                "container": container,\n                "previous": "true",\n                "tailLines": (\n                    self._LOG_TAIL_LINES\n                ),\n                "limitBytes": (\n                    self._LOG_LIMIT_BYTES\n                ),\n                "timestamps": "true",\n            }\n        )\n\n        return (\n            f"{base}/log?{query}"\n        )\n\n    @property\n    def _headers(self) -> dict[str, str]:\n        headers = {\n            "Accept": "application/json"\n        }\n\n        if self.bearer_token:\n            headers["Authorization"] = (\n                f"Bearer {self.bearer_token}"\n            )\n\n        return headers\n\n    @property\n    def _httpx_verify(\n        self,\n    ) -> bool | ssl.SSLContext:\n        if not self.verify_tls:\n            return False\n\n        if self.ca_file is None:\n            return True\n\n        if not self.ca_file.is_file():\n            raise KubernetesConfigurationError(\n                "Kubernetes CA file was not found"\n            )\n\n        try:\n            return ssl.create_default_context(\n                cafile=str(self.ca_file)\n            )\n        except OSError as exc:\n            raise KubernetesConfigurationError(\n                "Kubernetes CA file is invalid"\n            ) from exc\n\n    def _dry_run_response(\n        self,\n        action: str,\n        resource: str,\n        target: str,\n        namespace: str,\n    ) -> dict[str, Any]:\n        return {\n            "success": True,\n            "source": "mock_kubernetes",\n            "mode": "dry_run",\n            "production_signal": False,\n            "observed_at": self._now().isoformat(),\n            "action": action,\n            "resource": resource,\n            "target": target,\n            "namespace": namespace,\n            "message": (\n                "Kubernetes action simulated"\n            ),\n        }\n\n    def _now(self) -> datetime:\n        value = self._clock()\n\n        if value.tzinfo is None:\n            raise KubernetesConfigurationError(\n                "Kubernetes clock must return a "\n                "timezone-aware datetime"\n            )\n\n        return value.astimezone(UTC)\n\n    @classmethod\n    def _discover_in_cluster_url(\n        cls,\n    ) -> str | None:\n        host = os.getenv(\n            "KUBERNETES_SERVICE_HOST"\n        )\n\n        if not host:\n            return None\n\n        port = os.getenv(\n            "KUBERNETES_SERVICE_PORT_HTTPS",\n            os.getenv(\n                "KUBERNETES_SERVICE_PORT",\n                "443",\n            ),\n        )\n\n        normalized_host = host.strip()\n\n        if ":" in normalized_host and not (\n            normalized_host.startswith("[")\n            and normalized_host.endswith("]")\n        ):\n            normalized_host = (\n                f"[{normalized_host}]"\n            )\n\n        return (\n            f"https://{normalized_host}:{port}"\n        )\n\n    @staticmethod\n    def _read_token(\n        path: Path,\n    ) -> str:\n        try:\n            token = path.read_text(\n                encoding="utf-8"\n            ).strip()\n        except OSError as exc:\n            raise KubernetesConfigurationError(\n                "Kubernetes token file could not be read"\n            ) from exc\n\n        if not token:\n            raise KubernetesConfigurationError(\n                "Kubernetes token file is empty"\n            )\n\n        return token\n\n    @staticmethod\n    def _required_text(\n        value: Any,\n        name: str,\n    ) -> str:\n        if not isinstance(value, str):\n            raise KubernetesToolError(\n                f"Kubernetes {name} must be text"\n            )\n\n        normalized = value.strip()\n\n        if not normalized:\n            raise KubernetesToolError(\n                f"Kubernetes {name} cannot be empty"\n            )\n\n        return normalized\n\n    @staticmethod\n    def _read_bool(\n        name: str,\n        default: bool,\n    ) -> bool:\n        raw = os.getenv(name)\n\n        if raw is None:\n            return default\n\n        normalized = raw.strip().lower()\n\n        if normalized in {\n            "1",\n            "true",\n            "yes",\n            "on",\n        }:\n            return True\n\n        if normalized in {\n            "0",\n            "false",\n            "no",\n            "off",\n        }:\n            return False\n\n        raise KubernetesConfigurationError(\n            f"{name} must be a boolean"\n        )\n\n    @staticmethod\n    def _read_positive_float(\n        name: str,\n        default: float,\n    ) -> float:\n        raw = os.getenv(name)\n\n        if raw is None:\n            return default\n\n        try:\n            value = float(raw)\n        except ValueError as exc:\n            raise KubernetesConfigurationError(\n                f"{name} must be a number"\n            ) from exc\n\n        if value <= 0:\n            raise KubernetesConfigurationError(\n                f"{name} must be positive"\n            )\n\n        return value\n'
BENCHMARK_ENGINE_SOURCE = 'from __future__ import annotations\n\nimport json\nfrom dataclasses import dataclass\nfrom datetime import UTC, datetime\nfrom types import SimpleNamespace\nfrom typing import Any\n\nfrom pydantic import BaseModel, ConfigDict, Field\n\nfrom services.agent_runtime.app.evaluation.real_incident.llm_run import (\n    create_historical_llm_runtime,\n)\nfrom services.agent_runtime.app.investigation.coordinator import (\n    EvidenceDrivenInvestigationCoordinator,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    EvidenceItem,\n    InvestigationDecision,\n    InvestigationLimits,\n    InvestigationProbe,\n    InvestigationState,\n    InvestigationStopReason,\n)\nfrom services.agent_runtime.app.investigation.reasoner import (\n    BaseInvestigationReasoner,\n)\n\n\nclass IntelligenceBenchmarkError(\n    RuntimeError\n):\n    pass\n\n\nclass BenchmarkScenario(BaseModel):\n    """\n    One hidden-label Investigation exam.\n\n    hidden_* fields are evaluator-only. They never enter the Agent context,\n    InvestigationScope, LLM prompt, EvidenceItem facts or decision history.\n    """\n\n    model_config = ConfigDict(\n        frozen=True,\n        extra="forbid",\n    )\n\n    key: str\n    title: str\n\n    alert_name: str\n    alert_message: str\n\n    resource: str = "payment-api"\n    namespace: str = "payment"\n    cluster: str = "benchmark-lab"\n\n    evidence_by_probe: dict[\n        InvestigationProbe,\n        dict[str, Any] | str,\n    ]\n\n    hidden_expected_stop_reason: (\n        InvestigationStopReason\n    )\n\n    hidden_required_probes: list[\n        InvestigationProbe\n    ] = Field(\n        default_factory=list\n    )\n\n    hidden_preferred_first_probes: list[\n        InvestigationProbe\n    ] = Field(\n        default_factory=list\n    )\n\n    hidden_root_cause_keyword_groups: list[\n        list[str]\n    ] = Field(\n        default_factory=list\n    )\n\n    hidden_missing_capability_keywords: list[\n        str\n    ] = Field(\n        default_factory=list\n    )\n\n    hidden_max_reasonable_tool_calls: int = Field(\n        default=4,\n        ge=0,\n        le=10,\n    )\n\n\nclass ScenarioScore(BaseModel):\n    model_config = ConfigDict(\n        frozen=True,\n        extra="forbid",\n    )\n\n    scenario_key: str\n    title: str\n\n    score: float = Field(\n        ge=0.0,\n        le=100.0,\n    )\n\n    expected_stop_reason: str\n    outcome_correct: bool\n    grounding_correct: bool\n    required_probe_coverage: float\n    first_probe_quality: bool | None\n    tool_efficiency: float\n    root_cause_or_abstention_correct: bool\n    missing_capability_awareness: bool | None\n\n    final_status: str\n    final_stop_reason: str | None\n    failure_code: str | None\n    epistemic_guard_code: str | None\n    guard_rescued: bool\n\n    attempted_probes: list[str]\n    tool_call_count: int\n    iteration_count: int\n\n    conclusion_root_cause: str | None\n    conclusion_confidence: float | None\n\n    decision_trace: list[\n        dict[str, Any]\n    ]\n\n    notes: list[str] = Field(\n        default_factory=list\n    )\n\n\nclass IntelligenceBenchmarkReport(BaseModel):\n    model_config = ConfigDict(\n        frozen=True,\n        extra="forbid",\n    )\n\n    schema_version: str = "v1"\n    generated_at: datetime\n\n    provider: str\n    mode: str\n\n    scenario_count: int\n    overall_score: float = Field(\n        ge=0.0,\n        le=100.0,\n    )\n\n    outcome_accuracy: float = Field(\n        ge=0.0,\n        le=100.0,\n    )\n\n    abstention_accuracy: float = Field(\n        ge=0.0,\n        le=100.0,\n    )\n\n    sufficient_evidence_accuracy: float = Field(\n        ge=0.0,\n        le=100.0,\n    )\n\n    average_tool_calls: float = Field(\n        ge=0.0,\n    )\n\n    guard_rescue_count: int = Field(\n        ge=0,\n    )\n\n    guard_rescue_rate: float = Field(\n        ge=0.0,\n        le=100.0,\n    )\n\n    scenarios: list[\n        ScenarioScore\n    ]\n\n    strongest_signals: list[str]\n    weakest_signals: list[str]\n\n\nclass BenchmarkProbeExecutor:\n    """\n    Synthetic evidence backend for model-intelligence evaluation.\n\n    The model sees only the evidence corresponding to probes it chose.\n    Hidden labels remain inside BenchmarkScenario and never cross this class\n    into EvidenceItem.\n    """\n\n    def __init__(\n        self,\n        scenario: BenchmarkScenario,\n        *,\n        observed_at: datetime,\n    ) -> None:\n        self.scenario = scenario\n        self.observed_at = observed_at\n        self.calls: list[\n            InvestigationProbe\n        ] = []\n\n    async def collect(\n        self,\n        context,\n        scope,\n        probe,\n    ) -> EvidenceItem:\n        self.calls.append(\n            probe\n        )\n\n        value = (\n            self.scenario\n            .evidence_by_probe\n            .get(\n                probe\n            )\n        )\n\n        if isinstance(\n            value,\n            str,\n        ):\n            raise RuntimeError(\n                "Benchmark probe unavailable"\n            )\n\n        if value is None:\n            raise RuntimeError(\n                "Benchmark probe has no observation"\n            )\n\n        source = (\n            "kubernetes"\n            if probe\n            in {\n                InvestigationProbe.KUBERNETES_POD_STATE,\n                (\n                    InvestigationProbe\n                    .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n                ),\n            }\n            else "prometheus"\n        )\n\n        return EvidenceItem(\n            evidence_id=(\n                f"{self.scenario.key}:"\n                f"{probe.value}"\n            ),\n            probe=probe,\n            source=source,\n            success=True,\n            trusted=True,\n            production_signal=True,\n            reliability=1.0,\n            observed_at=self.observed_at,\n            facts=dict(\n                value\n            ),\n        )\n\n\nclass TracingReasoner(\n    BaseInvestigationReasoner\n):\n    """\n    Transparent delegate that records the actual Agent decisions.\n\n    It does not modify prompts, decisions, state or provider behavior.\n    """\n\n    def __init__(\n        self,\n        delegate: BaseInvestigationReasoner,\n    ) -> None:\n        if not isinstance(\n            delegate,\n            BaseInvestigationReasoner,\n        ):\n            raise TypeError(\n                "Benchmark delegate reasoner is invalid"\n            )\n\n        self.delegate = delegate\n\n        self.decisions: list[\n            InvestigationDecision\n        ] = []\n\n        self.states: list[\n            InvestigationState\n        ] = []\n\n    async def decide(\n        self,\n        scope,\n        state,\n    ) -> InvestigationDecision:\n        self.states.append(\n            state.model_copy(\n                deep=True\n            )\n        )\n\n        decision = await (\n            self.delegate.decide(\n                scope,\n                state,\n            )\n        )\n\n        self.decisions.append(\n            decision.model_copy(\n                deep=True\n            )\n        )\n\n        return decision\n\n\ndef _context(\n    scenario: BenchmarkScenario,\n):\n    return SimpleNamespace(\n        event=SimpleNamespace(\n            signal=SimpleNamespace(\n                name=scenario.alert_name,\n                message=(\n                    scenario.alert_message\n                ),\n            ),\n            resources=[\n                SimpleNamespace(\n                    name=scenario.resource,\n                    namespace=scenario.namespace,\n                    cluster=scenario.cluster,\n                )\n            ],\n        ),\n        metadata={},\n        variables={},\n    )\n\n\ndef _normalize_text(\n    value: str | None,\n) -> str:\n    if not value:\n        return ""\n\n    return (\n        value\n        .strip()\n        .lower()\n    )\n\n\ndef _missing_capability_text(\n    decisions: list[\n        InvestigationDecision\n    ],\n) -> str:\n    """\n    Return only explicit unresolved-evidence language.\n\n    Hypothesis causes, rationale prose and conclusion root-cause text are\n    intentionally excluded. Guessing "application panic" is not the same as\n    recognizing that application/container logs are missing.\n    """\n\n    fragments: list[\n        str\n    ] = []\n\n    for decision in decisions:\n        for hypothesis in decision.hypotheses:\n            fragments.extend(\n                hypothesis.missing_evidence\n            )\n\n        if decision.conclusion is not None:\n            fragments.extend(\n                decision.conclusion.remaining_uncertainties\n            )\n\n    return _normalize_text(\n        "\\n".join(\n            fragments\n        )\n    )\n\n\ndef _keyword_groups_match(\n    text: str,\n    groups: list[\n        list[str]\n    ],\n) -> bool:\n    normalized = _normalize_text(\n        text\n    )\n\n    if not groups:\n        return True\n\n    for group in groups:\n        if not any(\n            _normalize_text(\n                token\n            )\n            in normalized\n            for token in group\n        ):\n            return False\n\n    return True\n\n\ndef _decision_trace(\n    decisions: list[\n        InvestigationDecision\n    ],\n) -> list[\n    dict[str, Any]\n]:\n    trace = []\n\n    for index, decision in enumerate(\n        decisions,\n        start=1,\n    ):\n        trace.append(\n            {\n                "iteration": index,\n                "hypotheses": [\n                    {\n                        "hypothesis_id": (\n                            item.hypothesis_id\n                        ),\n                        "cause": item.cause,\n                        "confidence": (\n                            item.confidence\n                        ),\n                        "supporting_evidence_ids": list(\n                            item.supporting_evidence_ids\n                        ),\n                        "conflicting_evidence_ids": list(\n                            item.conflicting_evidence_ids\n                        ),\n                        "missing_evidence": list(\n                            item.missing_evidence\n                        ),\n                    }\n                    for item in decision.hypotheses\n                ],\n                "rationale_summary": (\n                    decision.rationale_summary\n                ),\n                "stop": decision.stop,\n                "stop_reason": (\n                    decision.stop_reason.value\n                    if decision.stop_reason\n                    is not None\n                    else None\n                ),\n                "next_probe": (\n                    decision.next_probe.value\n                    if decision.next_probe\n                    is not None\n                    else None\n                ),\n                "conclusion": (\n                    decision.conclusion.model_dump(\n                        mode="json"\n                    )\n                    if decision.conclusion\n                    is not None\n                    else None\n                ),\n            }\n        )\n\n    return trace\n\n\ndef score_scenario(\n    *,\n    scenario: BenchmarkScenario,\n    state: InvestigationState,\n    decisions: list[\n        InvestigationDecision\n    ],\n) -> ScenarioScore:\n    attempted = list(state.attempted_probes)\n    expected_stop = scenario.hidden_expected_stop_reason\n\n    legitimate_terminal = (\n        state.status.value == "concluded"\n        and state.stop_reason == expected_stop\n    )\n    outcome_correct = legitimate_terminal\n\n    if expected_stop == InvestigationStopReason.SUFFICIENT_EVIDENCE:\n        if not legitimate_terminal or state.conclusion is None:\n            grounding_correct = False\n        else:\n            trusted_ids = {\n                item.evidence_id\n                for item in state.evidence\n                if (\n                    item.success\n                    and item.trusted\n                    and item.production_signal\n                )\n            }\n            conclusion_ids = set(state.conclusion.evidence_ids)\n            grounding_correct = (\n                bool(conclusion_ids)\n                and conclusion_ids.issubset(trusted_ids)\n            )\n    else:\n        grounding_correct = (\n            legitimate_terminal\n            and state.conclusion is None\n        )\n\n    required = set(scenario.hidden_required_probes)\n    attempted_set = set(attempted)\n    required_probe_coverage = (\n        len(required & attempted_set) / len(required)\n        if required\n        else 1.0\n    )\n\n    if scenario.hidden_preferred_first_probes:\n        first_probe_quality = (\n            bool(attempted)\n            and attempted[0]\n            in scenario.hidden_preferred_first_probes\n        )\n    else:\n        first_probe_quality = None\n\n    max_calls = scenario.hidden_max_reasonable_tool_calls\n    if max_calls <= 0:\n        tool_efficiency = 1.0 if state.tool_call_count == 0 else 0.0\n    elif state.tool_call_count <= max_calls:\n        tool_efficiency = 1.0\n    else:\n        tool_efficiency = max(\n            0.0,\n            1.0 - (\n                state.tool_call_count - max_calls\n            ) / max_calls,\n        )\n\n    if expected_stop == InvestigationStopReason.SUFFICIENT_EVIDENCE:\n        root_cause_or_abstention_correct = (\n            legitimate_terminal\n            and state.conclusion is not None\n            and _keyword_groups_match(\n                state.conclusion.root_cause,\n                scenario.hidden_root_cause_keyword_groups,\n            )\n        )\n    else:\n        root_cause_or_abstention_correct = (\n            legitimate_terminal\n            and state.conclusion is None\n        )\n\n    if scenario.hidden_missing_capability_keywords:\n        reasoner_text = _missing_capability_text(decisions)\n        missing_capability_awareness = any(\n            _normalize_text(keyword) in reasoner_text\n            for keyword\n            in scenario.hidden_missing_capability_keywords\n        )\n    else:\n        missing_capability_awareness = None\n\n    score = 0.0\n    score += 30.0 if outcome_correct else 0.0\n    score += 20.0 if grounding_correct else 0.0\n\n    probe_weight = 30.0 if first_probe_quality is None else 20.0\n    score += required_probe_coverage * probe_weight\n\n    if first_probe_quality is not None:\n        score += 10.0 if first_probe_quality else 0.0\n\n    score += tool_efficiency * 10.0\n    score += 10.0 if root_cause_or_abstention_correct else 0.0\n\n    guard_rescued = (\n        state.epistemic_guard_code\n        is not None\n        and outcome_correct\n    )\n\n    if guard_rescued:\n        score = min(\n            score,\n            85.0,\n        )\n\n    notes: list[str] = []\n\n    if guard_rescued:\n        notes.append(\n            "Epistemic guard converted an unsupported sufficient-evidence "\n            "decision into safe insufficient_evidence."\n        )\n\n    if not outcome_correct:\n        notes.append(\n            "Final stop reason/status did not match the hidden evaluator label."\n        )\n\n    if state.status.value == "failed":\n        notes.append(\n            "Failed investigation is not counted as a valid abstention."\n        )\n\n    if (\n        expected_stop != InvestigationStopReason.SUFFICIENT_EVIDENCE\n        and state.conclusion is not None\n    ):\n        notes.append(\n            "Agent produced an RCA where the benchmark expected abstention."\n        )\n\n    if missing_capability_awareness is False:\n        notes.append(\n            "Agent did not explicitly recognize the expected missing capability."\n        )\n\n    return ScenarioScore(\n        scenario_key=scenario.key,\n        title=scenario.title,\n        expected_stop_reason=expected_stop.value,\n        score=round(\n            min(100.0, max(0.0, score)),\n            1,\n        ),\n        outcome_correct=outcome_correct,\n        grounding_correct=grounding_correct,\n        required_probe_coverage=round(\n            required_probe_coverage,\n            3,\n        ),\n        first_probe_quality=first_probe_quality,\n        tool_efficiency=round(\n            tool_efficiency,\n            3,\n        ),\n        root_cause_or_abstention_correct=(\n            root_cause_or_abstention_correct\n        ),\n        missing_capability_awareness=(\n            missing_capability_awareness\n        ),\n        final_status=state.status.value,\n        final_stop_reason=(\n            state.stop_reason.value\n            if state.stop_reason is not None\n            else None\n        ),\n        failure_code=state.failure_code,\n        epistemic_guard_code=(\n            state.epistemic_guard_code\n        ),\n        guard_rescued=guard_rescued,\n        attempted_probes=[\n            item.value\n            for item in attempted\n        ],\n        tool_call_count=state.tool_call_count,\n        iteration_count=state.iteration_count,\n        conclusion_root_cause=(\n            state.conclusion.root_cause\n            if state.conclusion is not None\n            else None\n        ),\n        conclusion_confidence=(\n            state.conclusion.confidence\n            if state.conclusion is not None\n            else None\n        ),\n        decision_trace=_decision_trace(decisions),\n        notes=notes,\n    )\n\n\nasync def run_scenario(\n    *,\n    reasoner: BaseInvestigationReasoner,\n    scenario: BenchmarkScenario,\n    limits: InvestigationLimits,\n    observed_at: datetime,\n) -> ScenarioScore:\n    tracing = TracingReasoner(\n        reasoner\n    )\n\n    probes = BenchmarkProbeExecutor(\n        scenario,\n        observed_at=observed_at,\n    )\n\n    coordinator = (\n        EvidenceDrivenInvestigationCoordinator(\n            reasoner=tracing,\n            probe_executor=probes,\n            limits=limits,\n            utc_clock=lambda: observed_at,\n        )\n    )\n\n    state = await coordinator.investigate(\n        _context(\n            scenario\n        )\n    )\n\n    return score_scenario(\n        scenario=scenario,\n        state=state,\n        decisions=tracing.decisions,\n    )\n\n\ndef build_bailian_reasoner(\n    *,\n    provider_name: str,\n    limits: InvestigationLimits,\n) -> BaseInvestigationReasoner:\n    runtime = (\n        create_historical_llm_runtime(\n            limits=limits,\n            provider_name=provider_name,\n        )\n    )\n\n    coordinator = getattr(\n        runtime,\n        "investigation_coordinator",\n        None,\n    )\n\n    reasoner = getattr(\n        coordinator,\n        "reasoner",\n        None,\n    )\n\n    if not isinstance(\n        reasoner,\n        BaseInvestigationReasoner,\n    ):\n        raise IntelligenceBenchmarkError(\n            "Benchmark could not obtain the canonical Investigation reasoner"\n        )\n\n    return reasoner\n\n\ndef build_report(\n    *,\n    provider: str,\n    mode: str,\n    scenarios: list[\n        ScenarioScore\n    ],\n) -> IntelligenceBenchmarkReport:\n    if not scenarios:\n        raise IntelligenceBenchmarkError(\n            "Benchmark produced no scenario results"\n        )\n\n    overall_score = (\n        sum(item.score for item in scenarios)\n        / len(scenarios)\n    )\n\n    outcome_accuracy = (\n        sum(\n            1\n            for item in scenarios\n            if item.outcome_correct\n        )\n        / len(scenarios)\n        * 100.0\n    )\n\n    expected_abstention_cases = [\n        item\n        for item in scenarios\n        if item.expected_stop_reason\n        != InvestigationStopReason.SUFFICIENT_EVIDENCE.value\n    ]\n\n    abstention_accuracy = (\n        sum(\n            1\n            for item in expected_abstention_cases\n            if (\n                item.outcome_correct\n                and item.root_cause_or_abstention_correct\n            )\n        )\n        / len(expected_abstention_cases)\n        * 100.0\n        if expected_abstention_cases\n        else 0.0\n    )\n\n    expected_sufficient_cases = [\n        item\n        for item in scenarios\n        if item.expected_stop_reason\n        == InvestigationStopReason.SUFFICIENT_EVIDENCE.value\n    ]\n\n    sufficient_evidence_accuracy = (\n        sum(\n            1\n            for item in expected_sufficient_cases\n            if (\n                item.outcome_correct\n                and item.grounding_correct\n                and item.root_cause_or_abstention_correct\n            )\n        )\n        / len(expected_sufficient_cases)\n        * 100.0\n        if expected_sufficient_cases\n        else 0.0\n    )\n\n    average_tool_calls = (\n        sum(\n            item.tool_call_count\n            for item in scenarios\n        )\n        / len(scenarios)\n    )\n\n    guard_rescue_count = sum(\n        1\n        for item in scenarios\n        if item.guard_rescued\n    )\n\n    guard_rescue_rate = (\n        guard_rescue_count\n        / len(scenarios)\n        * 100.0\n    )\n\n    ordered = sorted(\n        scenarios,\n        key=lambda item: (\n            item.score,\n            item.scenario_key,\n        ),\n    )\n\n    weakest = [\n        f"{item.scenario_key}: {item.score:.1f}/100"\n        for item in ordered[:3]\n    ]\n\n    strongest = [\n        f"{item.scenario_key}: {item.score:.1f}/100"\n        for item in reversed(ordered[-3:])\n    ]\n\n    return IntelligenceBenchmarkReport(\n        generated_at=datetime.now(UTC),\n        provider=provider,\n        mode=mode,\n        scenario_count=len(scenarios),\n        overall_score=round(\n            overall_score,\n            1,\n        ),\n        outcome_accuracy=round(\n            outcome_accuracy,\n            1,\n        ),\n        abstention_accuracy=round(\n            abstention_accuracy,\n            1,\n        ),\n        sufficient_evidence_accuracy=round(\n            sufficient_evidence_accuracy,\n            1,\n        ),\n        average_tool_calls=round(\n            average_tool_calls,\n            2,\n        ),\n        guard_rescue_count=(\n            guard_rescue_count\n        ),\n        guard_rescue_rate=round(\n            guard_rescue_rate,\n            1,\n        ),\n        scenarios=scenarios,\n        strongest_signals=strongest,\n        weakest_signals=weakest,\n    )\n\n\ndef render_report(\n    report: IntelligenceBenchmarkReport,\n) -> str:\n    lines = [\n        "=" * 96,\n        "INVESTIGATION INTELLIGENCE BENCHMARK v1",\n        "=" * 96,\n        "",\n        f"GeneratedAt: {report.generated_at.isoformat()}",\n        f"Provider: {report.provider}",\n        f"Mode: {report.mode}",\n        f"Scenarios: {report.scenario_count}",\n        "",\n        f"OverallScore: {report.overall_score:.1f}/100",\n        f"OutcomeAccuracy: {report.outcome_accuracy:.1f}%",\n        f"AbstentionAccuracy: {report.abstention_accuracy:.1f}%",\n        (\n            "SufficientEvidenceAccuracy: "\n            f"{report.sufficient_evidence_accuracy:.1f}%"\n        ),\n        f"AverageToolCalls: {report.average_tool_calls:.2f}",\n        f"GuardRescueCount: {report.guard_rescue_count}",\n        f"GuardRescueRate: {report.guard_rescue_rate:.1f}%",\n        "",\n        "Important:",\n        "- This is a controlled synthetic-evidence intelligence benchmark.",\n        "- The actual LLM Investigation reasoner is used in live mode.",\n        "- Hidden evaluator labels never enter the Agent prompt.",\n        "- This is stronger than unit testing but is not a production validation.",\n        "",\n        "SCENARIOS",\n        "-" * 96,\n    ]\n\n    for item in report.scenarios:\n        lines.extend(\n            [\n                "",\n                (\n                    f"[{item.score:5.1f}] "\n                    f"{item.scenario_key} - {item.title}"\n                ),\n                (\n                    "  outcome_correct="\n                    f"{item.outcome_correct}"\n                ),\n                (\n                    "  grounding_correct="\n                    f"{item.grounding_correct}"\n                ),\n                (\n                    "  required_probe_coverage="\n                    f"{item.required_probe_coverage:.3f}"\n                ),\n                (\n                    "  first_probe_quality="\n                    f"{item.first_probe_quality}"\n                ),\n                (\n                    "  tool_efficiency="\n                    f"{item.tool_efficiency:.3f}"\n                ),\n                (\n                    "  root_cause_or_abstention_correct="\n                    f"{item.root_cause_or_abstention_correct}"\n                ),\n                (\n                    "  missing_capability_awareness="\n                    f"{item.missing_capability_awareness}"\n                ),\n                (\n                    "  expected_stop_reason="\n                    f"{item.expected_stop_reason}"\n                ),\n                (\n                    "  final="\n                    f"{item.final_status}/"\n                    f"{item.final_stop_reason}"\n                ),\n                (\n                    "  failure_code="\n                    f"{item.failure_code}"\n                ),\n                (\n                    "  epistemic_guard_code="\n                    f"{item.epistemic_guard_code}"\n                ),\n                (\n                    "  guard_rescued="\n                    f"{item.guard_rescued}"\n                ),\n                (\n                    "  probes="\n                    + ", ".join(\n                        item.attempted_probes\n                    )\n                ),\n                (\n                    "  conclusion="\n                    + (\n                        item.conclusion_root_cause\n                        or "<NONE>"\n                    )\n                ),\n                (\n                    "  confidence="\n                    + (\n                        str(\n                            item.conclusion_confidence\n                        )\n                        if item.conclusion_confidence\n                        is not None\n                        else "<NONE>"\n                    )\n                ),\n            ]\n        )\n\n        for note in item.notes:\n            lines.append(\n                f"  note: {note}"\n            )\n\n        lines.append(\n            "  decision_trace:"\n        )\n\n        for decision in item.decision_trace:\n            lines.append(\n                "    "\n                + json.dumps(\n                    decision,\n                    ensure_ascii=False,\n                    sort_keys=True,\n                )\n            )\n\n    lines.extend(\n        [\n            "",\n            "STRONGEST",\n            "-" * 96,\n            *[\n                f"- {value}"\n                for value\n                in report.strongest_signals\n            ],\n            "",\n            "WEAKEST",\n            "-" * 96,\n            *[\n                f"- {value}"\n                for value\n                in report.weakest_signals\n            ],\n            "",\n            "=" * 96,\n        ]\n    )\n\n    return "\\n".join(\n        lines\n    ) + "\\n"\n\n\n__all__ = [\n    "BenchmarkProbeExecutor",\n    "BenchmarkScenario",\n    "IntelligenceBenchmarkError",\n    "IntelligenceBenchmarkReport",\n    "ScenarioScore",\n    "TracingReasoner",\n    "build_bailian_reasoner",\n    "build_report",\n    "render_report",\n    "run_scenario",\n    "score_scenario",\n]\n'
BENCHMARK_SCENARIOS_SOURCE = 'from __future__ import annotations\n\nfrom services.agent_runtime.app.evaluation.intelligence_benchmark.engine import (\n    BenchmarkScenario,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    InvestigationProbe,\n    InvestigationStopReason,\n)\n\n\ndef _all_probes(\n    *,\n    pod_state,\n    working_set,\n    memory_limit,\n    restart_count,\n):\n    return {\n        InvestigationProbe.KUBERNETES_POD_STATE: (\n            pod_state\n        ),\n        InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET: {\n            "value_sum": float(\n                working_set\n            ),\n        },\n        InvestigationProbe.PROMETHEUS_MEMORY_LIMIT: {\n            "value_sum": float(\n                memory_limit\n            ),\n        },\n        InvestigationProbe.PROMETHEUS_RESTART_COUNT: {\n            "value_sum": float(\n                restart_count\n            ),\n        },\n    }\n\n\nSCENARIOS = [\n    BenchmarkScenario(\n        key="oom_limit_pressure",\n        title=(\n            "Clear OOM with memory pressure near container limit"\n        ),\n        alert_name="PodOOMKilled",\n        alert_message=(\n            "payment-api restarted unexpectedly"\n        ),\n        evidence_by_probe=_all_probes(\n            pod_state={\n                "phase": "Running",\n                "ready": False,\n                "scheduled": True,\n                "oom_killed": True,\n                "max_restart_count": 7,\n                "state_reasons": (\n                    "CrashLoopBackOff"\n                ),\n                "last_termination_reasons": (\n                    "OOMKilled"\n                ),\n            },\n            working_set=530_000_000,\n            memory_limit=536_870_912,\n            restart_count=7,\n        ),\n        hidden_expected_stop_reason=(\n            InvestigationStopReason.SUFFICIENT_EVIDENCE\n        ),\n        hidden_required_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n            InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,\n        ],\n        hidden_preferred_first_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n        ],\n        hidden_root_cause_keyword_groups=[\n            [\n                "memory",\n                "内存",\n            ],\n            [\n                "limit",\n                "限制",\n                "oom",\n            ],\n        ],\n        hidden_max_reasonable_tool_calls=4,\n    ),\n    BenchmarkScenario(\n        key="crashloop_not_memory",\n        title=(\n            "CrashLoop with normal memory should not be mislabeled as OOM"\n        ),\n        alert_name="PodRestartHigh",\n        alert_message=(\n            "payment-api restart count is increasing"\n        ),\n        evidence_by_probe={\n            **_all_probes(\n                pod_state={\n                    "phase": "Running",\n                    "ready": False,\n                    "scheduled": True,\n                    "oom_killed": False,\n                    "max_restart_count": 9,\n                    "state_reasons": (\n                        "CrashLoopBackOff"\n                    ),\n                    "last_termination_reasons": (\n                        "Error"\n                    ),\n                },\n                working_set=120_000_000,\n                memory_limit=536_870_912,\n                restart_count=9,\n            ),\n            (\n                InvestigationProbe\n                .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n            ): "unavailable",\n        },\n        hidden_expected_stop_reason=(\n            InvestigationStopReason.INSUFFICIENT_EVIDENCE\n        ),\n        hidden_required_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n            InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,\n        ],\n        hidden_preferred_first_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n            InvestigationProbe.PROMETHEUS_RESTART_COUNT,\n        ],\n        hidden_missing_capability_keywords=[\n            "log",\n            "日志",\n            "stderr",\n            "stdout",\n            "container output",\n        ],\n        hidden_max_reasonable_tool_calls=4,\n    ),\n    BenchmarkScenario(\n        key="conflicting_oom_signal",\n        title=(\n            "Alert suggests OOM while bounded evidence does not confirm it"\n        ),\n        alert_name="PodOOMKilled",\n        alert_message=(\n            "OOM-related alert fired for payment-api"\n        ),\n        evidence_by_probe=_all_probes(\n            pod_state={\n                "phase": "Running",\n                "ready": True,\n                "scheduled": True,\n                "oom_killed": False,\n                "max_restart_count": 1,\n                "state_reasons": "",\n                "last_termination_reasons": (\n                    "Completed"\n                ),\n            },\n            working_set=470_000_000,\n            memory_limit=536_870_912,\n            restart_count=1,\n        ),\n        hidden_expected_stop_reason=(\n            InvestigationStopReason.INSUFFICIENT_EVIDENCE\n        ),\n        hidden_required_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n            InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,\n        ],\n        hidden_preferred_first_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n        ],\n        hidden_max_reasonable_tool_calls=4,\n    ),\n    BenchmarkScenario(\n        key="crashloop_previous_log_rca",\n        title=(\n            "CrashLoop previous-container log provides causal startup evidence"\n        ),\n        alert_name="PodRestartHigh",\n        alert_message=(\n            "payment-api restart count is increasing"\n        ),\n        evidence_by_probe={\n            **_all_probes(\n                pod_state={\n                    "phase": "Running",\n                    "ready": False,\n                    "scheduled": True,\n                    "oom_killed": False,\n                    "max_restart_count": 9,\n                    "state_reasons": (\n                        "CrashLoopBackOff"\n                    ),\n                    "last_termination_reasons": (\n                        "Error"\n                    ),\n                },\n                working_set=120_000_000,\n                memory_limit=536_870_912,\n                restart_count=9,\n            ),\n            (\n                InvestigationProbe\n                .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n            ): {\n                "temporal_basis": (\n                    "previous_container"\n                ),\n                "container_name": (\n                    "payment-api"\n                ),\n                "previous": True,\n                "log_line_count": 2,\n                "tool_truncated": False,\n                "evidence_truncated": False,\n                "redaction_count": 1,\n                "log_excerpt": (\n                    "panic: invalid configuration: "\n                    "MAX_CONNECTIONS must be >= 1\\n"\n                    "password=[REDACTED]"\n                ),\n            },\n        },\n        hidden_expected_stop_reason=(\n            InvestigationStopReason.SUFFICIENT_EVIDENCE\n        ),\n        hidden_required_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n            (\n                InvestigationProbe\n                .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n            ),\n        ],\n        hidden_preferred_first_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n            InvestigationProbe.PROMETHEUS_RESTART_COUNT,\n        ],\n        hidden_root_cause_keyword_groups=[\n            [\n                "panic",\n            ],\n            [\n                "config",\n                "configuration",\n            ],\n        ],\n        hidden_max_reasonable_tool_calls=4,\n    ),\n    BenchmarkScenario(\n        key="memory_false_alarm",\n        title=(\n            "Healthy memory state should drive safe abstention"\n        ),\n        alert_name="PodMemoryHigh",\n        alert_message=(\n            "payment-api memory alert fired"\n        ),\n        evidence_by_probe=_all_probes(\n            pod_state={\n                "phase": "Running",\n                "ready": True,\n                "scheduled": True,\n                "oom_killed": False,\n                "max_restart_count": 0,\n                "state_reasons": "",\n                "last_termination_reasons": "",\n            },\n            working_set=220_000_000,\n            memory_limit=536_870_912,\n            restart_count=0,\n        ),\n        hidden_expected_stop_reason=(\n            InvestigationStopReason.INSUFFICIENT_EVIDENCE\n        ),\n        hidden_required_probes=[\n            InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,\n            InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,\n        ],\n        hidden_preferred_first_probes=[\n            InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,\n            InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,\n        ],\n        hidden_max_reasonable_tool_calls=3,\n    ),\n    BenchmarkScenario(\n        key="probe_backend_failure",\n        title=(\n            "Unavailable pod evidence must not produce fabricated RCA"\n        ),\n        alert_name="PodRestartHigh",\n        alert_message=(\n            "payment-api restarts are elevated"\n        ),\n        evidence_by_probe={\n            InvestigationProbe.KUBERNETES_POD_STATE: (\n                "unavailable"\n            ),\n            InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET: {\n                "value_sum": 150_000_000.0,\n            },\n            InvestigationProbe.PROMETHEUS_MEMORY_LIMIT: {\n                "value_sum": 536_870_912.0,\n            },\n            InvestigationProbe.PROMETHEUS_RESTART_COUNT: {\n                "value_sum": 6.0,\n            },\n        },\n        hidden_expected_stop_reason=(\n            InvestigationStopReason.INSUFFICIENT_EVIDENCE\n        ),\n        hidden_required_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n        ],\n        hidden_preferred_first_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n            InvestigationProbe.PROMETHEUS_RESTART_COUNT,\n        ],\n        hidden_missing_capability_keywords=[\n            "log",\n            "日志",\n            "pod state",\n            "termination",\n        ],\n        hidden_max_reasonable_tool_calls=4,\n    ),\n    BenchmarkScenario(\n        key="oom_without_explanatory_metrics",\n        title=(\n            "OOM termination with non-explanatory sampled metrics should remain cautious"\n        ),\n        alert_name="PodOOMKilled",\n        alert_message=(\n            "payment-api was terminated and restarted"\n        ),\n        evidence_by_probe=_all_probes(\n            pod_state={\n                "phase": "Running",\n                "ready": True,\n                "scheduled": True,\n                "oom_killed": True,\n                "max_restart_count": 3,\n                "state_reasons": "",\n                "last_termination_reasons": (\n                    "OOMKilled"\n                ),\n            },\n            working_set=300_000_000,\n            memory_limit=1_073_741_824,\n            restart_count=3,\n        ),\n        hidden_expected_stop_reason=(\n            InvestigationStopReason.INSUFFICIENT_EVIDENCE\n        ),\n        hidden_required_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n            InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,\n            InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,\n        ],\n        hidden_preferred_first_probes=[\n            InvestigationProbe.KUBERNETES_POD_STATE,\n        ],\n        hidden_missing_capability_keywords=[\n            "histor",\n            "历史",\n            "range",\n            "peak",\n            "time",\n            "日志",\n            "log",\n        ],\n        hidden_max_reasonable_tool_calls=4,\n    ),\n]\n\n\nSMOKE_SCENARIO_KEYS = (\n    "oom_limit_pressure",\n    "crashloop_not_memory",\n    "conflicting_oom_signal",\n)\n\n\ndef scenarios_for_mode(\n    mode: str,\n) -> list[\n    BenchmarkScenario\n]:\n    if mode == "smoke":\n        keys = set(\n            SMOKE_SCENARIO_KEYS\n        )\n\n        return [\n            item\n            for item in SCENARIOS\n            if item.key in keys\n        ]\n\n    if mode == "full":\n        return list(\n            SCENARIOS\n        )\n\n    raise ValueError(\n        "Benchmark mode must be smoke or full"\n    )\n\n\ndef scenario_by_key(\n    key: str,\n) -> BenchmarkScenario:\n    for item in SCENARIOS:\n        if item.key == key:\n            return item\n\n    raise KeyError(\n        key\n    )\n\n\n__all__ = [\n    "SCENARIOS",\n    "SMOKE_SCENARIO_KEYS",\n    "scenario_by_key",\n    "scenarios_for_mode",\n]\n'

LOG_TEST_SOURCE = 'from __future__ import annotations\n\nfrom datetime import UTC, datetime\nfrom types import SimpleNamespace\nfrom urllib.parse import parse_qs, urlparse\n\nimport httpx\nimport pytest\n\nfrom services.agent_runtime.app.investigation.evidence_time import (\n    InvestigationEvidenceTimePolicy,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    InvestigationProbe,\n    InvestigationScope,\n    InvestigationState,\n)\nfrom services.agent_runtime.app.investigation.probes import (\n    InvestigationProbeResponseError,\n    ReadOnlyInvestigationProbeExecutor,\n)\nfrom services.agent_runtime.app.investigation.reasoner import (\n    LLMInvestigationReasoner,\n)\nfrom services.agent_runtime.app.tools.kubernetes.tool import (\n    KubernetesQueryError,\n    KubernetesTool,\n)\nfrom services.agent_runtime.app.tools.manager import (\n    ToolManager,\n)\nfrom services.agent_runtime.app.tools.registry import (\n    ToolRegistry,\n)\n\n\nNOW = datetime(\n    2026,\n    8,\n    10,\n    13,\n    0,\n    tzinfo=UTC,\n)\n\n\ndef scope() -> InvestigationScope:\n    return InvestigationScope(\n        alert_name="PodRestartHigh",\n        alert_message="payment-api is restarting",\n        event_occurred_at=NOW,\n        resource="payment-api",\n        namespace="payment",\n        cluster="benchmark-lab",\n    )\n\n\ndef pod_payload(\n    *,\n    containers=None,\n):\n    if containers is None:\n        containers = [\n            {\n                "name": "payment-api",\n                "ready": False,\n                "restartCount": 9,\n                "state": {\n                    "waiting": {\n                        "reason": "CrashLoopBackOff",\n                    }\n                },\n                "lastState": {\n                    "terminated": {\n                        "reason": "Error",\n                        "finishedAt": (\n                            "2026-08-10T12:59:30Z"\n                        ),\n                    }\n                },\n                "image": "payment-api:v2",\n                "imageID": "sha256:test",\n            }\n        ]\n\n    return {\n        "apiVersion": "v1",\n        "kind": "Pod",\n        "metadata": {\n            "name": "payment-api",\n            "namespace": "payment",\n            "uid": "pod-uid",\n            "resourceVersion": "123",\n        },\n        "spec": {\n            "nodeName": "worker-1",\n        },\n        "status": {\n            "phase": "Running",\n            "conditions": [],\n            "containerStatuses": containers,\n        },\n    }\n\n\nclass FakeToolManager:\n    def __init__(\n        self,\n        result,\n    ):\n        self.result = result\n        self.calls = []\n\n    async def call(\n        self,\n        name,\n        context=None,\n        **kwargs,\n    ):\n        self.calls.append(\n            {\n                "name": name,\n                "context": context,\n                "kwargs": kwargs,\n            }\n        )\n\n        return self.result\n\n\ndef valid_log_result(\n    *,\n    excerpt=(\n        "2026-08-10T12:59:30Z "\n        "panic: invalid configuration\\n"\n        "password=[REDACTED]"\n    ),\n):\n    return {\n        "success": True,\n        "source": "kubernetes",\n        "mode": "read_only",\n        "production_signal": True,\n        "observed_at": NOW.isoformat(),\n        "action": "previous_logs",\n        "resource": "pod",\n        "target": "payment-api",\n        "namespace": "payment",\n        "cluster": "benchmark-lab",\n        "data": {\n            "container_name": "payment-api",\n            "previous": True,\n            "line_count": 2,\n            "truncated": False,\n            "redaction_count": 1,\n            "excerpt": excerpt,\n        },\n    }\n\n\n@pytest.mark.asyncio\nasync def test_previous_logs_probe_has_fixed_platform_owned_call():\n    result = valid_log_result()\n    tools = FakeToolManager(\n        result\n    )\n    context = SimpleNamespace(\n        tools=tools,\n        trace=None,\n    )\n\n    evidence = await (\n        ReadOnlyInvestigationProbeExecutor()\n        .collect(\n            context,\n            scope(),\n            InvestigationProbe.KUBERNETES_PREVIOUS_CONTAINER_LOGS,\n        )\n    )\n\n    assert tools.calls == [\n        {\n            "name": "kubernetes",\n            "context": context,\n            "kwargs": {\n                "action": "previous_logs",\n                "resource": "pod",\n                "target": "payment-api",\n                "namespace": "payment",\n            },\n        }\n    ]\n\n    assert evidence.trusted is True\n    assert evidence.production_signal is True\n    assert evidence.source == "kubernetes"\n    assert (\n        evidence.facts["temporal_basis"]\n        == "previous_container"\n    )\n    assert (\n        evidence.facts["container_name"]\n        == "payment-api"\n    )\n    assert evidence.facts["previous"] is True\n    assert (\n        "panic: invalid configuration"\n        in evidence.facts["log_excerpt"]\n    )\n\n\n@pytest.mark.asyncio\nasync def test_investigation_boundary_redacts_forged_secret_again():\n    secret = "super-secret-value"\n    tools = FakeToolManager(\n        valid_log_result(\n            excerpt=(\n                "panic: startup failure\\n"\n                f"password={secret}\\n"\n                "token=abcdefghijk12345"\n            )\n        )\n    )\n\n    evidence = await (\n        ReadOnlyInvestigationProbeExecutor()\n        .collect(\n            SimpleNamespace(\n                tools=tools,\n                trace=None,\n            ),\n            scope(),\n            InvestigationProbe.KUBERNETES_PREVIOUS_CONTAINER_LOGS,\n        )\n    )\n\n    serialized = str(\n        evidence.model_dump(\n            mode="json"\n        )\n    )\n\n    assert secret not in serialized\n    assert "abcdefghijk12345" not in serialized\n    assert "[REDACTED]" in serialized\n    assert (\n        evidence.facts["redaction_count"]\n        >= 3\n    )\n\n\n@pytest.mark.asyncio\nasync def test_logs_probe_rejects_non_previous_or_oversized_tool_contract():\n    invalid = valid_log_result()\n    invalid["data"] = dict(\n        invalid["data"]\n    )\n    invalid["data"]["previous"] = False\n\n    with pytest.raises(\n        InvestigationProbeResponseError,\n        match="previous-container",\n    ):\n        await (\n            ReadOnlyInvestigationProbeExecutor()\n            .collect(\n                SimpleNamespace(\n                    tools=FakeToolManager(\n                        invalid\n                    ),\n                    trace=None,\n                ),\n                scope(),\n                InvestigationProbe.KUBERNETES_PREVIOUS_CONTAINER_LOGS,\n            )\n        )\n\n    oversized = valid_log_result(\n        excerpt=(\n            "x" * 4001\n        )\n    )\n\n    with pytest.raises(\n        InvestigationProbeResponseError,\n        match="too large",\n    ):\n        await (\n            ReadOnlyInvestigationProbeExecutor()\n            .collect(\n                SimpleNamespace(\n                    tools=FakeToolManager(\n                        oversized\n                    ),\n                    trace=None,\n                ),\n                scope(),\n                InvestigationProbe.KUBERNETES_PREVIOUS_CONTAINER_LOGS,\n            )\n        )\n\n\ndef test_previous_logs_have_distinct_temporal_basis():\n    policy = (\n        InvestigationEvidenceTimePolicy()\n    )\n\n    assert (\n        policy.temporal_basis(\n            scope=scope(),\n            probe=(\n                InvestigationProbe\n                .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n            ),\n        )\n        == "previous_container"\n    )\n\n    assert (\n        policy.query_time(\n            scope=scope(),\n            probe=(\n                InvestigationProbe\n                .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n            ),\n        )\n        is None\n    )\n\n\ndef test_reasoner_exposes_symbolic_log_probe_without_raw_log_parameters():\n    current_scope = scope()\n\n    prompt = (\n        LLMInvestigationReasoner\n        ._build_prompt(\n            scope=current_scope,\n            state=InvestigationState(\n                scope=current_scope\n            ),\n        )\n    )\n\n    assert (\n        "kubernetes_previous_container_logs"\n        in prompt\n    )\n\n    assert "tailLines" not in prompt\n    assert "limitBytes" not in prompt\n    assert "previous=true" not in prompt\n\n\n@pytest.mark.asyncio\nasync def test_kubernetes_previous_logs_are_bounded_redacted_and_fixed():\n    seen = []\n\n    raw_password = (\n        "dont-print-this-password"\n    )\n\n    raw_token = (\n        "abcdefghijk123456789"\n    )\n\n    def handler(\n        request: httpx.Request,\n    ) -> httpx.Response:\n        seen.append(\n            str(\n                request.url\n            )\n        )\n\n        if (\n            request.url.path\n            == (\n                "/api/v1/namespaces/payment/"\n                "pods/payment-api"\n            )\n        ):\n            return httpx.Response(\n                200,\n                json=pod_payload(),\n            )\n\n        if (\n            request.url.path\n            == (\n                "/api/v1/namespaces/payment/"\n                "pods/payment-api/log"\n            )\n        ):\n            return httpx.Response(\n                200,\n                text=(\n                    "2026-08-10T12:59:30Z "\n                    "panic: invalid configuration MAX_CONNECTIONS\\n"\n                    f"password={raw_password}\\n"\n                    f"token={raw_token}\\n"\n                    "Authorization: Bearer abcdefghijklmnop"\n                ),\n                headers={\n                    "content-type": (\n                        "text/plain; charset=utf-8"\n                    )\n                },\n            )\n\n        return httpx.Response(\n            404\n        )\n\n    transport = httpx.MockTransport(\n        handler\n    )\n\n    async with httpx.AsyncClient(\n        transport=transport\n    ) as client:\n        tool = KubernetesTool(\n            api_url="https://kubernetes.test",\n            bearer_token="test-token",\n            cluster_name="benchmark-lab",\n            allow_dry_run_fallback=False,\n            client=client,\n            clock=lambda: NOW,\n        )\n\n        result = await tool.execute(\n            action="previous_logs",\n            resource="pod",\n            target="payment-api",\n            namespace="payment",\n            # These untrusted extras are deliberately ignored.\n            container="attacker-selected",\n            tail_lines=999999,\n        )\n\n    assert len(\n        seen\n    ) == 2\n\n    parsed = urlparse(\n        seen[1]\n    )\n\n    params = parse_qs(\n        parsed.query\n    )\n\n    assert params == {\n        "container": [\n            "payment-api"\n        ],\n        "previous": [\n            "true"\n        ],\n        "tailLines": [\n            "80"\n        ],\n        "limitBytes": [\n            "16384"\n        ],\n        "timestamps": [\n            "true"\n        ],\n    }\n\n    assert (\n        result["source"]\n        == "kubernetes"\n    )\n    assert (\n        result["mode"]\n        == "read_only"\n    )\n    assert (\n        result["production_signal"]\n        is True\n    )\n\n    data = result["data"]\n\n    assert (\n        data["container_name"]\n        == "payment-api"\n    )\n    assert data["previous"] is True\n    assert (\n        "panic: invalid configuration"\n        in data["excerpt"]\n    )\n    assert raw_password not in str(\n        result\n    )\n    assert raw_token not in str(\n        result\n    )\n    assert (\n        "abcdefghijklmnop"\n        not in str(\n            result\n        )\n    )\n    assert "[REDACTED]" in (\n        data["excerpt"]\n    )\n    assert (\n        data["redaction_count"]\n        >= 3\n    )\n\n\n@pytest.mark.asyncio\nasync def test_kubernetes_previous_logs_fail_closed_on_ambiguous_container():\n    containers = [\n        {\n            "name": "app",\n            "restartCount": 3,\n            "lastState": {\n                "terminated": {\n                    "reason": "Error"\n                }\n            },\n        },\n        {\n            "name": "sidecar",\n            "restartCount": 2,\n            "lastState": {\n                "terminated": {\n                    "reason": "Error"\n                }\n            },\n        },\n    ]\n\n    log_calls = 0\n\n    def handler(\n        request: httpx.Request,\n    ) -> httpx.Response:\n        nonlocal log_calls\n\n        if request.url.path.endswith(\n            "/log"\n        ):\n            log_calls += 1\n\n        return httpx.Response(\n            200,\n            json=pod_payload(\n                containers=containers\n            ),\n        )\n\n    transport = httpx.MockTransport(\n        handler\n    )\n\n    async with httpx.AsyncClient(\n        transport=transport\n    ) as client:\n        tool = KubernetesTool(\n            api_url="https://kubernetes.test",\n            bearer_token="test-token",\n            allow_dry_run_fallback=False,\n            client=client,\n            clock=lambda: NOW,\n        )\n\n        with pytest.raises(\n            KubernetesQueryError,\n            match="selection is ambiguous",\n        ):\n            await tool.execute(\n                action="previous_logs",\n                resource="pod",\n                target="payment-api",\n                namespace="payment",\n            )\n\n    assert log_calls == 0\n\n\n@pytest.mark.asyncio\nasync def test_real_tool_manager_path_returns_trusted_redacted_log_evidence():\n    raw_secret = (\n        "another-secret-value"\n    )\n\n    def handler(\n        request: httpx.Request,\n    ) -> httpx.Response:\n        if request.url.path.endswith(\n            "/log"\n        ):\n            return httpx.Response(\n                200,\n                text=(\n                    "panic: invalid configuration\\n"\n                    f"client_secret={raw_secret}"\n                ),\n            )\n\n        return httpx.Response(\n            200,\n            json=pod_payload(),\n        )\n\n    transport = httpx.MockTransport(\n        handler\n    )\n\n    async with httpx.AsyncClient(\n        transport=transport\n    ) as client:\n        registry = ToolRegistry()\n        registry.register(\n            KubernetesTool(\n                api_url="https://kubernetes.test",\n                bearer_token="test-token",\n                cluster_name="benchmark-lab",\n                allow_dry_run_fallback=False,\n                client=client,\n                clock=lambda: NOW,\n            )\n        )\n\n        manager = ToolManager(\n            registry\n        )\n\n        context = SimpleNamespace(\n            tools=manager,\n            trace=None,\n        )\n\n        evidence = await (\n            ReadOnlyInvestigationProbeExecutor()\n            .collect(\n                context,\n                scope(),\n                (\n                    InvestigationProbe\n                    .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n                ),\n            )\n        )\n\n    serialized = str(\n        evidence.model_dump(\n            mode="json"\n        )\n    )\n\n    assert evidence.trusted is True\n    assert raw_secret not in serialized\n    assert (\n        "panic: invalid configuration"\n        in evidence.facts["log_excerpt"]\n    )\n'
BENCHMARK_LOG_TEST_SOURCE = 'from __future__ import annotations\n\nimport asyncio\nfrom datetime import UTC, datetime\n\nimport pytest\n\nfrom services.agent_runtime.app.evaluation.intelligence_benchmark.engine import (\n    BenchmarkProbeExecutor,\n)\nfrom services.agent_runtime.app.evaluation.intelligence_benchmark.scenarios import (\n    scenario_by_key,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    InvestigationProbe,\n    InvestigationStopReason,\n)\n\n\nNOW = datetime(\n    2026,\n    8,\n    10,\n    13,\n    30,\n    tzinfo=UTC,\n)\n\n\ndef test_logs_rca_scenario_is_available_with_hidden_causal_label():\n    scenario = scenario_by_key(\n        "crashloop_previous_log_rca"\n    )\n\n    assert (\n        scenario.hidden_expected_stop_reason\n        == InvestigationStopReason.SUFFICIENT_EVIDENCE\n    )\n\n    assert (\n        InvestigationProbe.KUBERNETES_PREVIOUS_CONTAINER_LOGS\n        in scenario.evidence_by_probe\n    )\n\n    assert (\n        InvestigationProbe.KUBERNETES_PREVIOUS_CONTAINER_LOGS\n        in scenario.hidden_required_probes\n    )\n\n\ndef test_crashloop_without_logs_explicitly_models_log_unavailability():\n    scenario = scenario_by_key(\n        "crashloop_not_memory"\n    )\n\n    assert (\n        scenario.evidence_by_probe[\n            InvestigationProbe.KUBERNETES_PREVIOUS_CONTAINER_LOGS\n        ]\n        == "unavailable"\n    )\n\n\n@pytest.mark.asyncio\nasync def test_benchmark_log_probe_is_kubernetes_evidence():\n    scenario = scenario_by_key(\n        "crashloop_previous_log_rca"\n    )\n\n    executor = BenchmarkProbeExecutor(\n        scenario,\n        observed_at=NOW,\n    )\n\n    evidence = await executor.collect(\n        None,\n        None,\n        InvestigationProbe.KUBERNETES_PREVIOUS_CONTAINER_LOGS,\n    )\n\n    assert evidence.source == "kubernetes"\n    assert evidence.trusted is True\n    assert evidence.production_signal is True\n    assert (\n        evidence.facts["temporal_basis"]\n        == "previous_container"\n    )\n    assert (\n        "panic: invalid configuration"\n        in evidence.facts["log_excerpt"]\n    )\n    assert "real-password" not in str(\n        evidence.model_dump(\n            mode="json"\n        )\n    )\n'


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


def verify_current_hash(
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
                f"{relative} changed after the reviewed snapshot. "
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

    probes_file = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "investigation"
        / "probes.py"
    )

    evidence_time_file = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "investigation"
        / "evidence_time.py"
    )

    kubernetes_tool_file = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "tools"
        / "kubernetes"
        / "tool.py"
    )

    benchmark_engine_file = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "evaluation"
        / "intelligence_benchmark"
        / "engine.py"
    )

    benchmark_scenarios_file = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "evaluation"
        / "intelligence_benchmark"
        / "scenarios.py"
    )

    logs_test_file = (
        root
        / "services"
        / "agent_runtime"
        / "tests"
        / "test_investigation_logs.py"
    )

    benchmark_test_file = (
        root
        / "services"
        / "agent_runtime"
        / "tests"
        / "test_investigation_logs_benchmark.py"
    )

    source_targets = {
        models_file: MODELS_SOURCE,
        probes_file: PROBES_SOURCE,
        evidence_time_file: EVIDENCE_TIME_SOURCE,
        kubernetes_tool_file: KUBERNETES_TOOL_SOURCE,
        benchmark_engine_file: BENCHMARK_ENGINE_SOURCE,
        benchmark_scenarios_file: BENCHMARK_SCENARIOS_SOURCE,
    }

    new_files = {
        logs_test_file: LOG_TEST_SOURCE,
        benchmark_test_file: BENCHMARK_LOG_TEST_SOURCE,
    }

    all_targets = [
        *source_targets.keys(),
        *new_files.keys(),
    ]

    preexisting = {
        path: path.exists()
        for path in all_targets
    }

    backups = []

    report = [
        "Logs Investigation v2",
        f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
        "",
        "Capability:",
        "- add symbolic probe kubernetes_previous_container_logs",
        "- model still selects only an InvestigationProbe enum",
        "- model cannot select container, tailLines, limitBytes, URL or credentials",
        "- KubernetesTool resolves exactly one safe previous-log container",
        "- ambiguous multi-container selection fails closed",
        "- previous log request is fixed to previous=true, tailLines=80, limitBytes=16384, timestamps=true",
        "- raw log stream is bounded before retention",
        "- KubernetesTool redacts common credentials before ToolManager tracing",
        "- Investigation boundary performs a second redaction pass",
        "- InvestigationState retains only a bounded redacted excerpt",
        "- previous-container logs have an explicit temporal_basis",
        "",
        "Benchmark:",
        "- add crashloop_previous_log_rca hidden-label scenario",
        "- crashloop_not_memory explicitly models previous logs as unavailable",
        "- existing smoke scenario set remains unchanged for historical comparison",
        "",
        "Installer v2 fix:",
        "- correct KubernetesTool reviewed-snapshot SHA256 metadata",
        "- expected hash now matches the uploaded current-code snapshot",
        "- Logs capability implementation itself is unchanged from v1",
        "",
        "Safety:",
        "- no Action/Approval/Verification changes",
        "- no write-capable Kubernetes verb is added",
        "- installer sends no LLM/Kubernetes/Prometheus request",
        "- Kubernetes tests use httpx.MockTransport only",
    ]

    try:
        section(
            report,
            "CURRENT SOURCE HASH PREFLIGHT",
        )

        for relative in EXPECTED_HASHES:
            verify_current_hash(
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

        for path in all_targets:
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
            source_targets.items()
        ):
            write_text(
                path,
                source,
            )

        for path, source in (
            new_files.items()
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
                    in all_targets
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
            name="Logs Investigation focused regression suite",
            command=[
                "uv",
                "run",
                "pytest",
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
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_epistemic_guard.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_causal_sufficiency.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_production_tool_contract.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_kubernetes_tool.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_tools.py"
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
                "Logs Investigation focused regression suite failed"
            )

        compatibility = run_command(
            root=root,
            name="Historical/evaluation compatibility tests",
            command=[
                "uv",
                "run",
                "pytest",
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
                "Historical/evaluation compatibility tests failed"
            )

        authority = run_command(
            root=root,
            name="Logs read-only authority boundary",
            command=[
                "uv",
                "run",
                "python",
                "-c",
                (
                    "from pathlib import Path; "
                    "p=Path(r'services/agent_runtime/app/investigation/probes.py')"
                    ".read_text(encoding='utf-8'); "
                    "k=Path(r'services/agent_runtime/app/tools/kubernetes/tool.py')"
                    ".read_text(encoding='utf-8'); "
                    "assert 'kubernetes_previous_container_logs' in "
                    "Path(r'services/agent_runtime/app/investigation/models.py')"
                    ".read_text(encoding='utf-8'); "
                    "assert 'action=\"previous_logs\"' in p; "
                    "assert 'container=' not in p[p.index('KUBERNETES_PREVIOUS_CONTAINER_LOGS'):"
                    "p.index('query = self._prometheus_query')]; "
                    "bad=[x for x in ['.post(','.patch(','.delete(','.put('] if x in k]; "
                    "print('write_http_methods='+str(bad)); "
                    "print('fixed_tail='+str('_LOG_TAIL_LINES = 80' in k)); "
                    "print('fixed_bytes='+str('_LOG_LIMIT_BYTES = 16384' in k)); "
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
                "Logs read-only authority boundary failed"
            )

        benchmark_preflight = run_command(
            root=root,
            name="Logs benchmark scenario preflight",
            command=[
                "uv",
                "run",
                "python",
                "-c",
                (
                    "from services.agent_runtime.app.evaluation."
                    "intelligence_benchmark.scenarios import "
                    "scenario_by_key,scenarios_for_mode; "
                    "from services.agent_runtime.app.investigation.models "
                    "import InvestigationProbe; "
                    "s=scenario_by_key('crashloop_previous_log_rca'); "
                    "print('full_scenarios='+str(len(scenarios_for_mode('full')))); "
                    "print('smoke_scenarios='+str(len(scenarios_for_mode('smoke')))); "
                    "print('log_probe=' + str("
                    "InvestigationProbe.KUBERNETES_PREVIOUS_CONTAINER_LOGS "
                    "in s.evidence_by_probe))"
                ),
            ],
        )

        add_command(
            report,
            benchmark_preflight,
        )

        if benchmark_preflight.returncode != 0:
            raise RuntimeError(
                "Logs benchmark scenario preflight failed"
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
                    in all_targets
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
                "Logs Investigation v1 is installed.",
                "",
                "Agent autonomy preserved:",
                "- Qwen sees one new symbolic InvestigationProbe only",
                "- Qwen still owns whether/when to choose that probe",
                "- platform owns container resolution and every raw Kubernetes log parameter",
                "",
                "Security boundary:",
                "- previous-container only",
                "- exactly one safely resolvable restarted container",
                "- fixed 80-line / 16KiB Kubernetes request",
                "- bounded 4000-char sanitized Tool result",
                "- bounded 1800-char redacted Investigation evidence",
                "- common credential patterns are redacted twice",
                "",
                "Real Qwen acceptance command after this installer passes:",
                (
                    "uv run python scripts/dev/"
                    "run_investigation_intelligence_benchmark_v1.py "
                    "--provider bailian "
                    "--scenario crashloop_previous_log_rca"
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
            "LOGS INVESTIGATION V2 PASSED"
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

        for path in all_targets:
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
                    "Logs Investigation v2 FAILED",
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
            "LOGS INVESTIGATION V2 FAILED"
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
