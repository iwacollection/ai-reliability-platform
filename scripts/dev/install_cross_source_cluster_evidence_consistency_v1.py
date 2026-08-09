from __future__ import annotations

import hashlib
import shutil
import subprocess
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


VERSION = "cross-source-cluster-evidence-consistency-v1"

AFTER_NAME = (
    "cross_source_cluster_evidence_consistency_v1_after.txt"
)

ERROR_NAME = (
    "cross_source_cluster_evidence_consistency_v1_error.txt"
)

EXPECTED_RAW_HASHES = {'services/agent_runtime/app/investigation/models.py': 'b1a3b14a5092d954a797d0c930a285afda60dc7b2d7f40a9ebc40cdc63bf138f', 'services/agent_runtime/app/investigation/probes.py': 'cca5c32c606d01a70cb0e2004432455cef093eaaea610ce29616eb82a8543113', 'services/agent_runtime/app/investigation/coordinator.py': '12f89f270fb8cded06c736dae6a22bc6bb5219387b9abab1ac9baf407a119033', 'services/agent_runtime/app/verification/collector.py': 'a8d68444a67ca90c38088c0db8009467ecf1d547a2e6a9404b1f6031904eb124', 'services/agent_runtime/app/verification/profiles.py': 'bc4e0c5e25e7a7fd67e31637af3fc30f36bfb2d4d0e62a2a55fa840b62a702db'}

MODELS_SOURCE = 'from datetime import UTC, datetime\nfrom enum import Enum\nfrom typing import Annotated, Literal\nfrom uuid import uuid4\n\nfrom pydantic import (\n    BaseModel,\n    ConfigDict,\n    Field,\n    StringConstraints,\n    model_validator,\n)\n\n\nShortText = Annotated[\n    str,\n    StringConstraints(\n        strip_whitespace=True,\n        min_length=1,\n        max_length=256,\n    ),\n]\n\nLongText = Annotated[\n    str,\n    StringConstraints(\n        strip_whitespace=True,\n        min_length=1,\n        max_length=2000,\n    ),\n]\n\nEvidenceScalar = bool | int | float | str | None\n\n\nclass InvestigationProbe(str, Enum):\n    """\n    Closed set of read-only probes selectable by a reasoner.\n\n    The reasoner selects only a symbolic probe. It never supplies a tool\n    name, Kubernetes verb, resource target, URL, credential or PromQL.\n    """\n\n    KUBERNETES_POD_STATE = "kubernetes_pod_state"\n    KUBERNETES_PREVIOUS_CONTAINER_LOGS = (\n        "kubernetes_previous_container_logs"\n    )\n    KUBERNETES_WORKLOAD_CHANGE = (\n        "kubernetes_workload_change"\n    )\n    KUBERNETES_CONFIG_CHANGE = (\n        "kubernetes_config_change"\n    )\n    PROMETHEUS_MEMORY_WORKING_SET = (\n        "prometheus_memory_working_set"\n    )\n    PROMETHEUS_MEMORY_LIMIT = "prometheus_memory_limit"\n    PROMETHEUS_RESTART_COUNT = "prometheus_restart_count"\n\n\ndef default_investigation_probes(\n) -> list[InvestigationProbe]:\n    """\n    Stable baseline probe set.\n\n    New capability enums are not automatically exposed to the reasoner.\n    The Coordinator may add a capability-specific probe only when the active\n    read-only ProbeExecutor proves that capability is available.\n    """\n\n    return [\n        InvestigationProbe.KUBERNETES_POD_STATE,\n        InvestigationProbe.KUBERNETES_PREVIOUS_CONTAINER_LOGS,\n        InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,\n        InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,\n        InvestigationProbe.PROMETHEUS_RESTART_COUNT,\n    ]\n\n\nclass InvestigationStatus(str, Enum):\n    PENDING = "pending"\n    RUNNING = "running"\n    CONCLUDED = "concluded"\n    EXHAUSTED = "exhausted"\n    FAILED = "failed"\n\n\nclass InvestigationStopReason(str, Enum):\n    SUFFICIENT_EVIDENCE = "sufficient_evidence"\n    INSUFFICIENT_EVIDENCE = "insufficient_evidence"\n    MAX_ITERATIONS = "max_iterations"\n    MAX_TOOL_CALLS = "max_tool_calls"\n    TIMEOUT = "timeout"\n    DUPLICATE_PROBE = "duplicate_probe"\n    NO_SAFE_PROBE = "no_safe_probe"\n    REASONER_ERROR = "reasoner_error"\n    INVALID_SCOPE = "invalid_scope"\n\n\nclass InvestigationLimits(BaseModel):\n    """\n    Hard execution limits for one read-only investigation.\n    """\n\n    model_config = ConfigDict(\n        frozen=True,\n        extra="forbid",\n    )\n\n    max_iterations: int = Field(\n        default=6,\n        ge=1,\n        le=10,\n    )\n    max_tool_calls: int = Field(\n        default=10,\n        ge=1,\n        le=20,\n    )\n    timeout_seconds: float = Field(\n        default=30.0,\n        ge=1.0,\n        le=60.0,\n    )\n\n\nclass InvestigationScope(BaseModel):\n    """\n    Trusted scope derived from StandardEvent, never from LLM output.\n    """\n\n    model_config = ConfigDict(\n        frozen=True,\n        extra="forbid",\n    )\n\n    alert_name: ShortText\n    alert_message: str = Field(\n        default="",\n        max_length=2000,\n    )\n    event_occurred_at: datetime | None = None\n    resource: ShortText\n    namespace: ShortText = "default"\n    cluster: ShortText | None = None\n\n\nclass EvidenceItem(BaseModel):\n    """\n    Bounded evidence retained by the Shadow loop.\n\n    Raw Kubernetes or Prometheus payloads are not stored here. facts accepts\n    scalar values only. Kubernetes log evidence is retained only as a bounded,\n    redacted excerpt, which prevents nested responses or raw log streams from\n    becoming an unbounded or sensitive reasoning transcript.\n    """\n\n    model_config = ConfigDict(\n        frozen=True,\n        extra="forbid",\n    )\n\n    evidence_id: str = Field(\n        default_factory=lambda: str(uuid4()),\n        min_length=1,\n        max_length=64,\n    )\n    probe: InvestigationProbe\n    source: ShortText\n    success: bool\n    trusted: bool\n    production_signal: bool\n    reliability: float = Field(\n        ge=0.0,\n        le=1.0,\n    )\n    observed_at: datetime\n    cluster: ShortText | None = None\n    cluster_verified: bool = False\n    facts: dict[str, EvidenceScalar] = Field(\n        default_factory=dict,\n        max_length=32,\n    )\n    error_code: ShortText | None = None\n\n    @model_validator(mode="after")\n    def validate_trust_boundary(self):\n        if self.trusted and (\n            not self.success\n            or not self.production_signal\n        ):\n            raise ValueError(\n                "trusted evidence requires a successful production signal"\n            )\n\n        if not self.success and self.error_code is None:\n            raise ValueError(\n                "failed evidence requires an error code"\n            )\n\n        if self.cluster_verified:\n            if self.cluster is None:\n                raise ValueError(\n                    "cluster-verified evidence requires a cluster identity"\n                )\n\n            if not (\n                self.success\n                and self.trusted\n                and self.production_signal\n            ):\n                raise ValueError(\n                    "cluster-verified evidence requires trusted production evidence"\n                )\n\n        return self\n\n\nclass IncidentHypothesis(BaseModel):\n    """\n    One current incident explanation maintained by the reasoner.\n    """\n\n    model_config = ConfigDict(\n        extra="forbid",\n    )\n\n    hypothesis_id: ShortText\n    cause: LongText\n    confidence: float = Field(\n        ge=0.0,\n        le=1.0,\n    )\n    supporting_evidence_ids: list[ShortText] = Field(\n        default_factory=list,\n        max_length=32,\n    )\n    conflicting_evidence_ids: list[ShortText] = Field(\n        default_factory=list,\n        max_length=32,\n    )\n    missing_evidence: list[ShortText] = Field(\n        default_factory=list,\n        max_length=16,\n    )\n    optional_evidence: list[ShortText] = Field(\n        default_factory=list,\n        max_length=16,\n    )\n\n\nclass InvestigationConclusion(BaseModel):\n    """\n    Structured diagnosis output. It contains no remediation authorization.\n    """\n\n    model_config = ConfigDict(\n        extra="forbid",\n    )\n\n    root_cause: LongText\n    confidence: float = Field(\n        ge=0.0,\n        le=1.0,\n    )\n    evidence_ids: list[ShortText] = Field(\n        min_length=1,\n        max_length=32,\n    )\n    remaining_uncertainties: list[ShortText] = Field(\n        default_factory=list,\n        max_length=16,\n    )\n\n\nclass InvestigationDecision(BaseModel):\n    """\n    One bounded reasoner decision.\n\n    A non-terminal decision must select exactly one symbolic read-only probe.\n    A terminal decision cannot select a probe. Sufficient-evidence stops must\n    include a structured conclusion.\n    """\n\n    model_config = ConfigDict(\n        extra="forbid",\n    )\n\n    hypotheses: list[IncidentHypothesis] = Field(\n        min_length=1,\n        max_length=8,\n    )\n    rationale_summary: LongText\n    stop: bool = False\n    stop_reason: InvestigationStopReason | None = None\n    next_probe: InvestigationProbe | None = None\n    conclusion: InvestigationConclusion | None = None\n\n    @model_validator(mode="after")\n    def validate_decision_shape(self):\n        if self.stop:\n            if self.next_probe is not None:\n                raise ValueError(\n                    "terminal decision cannot select a probe"\n                )\n            if self.stop_reason is None:\n                raise ValueError(\n                    "terminal decision requires a stop reason"\n                )\n            if self.stop_reason not in {\n                InvestigationStopReason.SUFFICIENT_EVIDENCE,\n                InvestigationStopReason.INSUFFICIENT_EVIDENCE,\n                InvestigationStopReason.NO_SAFE_PROBE,\n            }:\n                raise ValueError(\n                    "reasoner cannot select an internal stop reason"\n                )\n            if (\n                self.stop_reason\n                == InvestigationStopReason.SUFFICIENT_EVIDENCE\n                and self.conclusion is None\n            ):\n                raise ValueError(\n                    "sufficient evidence requires a conclusion"\n                )\n            if (\n                self.stop_reason\n                != InvestigationStopReason.SUFFICIENT_EVIDENCE\n                and self.conclusion is not None\n            ):\n                raise ValueError(\n                    "insufficient evidence cannot include a conclusion"\n                )\n        else:\n            if self.next_probe is None:\n                raise ValueError(\n                    "continuing decision requires a probe"\n                )\n            if self.stop_reason is not None:\n                raise ValueError(\n                    "continuing decision cannot have a stop reason"\n                )\n            if self.conclusion is not None:\n                raise ValueError(\n                    "continuing decision cannot have a conclusion"\n                )\n\n        return self\n\n\nclass InvestigationState(BaseModel):\n    """\n    Complete bounded state of one Shadow investigation.\n    """\n\n    model_config = ConfigDict(\n        extra="forbid",\n    )\n\n    investigation_id: str = Field(\n        default_factory=lambda: str(uuid4()),\n        min_length=1,\n        max_length=64,\n    )\n    shadow_mode: Literal[True] = True\n    read_only: Literal[True] = True\n    status: InvestigationStatus = InvestigationStatus.PENDING\n    scope: InvestigationScope\n    limits: InvestigationLimits = Field(\n        default_factory=InvestigationLimits\n    )\n    started_at: datetime = Field(\n        default_factory=lambda: datetime.now(UTC)\n    )\n    updated_at: datetime = Field(\n        default_factory=lambda: datetime.now(UTC)\n    )\n    iteration_count: int = Field(\n        default=0,\n        ge=0,\n        le=10,\n    )\n    tool_call_count: int = Field(\n        default=0,\n        ge=0,\n        le=20,\n    )\n    hypotheses: list[IncidentHypothesis] = Field(\n        default_factory=list,\n        max_length=8,\n    )\n    evidence: list[EvidenceItem] = Field(\n        default_factory=list,\n        max_length=20,\n    )\n    available_probes: list[InvestigationProbe] = Field(\n        default_factory=default_investigation_probes,\n        min_length=1,\n        max_length=20,\n    )\n    attempted_probes: list[InvestigationProbe] = Field(\n        default_factory=list,\n        max_length=20,\n    )\n    decision_summaries: list[LongText] = Field(\n        default_factory=list,\n        max_length=10,\n    )\n    stop_reason: InvestigationStopReason | None = None\n    failure_code: ShortText | None = None\n    epistemic_guard_code: ShortText | None = None\n    conclusion: InvestigationConclusion | None = None\n'
PROBES_SOURCE = 'import re\nfrom collections.abc import Mapping\nfrom datetime import UTC, datetime\nfrom math import isfinite\nfrom typing import Any\n\nfrom services.agent_runtime.app.investigation.evidence_time import (\n    InvestigationEvidenceTimeError,\n    InvestigationEvidenceTimePolicy,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    EvidenceItem,\n    InvestigationProbe,\n    InvestigationScope,\n    default_investigation_probes,\n)\n\n\nclass InvestigationProbeError(RuntimeError):\n    """\n    Base error for the bounded read-only probe adapter.\n    """\n\n\nclass InvestigationToolUnavailableError(\n    InvestigationProbeError\n):\n    """\n    Runtime ToolManager is unavailable.\n    """\n\n\nclass InvestigationProbeResponseError(\n    InvestigationProbeError\n):\n    """\n    A read-only tool returned evidence that cannot cross the\n    Investigation trust boundary.\n    """\n\n\nclass ReadOnlyInvestigationProbeExecutor:\n    """\n    Translate symbolic Investigation probes into exact read-only tool calls.\n\n    The reasoner selects only an InvestigationProbe enum value.\n\n    This adapter owns:\n\n    - fixed Kubernetes read-only actions;\n    - fixed bounded previous-container log collection;\n    - fixed Prometheus query templates;\n    - provider/source validation;\n    - read-only mode validation;\n    - production-signal validation;\n    - observed-at validation;\n    - bounded evidence normalization.\n\n    The reasoner cannot provide Kubernetes verbs, resource kinds, PromQL,\n    URLs, credentials or raw tool arguments.\n    """\n\n    _TRUSTED_MODE = "read_only"\n    _MAX_LOG_TOOL_CHARS = 4000\n    _MAX_LOG_EVIDENCE_CHARS = 1800\n    _MAX_LOG_LINES = 80\n\n    def __init__(\n        self,\n        time_policy: (\n            InvestigationEvidenceTimePolicy\n            | None\n        ) = None,\n    ) -> None:\n        self.time_policy = (\n            time_policy\n            if time_policy is not None\n            else InvestigationEvidenceTimePolicy()\n        )\n\n    @staticmethod\n    def available_probes(\n        context,\n    ) -> list[InvestigationProbe]:\n        probes = default_investigation_probes()\n\n        tools = getattr(\n            context,\n            "tools",\n            None,\n        )\n\n        registry = getattr(\n            tools,\n            "registry",\n            None,\n        )\n\n        getter = getattr(\n            registry,\n            "get",\n            None,\n        )\n\n        if not callable(\n            getter\n        ):\n            return probes\n\n        try:\n            change_tool = getter(\n                "kubernetes_change"\n            )\n        except KeyError:\n            return probes\n\n        if (\n            getattr(\n                change_tool,\n                "is_available",\n                True,\n            )\n            is not True\n        ):\n            return probes\n\n        probes.append(\n            InvestigationProbe.KUBERNETES_WORKLOAD_CHANGE\n        )\n\n        probes.append(\n            InvestigationProbe.KUBERNETES_CONFIG_CHANGE\n        )\n\n        return probes\n\n    async def collect(\n        self,\n        context,\n        scope: InvestigationScope,\n        probe: InvestigationProbe,\n    ) -> EvidenceItem:\n        tools = getattr(\n            context,\n            "tools",\n            None,\n        )\n\n        if tools is None:\n            raise InvestigationToolUnavailableError(\n                "Runtime tools are unavailable"\n            )\n\n        if (\n            probe\n            == InvestigationProbe.KUBERNETES_POD_STATE\n        ):\n            result = await tools.call(\n                "kubernetes",\n                context=context,\n                action="describe",\n                resource="pod",\n                target=scope.resource,\n                namespace=scope.namespace,\n                cluster=scope.cluster,\n            )\n\n            return self._normalize_kubernetes(\n                scope=scope,\n                probe=probe,\n                result=result,\n            )\n\n        if (\n            probe\n            == InvestigationProbe.KUBERNETES_PREVIOUS_CONTAINER_LOGS\n        ):\n            result = await tools.call(\n                "kubernetes",\n                context=context,\n                action="previous_logs",\n                resource="pod",\n                target=scope.resource,\n                namespace=scope.namespace,\n                cluster=scope.cluster,\n            )\n\n            return self._normalize_kubernetes_logs(\n                scope=scope,\n                probe=probe,\n                result=result,\n            )\n\n        if (\n            probe\n            == InvestigationProbe.KUBERNETES_WORKLOAD_CHANGE\n        ):\n            result = await tools.call(\n                "kubernetes_change",\n                context=context,\n                target=scope.resource,\n                namespace=scope.namespace,\n                cluster=scope.cluster,\n                incident_time=(\n                    scope.event_occurred_at.isoformat()\n                    if scope.event_occurred_at\n                    is not None\n                    else None\n                ),\n                view="workload",\n            )\n\n            return self._normalize_kubernetes_change(\n                scope=scope,\n                probe=probe,\n                result=result,\n            )\n\n        if (\n            probe\n            == InvestigationProbe.KUBERNETES_CONFIG_CHANGE\n        ):\n            result = await tools.call(\n                "kubernetes_change",\n                context=context,\n                target=scope.resource,\n                namespace=scope.namespace,\n                cluster=scope.cluster,\n                incident_time=(\n                    scope.event_occurred_at.isoformat()\n                    if scope.event_occurred_at\n                    is not None\n                    else None\n                ),\n                view="config",\n            )\n\n            return self._normalize_kubernetes_config_change(\n                scope=scope,\n                probe=probe,\n                result=result,\n            )\n\n        query = self._prometheus_query(\n            scope=scope,\n            probe=probe,\n        )\n\n        query_time = self.time_policy.query_time(\n            scope=scope,\n            probe=probe,\n        )\n\n        call_arguments = {\n            "query": query,\n        }\n\n        if scope.cluster is not None:\n            call_arguments[\n                "cluster"\n            ] = scope.cluster\n\n        if query_time is not None:\n            call_arguments["time"] = (\n                query_time\n            )\n\n        result = await tools.call(\n            "prometheus",\n            context=context,\n            **call_arguments,\n        )\n\n        return self._normalize_prometheus(\n            scope=scope,\n            probe=probe,\n            result=result,\n        )\n\n    @classmethod\n    def _prometheus_query(\n        cls,\n        scope: InvestigationScope,\n        probe: InvestigationProbe,\n    ) -> str:\n        labels = [\n            (\n                \'pod="\'\n                f\'{cls._escape_label(scope.resource)}\'\n                \'"\'\n            ),\n            (\n                \'namespace="\'\n                f\'{cls._escape_label(scope.namespace)}\'\n                \'"\'\n            ),\n        ]\n\n        if scope.cluster:\n            labels.append(\n                \'cluster="\'\n                f\'{cls._escape_label(scope.cluster)}\'\n                \'"\'\n            )\n\n        selector = ",".join(\n            labels\n        )\n\n        if (\n            probe\n            == InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET\n        ):\n            return (\n                "sum(container_memory_working_set_bytes{"\n                f\'{selector},container!="POD",container!="",image!=""\'\n                "})"\n            )\n\n        if (\n            probe\n            == InvestigationProbe.PROMETHEUS_MEMORY_LIMIT\n        ):\n            return (\n                "sum(kube_pod_container_resource_limits{"\n                f\'{selector},resource="memory",unit="byte"\'\n                "})"\n            )\n\n        if (\n            probe\n            == InvestigationProbe.PROMETHEUS_RESTART_COUNT\n        ):\n            return (\n                "sum(kube_pod_container_status_restarts_total{"\n                f"{selector}"\n                "})"\n            )\n\n        raise InvestigationProbeError(\n            "Unsupported investigation probe"\n        )\n\n    def _normalize_kubernetes(\n        self,\n        scope: InvestigationScope,\n        probe: InvestigationProbe,\n        result: Any,\n    ) -> EvidenceItem:\n        data, observed_at, evidence_cluster, cluster_verified = (\n            self._validate_tool_evidence(\n                result=result,\n                expected_source="kubernetes",\n                expected_cluster=scope.cluster,\n            )\n        )\n\n        if "phase" not in data:\n            raise InvestigationProbeResponseError(\n                "Kubernetes evidence phase is missing"\n            )\n\n        containers = data.get(\n            "containers"\n        )\n\n        if not isinstance(\n            containers,\n            list,\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes evidence containers are invalid"\n            )\n\n        restart_counts: list[int] = []\n        state_reasons: set[str] = set()\n        termination_reasons: set[str] = set()\n\n        for container in containers[:32]:\n            if not isinstance(\n                container,\n                Mapping,\n            ):\n                continue\n\n            restart_count = container.get(\n                "restart_count"\n            )\n\n            if isinstance(\n                restart_count,\n                int,\n            ):\n                restart_counts.append(\n                    restart_count\n                )\n\n            state_reason = container.get(\n                "state_reason"\n            )\n\n            if (\n                isinstance(\n                    state_reason,\n                    str,\n                )\n                and state_reason\n            ):\n                state_reasons.add(\n                    state_reason[:128]\n                )\n\n            termination_reason = container.get(\n                "last_termination_reason"\n            )\n\n            if (\n                isinstance(\n                    termination_reason,\n                    str,\n                )\n                and termination_reason\n            ):\n                termination_reasons.add(\n                    termination_reason[:128]\n                )\n\n        facts = {\n            "temporal_basis": (\n                self.time_policy.temporal_basis(\n                    scope=scope,\n                    probe=probe,\n                )\n            ),\n            "phase": cls_scalar(\n                data.get("phase")\n            ),\n            "ready": cls_scalar(\n                data.get("ready")\n            ),\n            "scheduled": cls_scalar(\n                data.get("scheduled")\n            ),\n            "oom_killed": cls_scalar(\n                data.get("oom_killed")\n            ),\n            "max_restart_count": (\n                max(restart_counts)\n                if restart_counts\n                else None\n            ),\n            "state_reasons": (\n                ",".join(\n                    sorted(\n                        state_reasons\n                    )\n                )\n                if state_reasons\n                else None\n            ),\n            "last_termination_reasons": (\n                ",".join(\n                    sorted(\n                        termination_reasons\n                    )\n                )\n                if termination_reasons\n                else None\n            ),\n        }\n\n        return EvidenceItem(\n            probe=probe,\n            source="kubernetes",\n            success=True,\n            trusted=True,\n            production_signal=True,\n            reliability=1.0,\n            observed_at=observed_at,\n            cluster=evidence_cluster,\n            cluster_verified=cluster_verified,\n            facts=facts,\n        )\n\n    def _normalize_kubernetes_logs(\n        self,\n        scope: InvestigationScope,\n        probe: InvestigationProbe,\n        result: Any,\n    ) -> EvidenceItem:\n        data, observed_at, evidence_cluster, cluster_verified = (\n            self._validate_tool_evidence(\n                result=result,\n                expected_source="kubernetes",\n                expected_cluster=scope.cluster,\n            )\n        )\n\n        if (\n            data.get(\n                "previous"\n            )\n            is not True\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes log evidence is not previous-container output"\n            )\n\n        container_value = data.get(\n            "container_name"\n        )\n\n        if not isinstance(\n            container_value,\n            str,\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes log evidence container is invalid"\n            )\n\n        container_name = (\n            container_value\n            .strip()\n        )\n\n        if (\n            not container_name\n            or len(\n                container_name\n            )\n            > 128\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes log evidence container is invalid"\n            )\n\n        line_count = data.get(\n            "line_count"\n        )\n\n        if (\n            not isinstance(\n                line_count,\n                int,\n            )\n            or isinstance(\n                line_count,\n                bool,\n            )\n            or line_count < 0\n            or line_count > self._MAX_LOG_LINES\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes log evidence line count is invalid"\n            )\n\n        truncated = data.get(\n            "truncated"\n        )\n\n        if not isinstance(\n            truncated,\n            bool,\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes log evidence truncation flag is invalid"\n            )\n\n        redaction_count = data.get(\n            "redaction_count"\n        )\n\n        if (\n            not isinstance(\n                redaction_count,\n                int,\n            )\n            or isinstance(\n                redaction_count,\n                bool,\n            )\n            or redaction_count < 0\n            or redaction_count > 10000\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes log evidence redaction count is invalid"\n            )\n\n        excerpt_value = data.get(\n            "excerpt"\n        )\n\n        if not isinstance(\n            excerpt_value,\n            str,\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes log evidence excerpt is invalid"\n            )\n\n        if len(\n            excerpt_value\n        ) > self._MAX_LOG_TOOL_CHARS:\n            raise InvestigationProbeResponseError(\n                "Kubernetes log evidence excerpt is too large"\n            )\n\n        excerpt, local_redactions = (\n            redact_log_excerpt(\n                excerpt_value\n            )\n        )\n\n        redaction_count = (\n            redaction_count\n            + local_redactions\n        )\n\n        evidence_truncated = (\n            len(\n                excerpt\n            )\n            > self._MAX_LOG_EVIDENCE_CHARS\n        )\n\n        if evidence_truncated:\n            excerpt = excerpt[\n                -self._MAX_LOG_EVIDENCE_CHARS:\n            ]\n\n        facts = {\n            "temporal_basis": (\n                self.time_policy.temporal_basis(\n                    scope=scope,\n                    probe=probe,\n                )\n            ),\n            "container_name": container_name,\n            "previous": True,\n            "log_line_count": line_count,\n            "tool_truncated": truncated,\n            "evidence_truncated": (\n                evidence_truncated\n            ),\n            "redaction_count": (\n                redaction_count\n            ),\n            "log_excerpt": (\n                excerpt\n                if excerpt\n                else None\n            ),\n        }\n\n        return EvidenceItem(\n            probe=probe,\n            source="kubernetes",\n            success=True,\n            trusted=True,\n            production_signal=True,\n            reliability=1.0,\n            observed_at=observed_at,\n            cluster=evidence_cluster,\n            cluster_verified=cluster_verified,\n            facts=facts,\n        )\n\n    def _normalize_kubernetes_config_change(\n        self,\n        scope: InvestigationScope,\n        probe: InvestigationProbe,\n        result: Any,\n    ) -> EvidenceItem:\n        data, observed_at, evidence_cluster, cluster_verified = (\n            self._validate_tool_evidence(\n                result=result,\n                expected_source="kubernetes_change",\n                expected_cluster=scope.cluster,\n            )\n        )\n\n        if (\n            data.get(\n                "owner_chain_verified"\n            )\n            is not True\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes config change owner chain is untrusted"\n            )\n\n        if (\n            data.get(\n                "workload_kind"\n            )\n            != "Deployment"\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes config change workload kind is unsupported"\n            )\n\n        if (\n            data.get(\n                "secret_content_queried"\n            )\n            is not False\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes config change must not query Secret content"\n            )\n\n        if (\n            data.get(\n                "configmap_content_exposed"\n            )\n            is not False\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes config change must not expose ConfigMap content"\n            )\n\n        metadata_status = data.get(\n            "current_configmap_metadata_status"\n        )\n\n        if metadata_status not in {\n            "complete",\n            "partial",\n            "unavailable",\n            "not_applicable",\n        }:\n            raise InvestigationProbeResponseError(\n                "Kubernetes config metadata status is invalid"\n            )\n\n        facts = {\n            "temporal_basis": (\n                "workload_template_config_change"\n            ),\n            "owner_chain_verified": True,\n            "deployment_name": bounded_change_text(\n                data.get(\n                    "deployment_name"\n                ),\n                required=True,\n            ),\n            "revision_before": bounded_change_int(\n                data.get(\n                    "revision_before"\n                )\n            ),\n            "revision_after": bounded_change_int(\n                data.get(\n                    "revision_after"\n                )\n            ),\n            "configmap_refs_before": bounded_change_text(\n                data.get(\n                    "configmap_refs_before"\n                ),\n                required=False,\n                max_length=1024,\n            ),\n            "configmap_refs_after": bounded_change_text(\n                data.get(\n                    "configmap_refs_after"\n                ),\n                required=False,\n                max_length=1024,\n            ),\n            "configmap_refs_changed": bounded_change_bool(\n                data.get(\n                    "configmap_refs_changed"\n                )\n            ),\n            "configmap_refs_added": bounded_change_text(\n                data.get(\n                    "configmap_refs_added"\n                ),\n                required=False,\n                max_length=1024,\n            ),\n            "configmap_refs_removed": bounded_change_text(\n                data.get(\n                    "configmap_refs_removed"\n                ),\n                required=False,\n                max_length=1024,\n            ),\n            "secret_refs_before": bounded_change_text(\n                data.get(\n                    "secret_refs_before"\n                ),\n                required=False,\n                max_length=1024,\n            ),\n            "secret_refs_after": bounded_change_text(\n                data.get(\n                    "secret_refs_after"\n                ),\n                required=False,\n                max_length=1024,\n            ),\n            "secret_refs_changed": bounded_change_bool(\n                data.get(\n                    "secret_refs_changed"\n                )\n            ),\n            "secret_refs_added": bounded_change_text(\n                data.get(\n                    "secret_refs_added"\n                ),\n                required=False,\n                max_length=1024,\n            ),\n            "secret_refs_removed": bounded_change_text(\n                data.get(\n                    "secret_refs_removed"\n                ),\n                required=False,\n                max_length=1024,\n            ),\n            "config_annotation_keys_before": bounded_change_text(\n                data.get(\n                    "config_annotation_keys_before"\n                ),\n                required=False,\n                max_length=1024,\n            ),\n            "config_annotation_keys_after": bounded_change_text(\n                data.get(\n                    "config_annotation_keys_after"\n                ),\n                required=False,\n                max_length=1024,\n            ),\n            "config_annotation_fingerprint_before": bounded_change_text(\n                data.get(\n                    "config_annotation_fingerprint_before"\n                ),\n                required=False,\n                max_length=128,\n            ),\n            "config_annotation_fingerprint_after": bounded_change_text(\n                data.get(\n                    "config_annotation_fingerprint_after"\n                ),\n                required=False,\n                max_length=128,\n            ),\n            "config_annotation_changed": bounded_change_bool(\n                data.get(\n                    "config_annotation_changed"\n                )\n            ),\n            "current_configmap_metadata_status": (\n                metadata_status\n            ),\n            "current_configmap_metadata_summary": bounded_change_text(\n                data.get(\n                    "current_configmap_metadata_summary"\n                ),\n                required=False,\n                max_length=1536,\n            ),\n            "current_configmap_metadata_error": bounded_change_text(\n                data.get(\n                    "current_configmap_metadata_error"\n                ),\n                required=False,\n                max_length=512,\n            ),\n            "secret_content_queried": False,\n            "configmap_content_exposed": False,\n        }\n\n        return EvidenceItem(\n            probe=probe,\n            source="kubernetes_change",\n            success=True,\n            trusted=True,\n            production_signal=True,\n            reliability=1.0,\n            observed_at=observed_at,\n            cluster=evidence_cluster,\n            cluster_verified=cluster_verified,\n            facts=facts,\n        )\n\n    def _normalize_kubernetes_change(\n        self,\n        scope: InvestigationScope,\n        probe: InvestigationProbe,\n        result: Any,\n    ) -> EvidenceItem:\n        data, observed_at, evidence_cluster, cluster_verified = (\n            self._validate_tool_evidence(\n                result=result,\n                expected_source="kubernetes_change",\n                expected_cluster=scope.cluster,\n            )\n        )\n\n        if (\n            data.get(\n                "owner_chain_verified"\n            )\n            is not True\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes change owner chain is untrusted"\n            )\n\n        if (\n            data.get(\n                "workload_kind"\n            )\n            != "Deployment"\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes change workload kind is unsupported"\n            )\n\n        deployment_name = bounded_change_text(\n            data.get(\n                "deployment_name"\n            ),\n            required=True,\n        )\n\n        rollout_started_at = bounded_change_text(\n            data.get(\n                "rollout_started_at"\n            ),\n            required=False,\n        )\n\n        rollout_offset_seconds = None\n        recent_rollout_before_incident = None\n\n        if (\n            rollout_started_at is not None\n            and scope.event_occurred_at\n            is not None\n        ):\n            rollout_time = parse_observed_at(\n                rollout_started_at\n            )\n\n            rollout_offset_seconds = (\n                scope.event_occurred_at\n                .astimezone(\n                    UTC\n                )\n                - rollout_time\n            ).total_seconds()\n\n            recent_rollout_before_incident = (\n                0.0\n                <= rollout_offset_seconds\n                <= 1800.0\n            )\n\n        facts = {\n            "temporal_basis": (\n                "workload_change_history"\n            ),\n            "owner_chain_verified": True,\n            "deployment_name": (\n                deployment_name\n            ),\n            "revision_before": bounded_change_int(\n                data.get(\n                    "revision_before"\n                )\n            ),\n            "revision_after": bounded_change_int(\n                data.get(\n                    "revision_after"\n                )\n            ),\n            "revision_changed": bounded_change_bool(\n                data.get(\n                    "revision_changed"\n                )\n            ),\n            "image_before": bounded_change_text(\n                data.get(\n                    "image_before"\n                ),\n                required=False,\n            ),\n            "image_after": bounded_change_text(\n                data.get(\n                    "image_after"\n                ),\n                required=False,\n            ),\n            "image_changed": bounded_change_bool(\n                data.get(\n                    "image_changed"\n                )\n            ),\n            "rollout_started_at": (\n                rollout_started_at\n            ),\n            "rollout_offset_seconds": (\n                rollout_offset_seconds\n            ),\n            "recent_rollout_before_incident": (\n                recent_rollout_before_incident\n            ),\n            "generation": bounded_change_int(\n                data.get(\n                    "generation"\n                )\n            ),\n            "observed_generation": bounded_change_int(\n                data.get(\n                    "observed_generation"\n                )\n            ),\n            "replicas_desired": bounded_change_int(\n                data.get(\n                    "replicas_desired"\n                )\n            ),\n            "replicas_updated": bounded_change_int(\n                data.get(\n                    "replicas_updated"\n                )\n            ),\n            "replicas_ready": bounded_change_int(\n                data.get(\n                    "replicas_ready"\n                )\n            ),\n            "replicas_available": bounded_change_int(\n                data.get(\n                    "replicas_available"\n                )\n            ),\n            "replicas_unavailable": bounded_change_int(\n                data.get(\n                    "replicas_unavailable"\n                )\n            ),\n            "history_complete": bounded_change_bool(\n                data.get(\n                    "history_complete"\n                )\n            ),\n            "rollout_condition_summary": bounded_change_text(\n                data.get(\n                    "rollout_condition_summary"\n                ),\n                required=False,\n                max_length=512,\n            ),\n            "generation_observed": bounded_change_bool(\n                data.get(\n                    "generation_observed"\n                )\n            ),\n            "rollout_complete": bounded_change_bool(\n                data.get(\n                    "rollout_complete"\n                )\n            ),\n            "rollout_failure_signal": bounded_change_bool(\n                data.get(\n                    "rollout_failure_signal"\n                )\n            ),\n            "rollout_failure_reason": bounded_change_text(\n                data.get(\n                    "rollout_failure_reason"\n                ),\n                required=False,\n            ),\n            "events_status": bounded_change_events_status(\n                data.get(\n                    "events_status"\n                )\n            ),\n            "events_error_code": bounded_change_text(\n                data.get(\n                    "events_error_code"\n                ),\n                required=False,\n            ),\n            "recent_event_count": bounded_change_int(\n                data.get(\n                    "recent_event_count"\n                )\n            ),\n            "recent_warning_count": bounded_change_int(\n                data.get(\n                    "recent_warning_count"\n                )\n            ),\n            "recent_event_reasons": bounded_change_text(\n                data.get(\n                    "recent_event_reasons"\n                ),\n                required=False,\n                max_length=512,\n            ),\n            "recent_event_summary": bounded_change_text(\n                data.get(\n                    "recent_event_summary"\n                ),\n                required=False,\n                max_length=1536,\n            ),\n        }\n\n        return EvidenceItem(\n            probe=probe,\n            source="kubernetes_change",\n            success=True,\n            trusted=True,\n            production_signal=True,\n            reliability=1.0,\n            observed_at=observed_at,\n            cluster=evidence_cluster,\n            cluster_verified=cluster_verified,\n            facts=facts,\n        )\n\n    def _normalize_prometheus(\n        self,\n        scope: InvestigationScope,\n        probe: InvestigationProbe,\n        result: Any,\n    ) -> EvidenceItem:\n        data, observed_at, evidence_cluster, cluster_verified = (\n            self._validate_tool_evidence(\n                result=result,\n                expected_source="prometheus",\n                expected_cluster=scope.cluster,\n            )\n        )\n\n        result_type_value = data.get(\n            "resultType"\n        )\n\n        if (\n            not isinstance(\n                result_type_value,\n                str,\n            )\n            or result_type_value\n            not in {\n                "vector",\n                "matrix",\n                "scalar",\n                "string",\n            }\n        ):\n            raise InvestigationProbeResponseError(\n                "Prometheus evidence result type is invalid"\n            )\n\n        result_type = (\n            result_type_value[:64]\n        )\n\n        samples = extract_numeric_samples(\n            result_type=result_type,\n            value=data.get(\n                "result"\n            ),\n        )\n\n        if not samples:\n            raise InvestigationProbeResponseError(\n                "Prometheus evidence contains no numeric samples"\n            )\n\n        try:\n            event_offset_seconds = (\n                self.time_policy.validate_observed_at(\n                    scope=scope,\n                    probe=probe,\n                    observed_at=observed_at,\n                )\n            )\n        except InvestigationEvidenceTimeError as exc:\n            raise InvestigationProbeResponseError(\n                "Prometheus evidence is not "\n                "temporally relevant"\n            ) from exc\n\n        facts = {\n            "temporal_basis": (\n                self.time_policy.temporal_basis(\n                    scope=scope,\n                    probe=probe,\n                )\n            ),\n            "event_offset_seconds": (\n                event_offset_seconds\n            ),\n            "result_type": result_type,\n            "sample_count": len(\n                samples\n            ),\n            "value_sum": sum(\n                samples\n            ),\n            "value_min": min(\n                samples\n            ),\n            "value_max": max(\n                samples\n            ),\n        }\n\n        return EvidenceItem(\n            probe=probe,\n            source="prometheus",\n            success=True,\n            trusted=True,\n            production_signal=True,\n            reliability=1.0,\n            observed_at=observed_at,\n            cluster=evidence_cluster,\n            cluster_verified=cluster_verified,\n            facts=facts,\n        )\n\n    @classmethod\n    def _validate_tool_evidence(\n        cls,\n        *,\n        result: Any,\n        expected_source: str,\n        expected_cluster: str | None,\n    ) -> tuple[\n        Mapping[str, Any],\n        datetime,\n        str | None,\n        bool,\n    ]:\n        if not isinstance(\n            result,\n            Mapping,\n        ):\n            raise InvestigationProbeResponseError(\n                "Investigation tool result is invalid"\n            )\n\n        if (\n            result.get(\n                "success"\n            )\n            is not True\n        ):\n            raise InvestigationProbeResponseError(\n                "Investigation tool result was unsuccessful"\n            )\n\n        source_value = result.get(\n            "source"\n        )\n\n        if not isinstance(\n            source_value,\n            str,\n        ):\n            raise InvestigationProbeResponseError(\n                "Investigation evidence source is invalid"\n            )\n\n        source = (\n            source_value\n            .strip()\n            .lower()\n        )\n\n        if source != expected_source:\n            raise InvestigationProbeResponseError(\n                "Investigation evidence source is untrusted"\n            )\n\n        mode_value = result.get(\n            "mode"\n        )\n\n        if not isinstance(\n            mode_value,\n            str,\n        ):\n            raise InvestigationProbeResponseError(\n                "Investigation evidence mode is invalid"\n            )\n\n        mode = (\n            mode_value\n            .strip()\n            .lower()\n        )\n\n        if mode != cls._TRUSTED_MODE:\n            raise InvestigationProbeResponseError(\n                "Investigation evidence mode is not read-only"\n            )\n\n        if (\n            result.get(\n                "production_signal"\n            )\n            is not True\n        ):\n            raise InvestigationProbeResponseError(\n                "Investigation evidence is not a production signal"\n            )\n\n        observed_at = parse_observed_at(\n            result.get(\n                "observed_at"\n            )\n        )\n\n        evidence_cluster, cluster_verified = (\n            cls._validate_cluster_identity(\n                result=result,\n                expected_cluster=expected_cluster,\n            )\n        )\n\n        data = result.get(\n            "data"\n        )\n\n        if not isinstance(\n            data,\n            Mapping,\n        ):\n            raise InvestigationProbeResponseError(\n                "Investigation evidence data is invalid"\n            )\n\n        return (\n            data,\n            observed_at,\n            evidence_cluster,\n            cluster_verified,\n        )\n\n    @staticmethod\n    def _validate_cluster_identity(\n        *,\n        result: Mapping[str, Any],\n        expected_cluster: str | None,\n    ) -> tuple[\n        str | None,\n        bool,\n    ]:\n        """\n        Validate explicit provider-reported cluster identity when present.\n\n        Missing identity remains compatible with legacy single-cluster and\n        historical tools. Multi-cluster routers installed by the platform\n        always return an explicit cluster, so routed production evidence\n        becomes cluster_verified=True.\n\n        An explicit mismatch is never tolerated.\n        """\n\n        reported_value = result.get(\n            "cluster"\n        )\n\n        if reported_value is None:\n            return (\n                None,\n                False,\n            )\n\n        if not isinstance(\n            reported_value,\n            str,\n        ):\n            raise InvestigationProbeResponseError(\n                "Investigation evidence cluster identity is invalid"\n            )\n\n        reported = reported_value.strip()\n\n        if (\n            not reported\n            or reported != reported_value\n            or len(\n                reported\n            )\n            > 256\n            or "\\x00" in reported\n        ):\n            raise InvestigationProbeResponseError(\n                "Investigation evidence cluster identity is invalid"\n            )\n\n        if expected_cluster is None:\n            return (\n                reported,\n                False,\n            )\n\n        expected = expected_cluster.strip()\n\n        if reported != expected:\n            raise InvestigationProbeResponseError(\n                "Investigation evidence cluster does not match trusted scope"\n            )\n\n        return (\n            reported,\n            True,\n        )\n\n    @staticmethod\n    def _escape_label(\n        value: str,\n    ) -> str:\n        return (\n            value\n            .replace(\n                "\\\\",\n                "\\\\\\\\",\n            )\n            .replace(\n                "\\n",\n                "\\\\n",\n            )\n            .replace(\n                "\\r",\n                "\\\\r",\n            )\n            .replace(\n                \'"\',\n                \'\\\\"\',\n            )\n        )\n\n\ndef redact_log_excerpt(\n    value: str,\n) -> tuple[str, int]:\n    """\n    Defense-in-depth redaction at the Investigation trust boundary.\n\n    KubernetesTool redacts before ToolManager tracing. This second pass keeps\n    injected or forged ToolManager responses from placing obvious credentials\n    into bounded InvestigationState.\n    """\n\n    text = value\n    total = 0\n\n    patterns = [\n        (\n            re.compile(\n                (\n                    r"\\beyJ[A-Za-z0-9_-]{10,}"\n                    r"\\.[A-Za-z0-9_-]{10,}"\n                    r"\\.[A-Za-z0-9_-]{10,}\\b"\n                )\n            ),\n            "[REDACTED_JWT]",\n        ),\n        (\n            re.compile(\n                (\n                    r"(?i)\\b("\n                    r"bearer|basic"\n                    r")\\s+"\n                    r"[A-Za-z0-9._~+/=-]{8,}"\n                )\n            ),\n            None,\n        ),\n        (\n            re.compile(\n                (\n                    r"(?i)\\b("\n                    r"password|passwd|pwd|secret|token|"\n                    r"api[_-]?key|access[_-]?key|"\n                    r"client[_-]?secret"\n                    r")\\b"\n                    r"(\\s*[:=]\\s*)"\n                    r"([\\"\']?)"\n                    r"([^\\s,;\\"\']{4,})"\n                    r"([\\"\']?)"\n                )\n            ),\n            None,\n        ),\n    ]\n\n    text, count = patterns[0][0].subn(\n        patterns[0][1],\n        text,\n    )\n\n    total += count\n\n    text, count = patterns[1][0].subn(\n        lambda match: (\n            match.group(1)\n            + " [REDACTED]"\n        ),\n        text,\n    )\n\n    total += count\n\n    text, count = patterns[2][0].subn(\n        lambda match: (\n            match.group(1)\n            + match.group(2)\n            + "[REDACTED]"\n        ),\n        text,\n    )\n\n    total += count\n\n    return (\n        text,\n        total,\n    )\n\n\ndef bounded_change_text(\n    value: Any,\n    *,\n    required: bool,\n    max_length: int = 512,\n) -> str | None:\n    if value is None:\n        if required:\n            raise InvestigationProbeResponseError(\n                "Kubernetes change text fact is missing"\n            )\n        return None\n\n    if not isinstance(\n        value,\n        str,\n    ):\n        raise InvestigationProbeResponseError(\n            "Kubernetes change text fact is invalid"\n        )\n\n    normalized = value.strip()\n\n    if not normalized:\n        if required:\n            raise InvestigationProbeResponseError(\n                "Kubernetes change text fact is missing"\n            )\n        return None\n\n    if len(\n        normalized\n    ) > max_length:\n        raise InvestigationProbeResponseError(\n            "Kubernetes change text fact is too large"\n        )\n\n    return normalized\n\n\ndef bounded_change_int(\n    value: Any,\n) -> int | None:\n    if value is None:\n        return None\n\n    if (\n        isinstance(\n            value,\n            bool,\n        )\n        or not isinstance(\n            value,\n            int,\n        )\n        or value < 0\n        or value > 1_000_000_000\n    ):\n        raise InvestigationProbeResponseError(\n            "Kubernetes change integer fact is invalid"\n        )\n\n    return value\n\n\ndef bounded_change_events_status(\n    value: Any,\n) -> str | None:\n    if value is None:\n        return None\n\n    if value not in {\n        "complete",\n        "partial",\n        "unavailable",\n    }:\n        raise InvestigationProbeResponseError(\n            "Kubernetes event evidence status is invalid"\n        )\n\n    return value\n\n\ndef bounded_change_bool(\n    value: Any,\n) -> bool | None:\n    if value is None:\n        return None\n\n    if not isinstance(\n        value,\n        bool,\n    ):\n        raise InvestigationProbeResponseError(\n            "Kubernetes change boolean fact is invalid"\n        )\n\n    return value\n\n\ndef cls_scalar(\n    value: Any,\n):\n    if (\n        value is None\n        or isinstance(\n            value,\n            (\n                bool,\n                int,\n                float,\n                str,\n            ),\n        )\n    ):\n        return value\n\n    return str(\n        value\n    )[:256]\n\n\ndef parse_observed_at(\n    value: Any,\n) -> datetime:\n    if isinstance(\n        value,\n        datetime,\n    ):\n        parsed = value\n\n    elif isinstance(\n        value,\n        str,\n    ):\n        text = value.strip()\n\n        if not text:\n            raise InvestigationProbeResponseError(\n                "Investigation evidence observed_at is invalid"\n            )\n\n        if text.endswith(\n            "Z"\n        ):\n            text = (\n                f"{text[:-1]}+00:00"\n            )\n\n        try:\n            parsed = datetime.fromisoformat(\n                text\n            )\n        except ValueError as exc:\n            raise InvestigationProbeResponseError(\n                "Investigation evidence observed_at is invalid"\n            ) from exc\n\n    else:\n        raise InvestigationProbeResponseError(\n            "Investigation evidence observed_at is invalid"\n        )\n\n    if parsed.tzinfo is None:\n        raise InvestigationProbeResponseError(\n            "Investigation evidence observed_at must be timezone-aware"\n        )\n\n    return parsed.astimezone(\n        UTC\n    )\n\n\ndef extract_numeric_samples(\n    result_type: str | None,\n    value: Any,\n) -> list[float]:\n    samples: list[float] = []\n\n    def add_sample(\n        sample: Any,\n    ) -> None:\n        if (\n            not isinstance(\n                sample,\n                list,\n            )\n            or len(sample) < 2\n            or len(samples) >= 32\n        ):\n            return\n\n        try:\n            numeric_value = float(\n                sample[1]\n            )\n        except (\n            TypeError,\n            ValueError,\n        ):\n            return\n\n        if not isfinite(\n            numeric_value\n        ):\n            return\n\n        samples.append(\n            numeric_value\n        )\n\n    if result_type in {\n        "scalar",\n        "string",\n    }:\n        add_sample(\n            value\n        )\n\n    elif (\n        result_type == "vector"\n        and isinstance(\n            value,\n            list,\n        )\n    ):\n        for item in value[:32]:\n            if isinstance(\n                item,\n                Mapping,\n            ):\n                add_sample(\n                    item.get(\n                        "value"\n                    )\n                )\n\n    elif (\n        result_type == "matrix"\n        and isinstance(\n            value,\n            list,\n        )\n    ):\n        for item in value[:32]:\n            if not isinstance(\n                item,\n                Mapping,\n            ):\n                continue\n\n            values = item.get(\n                "values"\n            )\n\n            if (\n                isinstance(\n                    values,\n                    list,\n                )\n                and values\n            ):\n                add_sample(\n                    values[-1]\n                )\n\n    return samples\n\n\n__all__ = [\n    "InvestigationProbeError",\n    "InvestigationProbeResponseError",\n    "InvestigationToolUnavailableError",\n    "ReadOnlyInvestigationProbeExecutor",\n    "extract_numeric_samples",\n    "parse_observed_at",\n]\n'
COORDINATOR_SOURCE = 'import asyncio\nimport time\nfrom datetime import UTC, datetime\n\nfrom services.agent_runtime.app.investigation.epistemic_guard import (\n    EpistemicConclusionGuard,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    EvidenceItem,\n    InvestigationLimits,\n    InvestigationProbe,\n    InvestigationScope,\n    InvestigationState,\n    default_investigation_probes,\n    InvestigationStatus,\n    InvestigationStopReason,\n)\nfrom services.agent_runtime.app.investigation.reasoner import (\n    BaseInvestigationReasoner,\n)\n\n\nclass EvidenceDrivenInvestigationCoordinator:\n    """\n    Run one bounded, read-only, Shadow evidence investigation.\n\n    This coordinator is deliberately independent from PlannerPipeline and\n    ActionRuntime in v1. Calling it writes only a bounded JSON snapshot to\n    context.metadata["investigation_shadow"]. It never writes variables,\n    Incident state, Approval, Action, Verification, budget or Kubernetes.\n    """\n\n    def __init__(\n        self,\n        reasoner: BaseInvestigationReasoner,\n        probe_executor,\n        limits: InvestigationLimits | None = None,\n        monotonic_clock=None,\n        utc_clock=None,\n    ) -> None:\n        if not isinstance(\n            reasoner,\n            BaseInvestigationReasoner,\n        ):\n            raise TypeError(\n                "Investigation reasoner is invalid"\n            )\n\n        if probe_executor is None or not callable(\n            getattr(probe_executor, "collect", None)\n        ):\n            raise TypeError(\n                "Investigation probe executor is invalid"\n            )\n\n        self.reasoner = reasoner\n        self.probe_executor = probe_executor\n        self.limits = limits or InvestigationLimits()\n        self._monotonic = monotonic_clock or time.monotonic\n        self._utc_clock = utc_clock or (\n            lambda: datetime.now(UTC)\n        )\n\n    async def investigate(\n        self,\n        context,\n    ) -> InvestigationState:\n        scope = self._scope_from_context(\n            context\n        )\n        started_at = self._now()\n        started_monotonic = self._monotonic()\n\n        state = InvestigationState(\n            status=InvestigationStatus.RUNNING,\n            scope=scope,\n            limits=self.limits,\n            started_at=started_at,\n            updated_at=started_at,\n            available_probes=self._available_probes(\n                context\n            ),\n        )\n\n        while state.status == InvestigationStatus.RUNNING:\n            if state.iteration_count >= self.limits.max_iterations:\n                self._stop(\n                    state,\n                    status=InvestigationStatus.EXHAUSTED,\n                    reason=InvestigationStopReason.MAX_ITERATIONS,\n                )\n                break\n\n            remaining = self._remaining_seconds(\n                started_monotonic\n            )\n            if remaining <= 0:\n                self._stop(\n                    state,\n                    status=InvestigationStatus.EXHAUSTED,\n                    reason=InvestigationStopReason.TIMEOUT,\n                )\n                break\n\n            try:\n                decision = await asyncio.wait_for(\n                    self.reasoner.decide(\n                        scope,\n                        state.model_copy(deep=True),\n                    ),\n                    timeout=remaining,\n                )\n            except TimeoutError:\n                self._stop(\n                    state,\n                    status=InvestigationStatus.EXHAUSTED,\n                    reason=InvestigationStopReason.TIMEOUT,\n                )\n                break\n            except Exception as exc:\n                self._stop(\n                    state,\n                    status=InvestigationStatus.FAILED,\n                    reason=InvestigationStopReason.REASONER_ERROR,\n                    failure_code=type(exc).__name__,\n                )\n                break\n\n            if not self._evidence_references_are_valid(\n                decision=decision,\n                state=state,\n            ):\n                self._stop(\n                    state,\n                    status=InvestigationStatus.FAILED,\n                    reason=InvestigationStopReason.REASONER_ERROR,\n                    failure_code="InvalidEvidenceReference",\n                )\n                break\n\n            state.iteration_count += 1\n            state.hypotheses = [\n                item.model_copy(deep=True)\n                for item in decision.hypotheses\n            ]\n            state.decision_summaries.append(\n                decision.rationale_summary\n            )\n            state.updated_at = self._now()\n\n            if decision.stop:\n                guard_result = (\n                    EpistemicConclusionGuard()\n                    .evaluate(\n                        decision=decision,\n                        state=state,\n                    )\n                )\n\n                if not guard_result.allowed:\n                    state.epistemic_guard_code = (\n                        guard_result.code\n                    )\n\n                    self._stop(\n                        state,\n                        status=InvestigationStatus.CONCLUDED,\n                        reason=(\n                            InvestigationStopReason\n                            .INSUFFICIENT_EVIDENCE\n                        ),\n                    )\n\n                    state.conclusion = None\n                    break\n\n                self._stop(\n                    state,\n                    status=InvestigationStatus.CONCLUDED,\n                    reason=decision.stop_reason,\n                )\n                state.conclusion = decision.conclusion\n                break\n\n            probe = decision.next_probe\n            if probe is None:\n                self._stop(\n                    state,\n                    status=InvestigationStatus.EXHAUSTED,\n                    reason=InvestigationStopReason.NO_SAFE_PROBE,\n                )\n                break\n\n            if probe in state.attempted_probes:\n                self._stop(\n                    state,\n                    status=InvestigationStatus.EXHAUSTED,\n                    reason=InvestigationStopReason.DUPLICATE_PROBE,\n                )\n                break\n\n            if state.tool_call_count >= self.limits.max_tool_calls:\n                self._stop(\n                    state,\n                    status=InvestigationStatus.EXHAUSTED,\n                    reason=InvestigationStopReason.MAX_TOOL_CALLS,\n                )\n                break\n\n            remaining = self._remaining_seconds(\n                started_monotonic\n            )\n            if remaining <= 0:\n                self._stop(\n                    state,\n                    status=InvestigationStatus.EXHAUSTED,\n                    reason=InvestigationStopReason.TIMEOUT,\n                )\n                break\n\n            state.attempted_probes.append(probe)\n            state.tool_call_count += 1\n\n            try:\n                evidence = await asyncio.wait_for(\n                    self.probe_executor.collect(\n                        context,\n                        scope,\n                        probe,\n                    ),\n                    timeout=remaining,\n                )\n\n                if not self._evidence_cluster_is_consistent(\n                    scope=scope,\n                    evidence=evidence,\n                ):\n                    evidence = EvidenceItem(\n                        probe=probe,\n                        source="investigation_probe",\n                        success=False,\n                        trusted=False,\n                        production_signal=False,\n                        reliability=0.0,\n                        observed_at=self._now(),\n                        facts={},\n                        error_code="ClusterEvidenceMismatch",\n                    )\n\n            except TimeoutError:\n                self._stop(\n                    state,\n                    status=InvestigationStatus.EXHAUSTED,\n                    reason=InvestigationStopReason.TIMEOUT,\n                )\n                break\n            except Exception as exc:\n                evidence = EvidenceItem(\n                    probe=probe,\n                    source="investigation_probe",\n                    success=False,\n                    trusted=False,\n                    production_signal=False,\n                    reliability=0.0,\n                    observed_at=self._now(),\n                    facts={},\n                    error_code=type(exc).__name__[:256],\n                )\n\n            state.evidence.append(evidence)\n            state.updated_at = self._now()\n\n        self._publish_shadow_snapshot(\n            context=context,\n            state=state,\n        )\n        return state\n\n    @staticmethod\n    def _scope_from_context(\n        context,\n    ) -> InvestigationScope:\n        event = getattr(\n            context,\n            "event",\n            None,\n        )\n        signal = getattr(\n            event,\n            "signal",\n            None,\n        )\n        resources = getattr(\n            event,\n            "resources",\n            None,\n        )\n\n        if signal is None or not resources:\n            raise ValueError(\n                "Investigation requires one event resource"\n            )\n\n        resource = resources[0]\n\n        header = getattr(\n            event,\n            "header",\n            None,\n        )\n\n        event_occurred_at = getattr(\n            header,\n            "occurred_at",\n            None,\n        )\n\n        if event_occurred_at is not None:\n            if (\n                not isinstance(\n                    event_occurred_at,\n                    datetime,\n                )\n                or event_occurred_at.tzinfo is None\n            ):\n                raise ValueError(\n                    "Investigation event occurred_at "\n                    "must be timezone-aware"\n                )\n\n            event_occurred_at = (\n                event_occurred_at.astimezone(\n                    UTC\n                )\n            )\n\n        return InvestigationScope(\n            alert_name=str(\n                getattr(signal, "name", "")\n            ),\n            alert_message=str(\n                getattr(signal, "message", "")\n                or ""\n            ),\n            event_occurred_at=event_occurred_at,\n            resource=str(\n                getattr(resource, "name", "")\n            ),\n            namespace=str(\n                getattr(resource, "namespace", None)\n                or "default"\n            ),\n            cluster=(\n                str(getattr(resource, "cluster"))\n                if getattr(resource, "cluster", None)\n                else None\n            ),\n        )\n\n    def _available_probes(\n        self,\n        context,\n    ) -> list[InvestigationProbe]:\n        resolver = getattr(\n            self.probe_executor,\n            "available_probes",\n            None,\n        )\n\n        if not callable(\n            resolver\n        ):\n            return default_investigation_probes()\n\n        resolved = resolver(\n            context\n        )\n\n        if not isinstance(\n            resolved,\n            (\n                list,\n                tuple,\n            ),\n        ):\n            raise TypeError(\n                "Investigation available probes are invalid"\n            )\n\n        normalized: list[\n            InvestigationProbe\n        ] = []\n\n        for item in resolved:\n            if not isinstance(\n                item,\n                InvestigationProbe,\n            ):\n                raise TypeError(\n                    "Investigation available probe is invalid"\n                )\n\n            if item not in normalized:\n                normalized.append(\n                    item\n                )\n\n        if not normalized:\n            raise ValueError(\n                "Investigation requires at least one available probe"\n            )\n\n        return normalized\n\n    def _remaining_seconds(\n        self,\n        started_monotonic: float,\n    ) -> float:\n        return (\n            self.limits.timeout_seconds\n            - (\n                self._monotonic()\n                - started_monotonic\n            )\n        )\n\n    def _stop(\n        self,\n        state: InvestigationState,\n        status: InvestigationStatus,\n        reason: InvestigationStopReason | None,\n        failure_code: str | None = None,\n    ) -> None:\n        state.status = status\n        state.stop_reason = reason\n        state.failure_code = failure_code\n        state.updated_at = self._now()\n\n    def _now(self) -> datetime:\n        value = self._utc_clock()\n        if value.tzinfo is None:\n            return value.replace(tzinfo=UTC)\n        return value.astimezone(UTC)\n\n    @staticmethod\n    def _evidence_cluster_is_consistent(\n        *,\n        scope: InvestigationScope,\n        evidence: EvidenceItem,\n    ) -> bool:\n        """\n        Defense in depth for custom/replay ProbeExecutors.\n\n        Identity-less legacy evidence remains compatible. Any explicit\n        provider-reported cluster that conflicts with trusted Incident scope\n        is replaced by a failed, fact-free EvidenceItem before Reasoner sees\n        its facts.\n        """\n\n        if not isinstance(\n            evidence,\n            EvidenceItem,\n        ):\n            return False\n\n        if evidence.cluster is None:\n            return not evidence.cluster_verified\n\n        if scope.cluster is None:\n            return not evidence.cluster_verified\n\n        return (\n            evidence.cluster\n            == scope.cluster\n        )\n\n    @staticmethod\n    def _publish_shadow_snapshot(\n        context,\n        state: InvestigationState,\n    ) -> None:\n        metadata = getattr(\n            context,\n            "metadata",\n            None,\n        )\n\n        if not isinstance(metadata, dict):\n            raise TypeError(\n                "Investigation context metadata is unavailable"\n            )\n\n        metadata["investigation_shadow"] = (\n            state.model_dump(mode="json")\n        )\n\n    @staticmethod\n    def _evidence_references_are_valid(\n        decision,\n        state: InvestigationState,\n    ) -> bool:\n        known_ids = {\n            item.evidence_id\n            for item in state.evidence\n        }\n\n        for hypothesis in decision.hypotheses:\n            referenced_ids = set(\n                hypothesis.supporting_evidence_ids\n            ) | set(\n                hypothesis.conflicting_evidence_ids\n            )\n\n            if not referenced_ids.issubset(\n                known_ids\n            ):\n                return False\n\n        conclusion = decision.conclusion\n\n        if conclusion is None:\n            return True\n\n        conclusion_ids = set(\n            conclusion.evidence_ids\n        )\n        if not conclusion_ids.issubset(\n            known_ids\n        ):\n            return False\n\n        trusted_ids = {\n            item.evidence_id\n            for item in state.evidence\n            if item.trusted\n        }\n\n        return (\n            bool(conclusion_ids)\n            and conclusion_ids.issubset(\n                trusted_ids\n            )\n        )\n'
COLLECTOR_SOURCE = 'from collections.abc import Callable, Mapping\nfrom dataclasses import dataclass, field\nfrom datetime import UTC, datetime, timedelta\nfrom typing import Any\n\nfrom services.agent_runtime.app.tools.manager import (\n    ToolManager,\n)\nfrom services.agent_runtime.app.verification.models import (\n    VerificationCheck,\n    VerificationSource,\n)\n\n\n@dataclass(\n    frozen=True,\n    slots=True,\n)\nclass VerificationEvaluation:\n    """\n    Result produced after trusted evidence is evaluated.\n    """\n\n    passed: bool | None\n    observed_value: Any = None\n    expected_value: Any = None\n    message: str = ""\n    metadata: Mapping[str, Any] = field(\n        default_factory=dict\n    )\n\n\nEvidenceEvaluator = Callable[\n    [Mapping[str, Any]],\n    VerificationEvaluation,\n]\n\n\n@dataclass(\n    frozen=True,\n    slots=True,\n)\nclass VerificationProbe:\n    """\n    Definition of one read-only verification probe.\n\n    The tool response must use this evidence envelope:\n    - success: true\n    - source: expected provider name\n    - mode: live / production / read_only\n    - production_signal: true\n    - observed_at: timezone-aware datetime or ISO-8601 text\n    """\n\n    name: str\n    source: VerificationSource\n    tool: str\n    provider: str\n    arguments: Mapping[str, Any]\n    evaluator: EvidenceEvaluator\n    required: bool = True\n\n\nclass VerificationEvidenceCollector:\n    """\n    Collect and evaluate production verification evidence.\n\n    This class is fail-closed:\n    untrusted, stale, malformed, or unavailable evidence produces\n    an inconclusive VerificationCheck instead of a passing check.\n    """\n\n    _TRUSTED_MODES = {\n        "live",\n        "production",\n        "read_only",\n    }\n\n    _UNTRUSTED_MARKERS = {\n        "dry_run",\n        "fake",\n        "mock",\n        "simulated",\n        "simulation",\n        "test",\n    }\n\n    def __init__(\n        self,\n        tools: ToolManager,\n        max_evidence_age: timedelta = timedelta(\n            minutes=5\n        ),\n        max_future_skew: timedelta = timedelta(\n            seconds=30\n        ),\n        clock: Callable[[], datetime] | None = None,\n    ) -> None:\n        if max_evidence_age <= timedelta(0):\n            raise ValueError(\n                "max_evidence_age must be positive"\n            )\n\n        if max_future_skew < timedelta(0):\n            raise ValueError(\n                "max_future_skew cannot be negative"\n            )\n\n        self.tools = tools\n        self.max_evidence_age = max_evidence_age\n        self.max_future_skew = max_future_skew\n        self._clock = clock or (\n            lambda: datetime.now(UTC)\n        )\n\n    async def collect(\n        self,\n        probes: list[VerificationProbe],\n        context=None,\n    ) -> list[VerificationCheck]:\n        """\n        Run probes sequentially to avoid verification load spikes.\n        """\n\n        checks: list[VerificationCheck] = []\n\n        for probe in probes:\n            check = await self.collect_one(\n                probe,\n                context=context,\n            )\n            checks.append(check)\n\n        return checks\n\n    async def collect_one(\n        self,\n        probe: VerificationProbe,\n        context=None,\n    ) -> VerificationCheck:\n        checked_at = self._now()\n\n        try:\n            result = await self.tools.call(\n                probe.tool,\n                context=context,\n                **dict(probe.arguments),\n            )\n        except Exception as exc:\n            return self._inconclusive_check(\n                probe=probe,\n                checked_at=checked_at,\n                message=(\n                    "Verification evidence collection failed"\n                ),\n                metadata={\n                    "error_type": type(exc).__name__,\n                    "error": str(exc),\n                },\n            )\n\n        if not isinstance(result, Mapping):\n            return self._inconclusive_check(\n                probe=probe,\n                checked_at=checked_at,\n                message=(\n                    "Verification evidence was rejected"\n                ),\n                metadata={\n                    "rejection_reasons": [\n                        "tool result is not a mapping"\n                    ]\n                },\n            )\n\n        (\n            rejection_reasons,\n            observed_at,\n            evidence_cluster,\n            cluster_verified,\n        ) = self._validate_evidence(\n            probe=probe,\n            evidence=result,\n            now=checked_at,\n        )\n\n        if rejection_reasons:\n            return self._inconclusive_check(\n                probe=probe,\n                checked_at=checked_at,\n                message=(\n                    "Verification evidence was rejected"\n                ),\n                metadata={\n                    "rejection_reasons": (\n                        rejection_reasons\n                    )\n                },\n            )\n\n        try:\n            evaluation = probe.evaluator(\n                result\n            )\n        except Exception as exc:\n            return self._inconclusive_check(\n                probe=probe,\n                checked_at=checked_at,\n                message=(\n                    "Verification evidence evaluation failed"\n                ),\n                metadata={\n                    "error_type": type(exc).__name__,\n                    "error": str(exc),\n                },\n            )\n\n        if not isinstance(\n            evaluation,\n            VerificationEvaluation,\n        ):\n            return self._inconclusive_check(\n                probe=probe,\n                checked_at=checked_at,\n                message=(\n                    "Verification evaluator returned "\n                    "an invalid result"\n                ),\n            )\n\n        metadata = dict(\n            evaluation.metadata\n        )\n        metadata.update(\n            {\n                "trusted": True,\n                "tool": probe.tool,\n                "provider": probe.provider,\n                "evidence_observed_at": (\n                    observed_at.isoformat()\n                    if observed_at\n                    else None\n                ),\n                "evidence_cluster": (\n                    evidence_cluster\n                ),\n                "cluster_verified": (\n                    cluster_verified\n                ),\n            }\n        )\n\n        return VerificationCheck(\n            name=probe.name,\n            source=probe.source,\n            passed=evaluation.passed,\n            required=probe.required,\n            observed_value=(\n                evaluation.observed_value\n            ),\n            expected_value=(\n                evaluation.expected_value\n            ),\n            message=evaluation.message,\n            checked_at=checked_at,\n            metadata=metadata,\n        )\n\n    def _validate_evidence(\n        self,\n        probe: VerificationProbe,\n        evidence: Mapping[str, Any],\n        now: datetime,\n    ) -> tuple[\n        list[str],\n        datetime | None,\n        str | None,\n        bool,\n    ]:\n        reasons: list[str] = []\n        evidence_cluster: str | None = None\n        cluster_verified = False\n\n        if evidence.get("success") is not True:\n            reasons.append(\n                "success is not true"\n            )\n\n        provider = str(\n            evidence.get(\n                "source",\n                "",\n            )\n        ).strip().lower()\n\n        expected_provider = (\n            probe.provider.strip().lower()\n        )\n\n        if provider != expected_provider:\n            reasons.append(\n                "source does not match expected provider"\n            )\n\n        mode = str(\n            evidence.get(\n                "mode",\n                "",\n            )\n        ).strip().lower()\n\n        if mode not in self._TRUSTED_MODES:\n            reasons.append(\n                "mode is not trusted"\n            )\n\n        identity_text = (\n            f"{provider} {mode}"\n        ).lower()\n\n        if any(\n            marker in identity_text\n            for marker in self._UNTRUSTED_MARKERS\n        ):\n            reasons.append(\n                "mock, test, or simulated evidence "\n                "is not allowed"\n            )\n\n        if (\n            evidence.get(\n                "production_signal"\n            )\n            is not True\n        ):\n            reasons.append(\n                "production_signal is not true"\n            )\n\n        expected_cluster_value = (\n            probe.arguments.get(\n                "cluster"\n            )\n        )\n\n        expected_cluster = (\n            str(\n                expected_cluster_value\n            ).strip()\n            if expected_cluster_value\n            is not None\n            else None\n        )\n\n        reported_cluster_value = (\n            evidence.get(\n                "cluster"\n            )\n        )\n\n        if reported_cluster_value is not None:\n            if not isinstance(\n                reported_cluster_value,\n                str,\n            ):\n                reasons.append(\n                    "cluster identity is invalid"\n                )\n\n            else:\n                reported_cluster = (\n                    reported_cluster_value.strip()\n                )\n\n                if (\n                    not reported_cluster\n                    or reported_cluster\n                    != reported_cluster_value\n                    or len(\n                        reported_cluster\n                    )\n                    > 256\n                    or "\\x00"\n                    in reported_cluster\n                ):\n                    reasons.append(\n                        "cluster identity is invalid"\n                    )\n\n                else:\n                    evidence_cluster = (\n                        reported_cluster\n                    )\n\n                    if expected_cluster:\n                        if (\n                            evidence_cluster\n                            != expected_cluster\n                        ):\n                            reasons.append(\n                                "cluster does not match expected scope"\n                            )\n\n                        else:\n                            cluster_verified = True\n\n        observed_at = self._parse_datetime(\n            evidence.get(\n                "observed_at"\n            )\n        )\n\n        if observed_at is None:\n            reasons.append(\n                "observed_at is missing or invalid"\n            )\n            return (\n                reasons,\n                None,\n                evidence_cluster,\n                cluster_verified,\n            )\n\n        age = now - observed_at\n\n        if age > self.max_evidence_age:\n            reasons.append(\n                "evidence is stale"\n            )\n\n        if age < -self.max_future_skew:\n            reasons.append(\n                "observed_at is too far in the future"\n            )\n\n        return (\n            reasons,\n            observed_at,\n            evidence_cluster,\n            cluster_verified,\n        )\n\n    def _inconclusive_check(\n        self,\n        probe: VerificationProbe,\n        checked_at: datetime,\n        message: str,\n        metadata: Mapping[str, Any] | None = None,\n    ) -> VerificationCheck:\n        check_metadata = dict(\n            metadata or {}\n        )\n        check_metadata.update(\n            {\n                "trusted": False,\n                "tool": probe.tool,\n                "provider": probe.provider,\n            }\n        )\n\n        return VerificationCheck(\n            name=probe.name,\n            source=probe.source,\n            passed=None,\n            required=probe.required,\n            message=message,\n            checked_at=checked_at,\n            metadata=check_metadata,\n        )\n\n    def _now(\n        self,\n    ) -> datetime:\n        value = self._clock()\n\n        if value.tzinfo is None:\n            raise ValueError(\n                "clock must return a timezone-aware datetime"\n            )\n\n        return value.astimezone(\n            UTC\n        )\n\n    @staticmethod\n    def _parse_datetime(\n        value: Any,\n    ) -> datetime | None:\n        if isinstance(\n            value,\n            datetime,\n        ):\n            parsed = value\n        elif isinstance(\n            value,\n            str,\n        ):\n            text = value.strip()\n\n            if text.endswith("Z"):\n                text = (\n                    f"{text[:-1]}+00:00"\n                )\n\n            try:\n                parsed = datetime.fromisoformat(\n                    text\n                )\n            except ValueError:\n                return None\n        else:\n            return None\n\n        if parsed.tzinfo is None:\n            return None\n\n        return parsed.astimezone(\n            UTC\n        )\n'
PROFILES_SOURCE = 'from collections.abc import Mapping, Sequence\nfrom dataclasses import dataclass\nfrom math import isfinite\nfrom typing import Any\n\nfrom services.agent_runtime.app.action.models import (\n    ActionPlan,\n    ActionType,\n)\nfrom services.agent_runtime.app.verification.collector import (\n    VerificationEvaluation,\n    VerificationProbe,\n)\nfrom services.agent_runtime.app.verification.models import (\n    VerificationSource,\n)\n\n\nclass VerificationProfileError(ValueError):\n    """A safe verification profile cannot be built."""\n\n\n@dataclass(frozen=True, slots=True)\nclass VerificationProfile:\n    """\n    Immutable verification definition for one remediation action.\n\n    A profile only declares read-only probes and evaluators. It does not\n    execute tools, persist Verification results, or update Incident state.\n    """\n\n    name: str\n    action: ActionType\n    target: str\n    namespace: str\n    cluster: str | None\n    probes: tuple[VerificationProbe, ...]\n\n    def __post_init__(self) -> None:\n        if not self.name.strip():\n            raise VerificationProfileError(\n                "Verification profile name cannot be empty"\n            )\n\n        if not self.target.strip():\n            raise VerificationProfileError(\n                "Verification target cannot be empty"\n            )\n\n        if not self.namespace.strip():\n            raise VerificationProfileError(\n                "Verification namespace cannot be empty"\n            )\n\n        if not self.probes:\n            raise VerificationProfileError(\n                "Verification profile requires at least one probe"\n            )\n\n        if not any(probe.required for probe in self.probes):\n            raise VerificationProfileError(\n                "Verification profile requires a required probe"\n            )\n\n\nclass VerificationProfileFactory:\n    """\n    Build deterministic action-specific verification profiles.\n\n    The first profile supports INCREASE_MEMORY_LIMIT. Natural-language text\n    from ActionPlan.metadata["verification"] is deliberately not parsed as a\n    production rule.\n    """\n\n    def __init__(\n        self,\n        memory_utilization_threshold: float = 0.90,\n        restart_increase_threshold: float = 0.0,\n        restart_window: str = "5m",\n    ) -> None:\n        if not 0.0 < memory_utilization_threshold <= 1.0:\n            raise ValueError(\n                "memory_utilization_threshold must be in (0, 1]"\n            )\n\n        if restart_increase_threshold < 0:\n            raise ValueError(\n                "restart_increase_threshold cannot be negative"\n            )\n\n        normalized_window = restart_window.strip()\n        if not normalized_window or not normalized_window.isalnum():\n            raise ValueError(\n                "restart_window must be a Prometheus duration"\n            )\n\n        self.memory_utilization_threshold = float(\n            memory_utilization_threshold\n        )\n        self.restart_increase_threshold = float(\n            restart_increase_threshold\n        )\n        self.restart_window = normalized_window\n\n    def create(\n        self,\n        plan: ActionPlan,\n        *,\n        namespace: str | None = None,\n        cluster: str | None = None,\n    ) -> VerificationProfile:\n        """\n        Build a profile without executing any probe.\n\n        The caller supplies scope from the StandardEvent resource because the\n        current ActionPlan does not persist namespace or cluster as fields.\n        """\n\n        target = self._normalize_target(plan.target)\n        namespace = self._normalize_namespace(namespace)\n        cluster = self._normalize_cluster(cluster)\n\n        if plan.type == ActionType.INCREASE_MEMORY_LIMIT:\n            return self._increase_memory_limit(\n                target=target,\n                namespace=namespace,\n                cluster=cluster,\n            )\n\n        raise VerificationProfileError(\n            "No verification profile is registered for action: "\n            f"{plan.type.value}"\n        )\n\n    def _increase_memory_limit(\n        self,\n        *,\n        target: str,\n        namespace: str,\n        cluster: str | None,\n    ) -> VerificationProfile:\n        kubernetes_arguments: dict[str, Any] = {\n            "action": "describe",\n            "resource": "pod",\n            "target": target,\n            "namespace": namespace,\n        }\n        if cluster is not None:\n            kubernetes_arguments["cluster"] = cluster\n\n        memory_arguments: dict[str, Any] = {\n            "query": self._memory_utilization_query(\n                target=target,\n                namespace=namespace,\n                cluster=cluster,\n            )\n        }\n\n        restart_arguments: dict[str, Any] = {\n            "query": self._restart_increase_query(\n                target=target,\n                namespace=namespace,\n                cluster=cluster,\n            )\n        }\n\n        if cluster is not None:\n            memory_arguments[\n                "cluster"\n            ] = cluster\n\n            restart_arguments[\n                "cluster"\n            ] = cluster\n\n        probes = (\n            VerificationProbe(\n                name="pod_ready_after_memory_increase",\n                source=VerificationSource.WORKLOAD,\n                tool="kubernetes",\n                provider="kubernetes",\n                arguments=kubernetes_arguments,\n                evaluator=_evaluate_pod_ready,\n                required=True,\n            ),\n            VerificationProbe(\n                name="memory_headroom_after_memory_increase",\n                source=VerificationSource.METRIC,\n                tool="prometheus",\n                provider="prometheus",\n                arguments=memory_arguments,\n                evaluator=_build_upper_bound_evaluator(\n                    threshold=self.memory_utilization_threshold,\n                    unit="ratio",\n                    success_message=(\n                        "Container memory utilization is within limit"\n                    ),\n                    failure_message=(\n                        "Container memory utilization remains too high"\n                    ),\n                ),\n                required=True,\n            ),\n            VerificationProbe(\n                name="pod_restart_stability_after_memory_increase",\n                source=VerificationSource.METRIC,\n                tool="prometheus",\n                provider="prometheus",\n                arguments=restart_arguments,\n                evaluator=_build_upper_bound_evaluator(\n                    threshold=self.restart_increase_threshold,\n                    unit="restarts",\n                    success_message="Pod restart count is stable",\n                    failure_message="Pod continues to restart",\n                ),\n                # Applying the remediation itself may restart the Pod. Without\n                # a pre-action baseline this signal must not block resolution.\n                required=False,\n            ),\n        )\n\n        return VerificationProfile(\n            name="increase_memory_limit_v1",\n            action=ActionType.INCREASE_MEMORY_LIMIT,\n            target=target,\n            namespace=namespace,\n            cluster=cluster,\n            probes=probes,\n        )\n\n    @staticmethod\n    def _memory_utilization_query(\n        *,\n        target: str,\n        namespace: str,\n        cluster: str | None,\n    ) -> str:\n        selector = _container_selector(\n            target=target,\n            namespace=namespace,\n            cluster=cluster,\n        )\n        return (\n            "max("\n            f"container_memory_working_set_bytes{{{selector}}}"\n            ") / clamp_min(max("\n            f"container_spec_memory_limit_bytes{{{selector}}}"\n            "), 1)"\n        )\n\n    def _restart_increase_query(\n        self,\n        *,\n        target: str,\n        namespace: str,\n        cluster: str | None,\n    ) -> str:\n        labels = [\n            ("pod", target),\n            ("namespace", namespace),\n        ]\n        if cluster is not None:\n            labels.append(("cluster", cluster))\n\n        selector = ",".join(\n            f\'{name}="{_escape_label_value(value)}"\'\n            for name, value in labels\n        )\n        return (\n            "sum(increase("\n            "kube_pod_container_status_restarts_total"\n            f"{{{selector}}}[{self.restart_window}]"\n            "))"\n        )\n\n    @staticmethod\n    def _normalize_target(value: Any) -> str:\n        target = str(value if value is not None else "").strip()\n        if not target or target.lower() == "unknown":\n            raise VerificationProfileError(\n                "Verification requires a concrete action target"\n            )\n        return target\n\n    @staticmethod\n    def _normalize_namespace(value: Any) -> str:\n        namespace = str(value if value is not None else "").strip()\n        return namespace or "default"\n\n    @staticmethod\n    def _normalize_cluster(value: Any) -> str | None:\n        cluster = str(value if value is not None else "").strip()\n        return cluster or None\n\n\ndef _evaluate_pod_ready(\n    evidence: Mapping[str, Any],\n) -> VerificationEvaluation:\n    data = evidence.get("data")\n    if not isinstance(data, Mapping):\n        return VerificationEvaluation(\n            passed=None,\n            message=(\n                "Kubernetes evidence does not contain normalized pod data"\n            ),\n        )\n\n    phase = str(data.get("phase", "")).strip()\n    ready = _read_bool(data, "ready", "pod_ready")\n    scheduled = _read_bool(data, "scheduled", "pod_scheduled")\n\n    observed_value = {\n        "phase": phase or None,\n        "ready": ready,\n        "scheduled": scheduled,\n        "restart_count": data.get("restart_count"),\n        "oom_killed": data.get("oom_killed"),\n    }\n    expected_value = {\n        "phase": "Running",\n        "ready": True,\n        "scheduled": True,\n    }\n\n    if not phase or ready is None or scheduled is None:\n        return VerificationEvaluation(\n            passed=None,\n            observed_value=observed_value,\n            expected_value=expected_value,\n            message="Kubernetes pod readiness evidence is incomplete",\n        )\n\n    passed = (\n        phase.lower() == "running"\n        and ready is True\n        and scheduled is True\n    )\n    return VerificationEvaluation(\n        passed=passed,\n        observed_value=observed_value,\n        expected_value=expected_value,\n        message=(\n            "Pod is running, scheduled, and ready"\n            if passed\n            else "Pod is not ready after remediation"\n        ),\n        metadata={"evaluation": "pod_readiness"},\n    )\n\n\ndef _build_upper_bound_evaluator(\n    *,\n    threshold: float,\n    unit: str,\n    success_message: str,\n    failure_message: str,\n):\n    def evaluate(\n        evidence: Mapping[str, Any],\n    ) -> VerificationEvaluation:\n        values = _prometheus_values(evidence)\n        expected_value = {\n            "operator": "<=",\n            "threshold": threshold,\n            "unit": unit,\n        }\n\n        if not values:\n            return VerificationEvaluation(\n                passed=None,\n                expected_value=expected_value,\n                message="Prometheus evidence contains no numeric samples",\n            )\n\n        observed = max(values)\n        passed = observed <= threshold\n        return VerificationEvaluation(\n            passed=passed,\n            observed_value=observed,\n            expected_value=expected_value,\n            message=success_message if passed else failure_message,\n            metadata={\n                "aggregation": "max",\n                "sample_count": len(values),\n            },\n        )\n\n    return evaluate\n\n\ndef _prometheus_values(\n    evidence: Mapping[str, Any],\n) -> list[float]:\n    """Read normalized vector, scalar, and legacy metric containers."""\n\n    candidates: list[Any] = []\n    data = evidence.get("data")\n\n    if isinstance(data, Mapping):\n        candidates.extend(\n            data[key]\n            for key in ("samples", "result", "value")\n            if key in data\n        )\n    elif data is not None:\n        candidates.append(data)\n\n    candidates.extend(\n        evidence[key]\n        for key in ("samples", "result", "value", "metrics")\n        if key in evidence\n    )\n\n    values: list[float] = []\n    for candidate in candidates:\n        values.extend(_numeric_samples(candidate))\n    return values\n\n\ndef _numeric_samples(value: Any) -> list[float]:\n    direct = _as_finite_float(value)\n    if direct is not None:\n        return [direct]\n\n    if isinstance(value, Mapping):\n        for key in ("sample_value", "value"):\n            if key in value:\n                return _numeric_samples(value[key])\n\n        if "values" in value:\n            matrix = value["values"]\n            if isinstance(matrix, Sequence) and not isinstance(\n                matrix,\n                (str, bytes),\n            ):\n                return [\n                    parsed\n                    for sample in matrix\n                    if (parsed := _sample_pair_value(sample)) is not None\n                ]\n\n        return [\n            parsed\n            for nested in value.values()\n            if (parsed := _as_finite_float(nested)) is not None\n        ]\n\n    if isinstance(value, Sequence) and not isinstance(\n        value,\n        (str, bytes),\n    ):\n        is_sample_pair = (\n            len(value) == 2\n            and _as_finite_float(value[0])\n            is not None\n        )\n\n        if is_sample_pair:\n            pair_value = _sample_pair_value(\n                value\n            )\n\n            # A Prometheus sample pair with an invalid value must not fall\n            # through and expose its timestamp as a metric value.\n            return (\n                [pair_value]\n                if pair_value is not None\n                else []\n            )\n\n        values: list[float] = []\n        for item in value:\n            values.extend(_numeric_samples(item))\n        return values\n\n    return []\n\n\ndef _sample_pair_value(value: Any) -> float | None:\n    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):\n        return None\n    if len(value) != 2:\n        return None\n\n    timestamp = _as_finite_float(value[0])\n    sample = _as_finite_float(value[1])\n    if timestamp is None or sample is None:\n        return None\n    return sample\n\n\ndef _as_finite_float(value: Any) -> float | None:\n    if isinstance(value, bool) or not isinstance(value, (int, float, str)):\n        return None\n\n    try:\n        parsed = float(value)\n    except (TypeError, ValueError):\n        return None\n\n    return parsed if isfinite(parsed) else None\n\n\ndef _read_bool(\n    data: Mapping[str, Any],\n    *keys: str,\n) -> bool | None:\n    for key in keys:\n        value = data.get(key)\n        if isinstance(value, bool):\n            return value\n    return None\n\n\ndef _container_selector(\n    *,\n    target: str,\n    namespace: str,\n    cluster: str | None,\n) -> str:\n    selectors = [\n        f\'pod="{_escape_label_value(target)}"\',\n        f\'namespace="{_escape_label_value(namespace)}"\',\n        \'container!="POD"\',\n        \'container!=""\',\n        \'image!=""\',\n    ]\n    if cluster is not None:\n        selectors.append(\n            f\'cluster="{_escape_label_value(cluster)}"\'\n        )\n    return ",".join(selectors)\n\n\ndef _escape_label_value(value: str) -> str:\n    return (\n        value.replace("\\\\", "\\\\\\\\")\n        .replace(\'"\', \'\\\\"\')\n        .replace("\\n", "\\\\n")\n        .replace("\\r", "\\\\r")\n    )\n'
TEST_SOURCE = 'from __future__ import annotations\n\nfrom datetime import UTC, datetime\nfrom types import SimpleNamespace\nfrom typing import Any\n\nimport pytest\n\nfrom services.agent_runtime.app.action.models import (\n    ActionPlan,\n    ActionRisk,\n    ActionType,\n)\nfrom services.agent_runtime.app.investigation.coordinator import (\n    EvidenceDrivenInvestigationCoordinator,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    EvidenceItem,\n    IncidentHypothesis,\n    InvestigationDecision,\n    InvestigationLimits,\n    InvestigationProbe,\n    InvestigationScope,\n    InvestigationStopReason,\n)\nfrom services.agent_runtime.app.investigation.probes import (\n    InvestigationProbeResponseError,\n    ReadOnlyInvestigationProbeExecutor,\n)\nfrom services.agent_runtime.app.investigation.reasoner import (\n    BaseInvestigationReasoner,\n)\nfrom services.agent_runtime.app.verification.collector import (\n    VerificationEvaluation,\n    VerificationEvidenceCollector,\n    VerificationProbe,\n)\nfrom services.agent_runtime.app.verification.models import (\n    VerificationSource,\n)\nfrom services.agent_runtime.app.verification.profiles import (\n    VerificationProfileFactory,\n)\n\n\nNOW = datetime(\n    2026,\n    8,\n    11,\n    5,\n    0,\n    tzinfo=UTC,\n)\n\nINCIDENT_CLUSTER = "prod-us-03"\nWRONG_CLUSTER = "prod-sg-17"\n\n\ndef scope() -> InvestigationScope:\n    return InvestigationScope(\n        alert_name="PodRestartHigh",\n        alert_message="device gateway restart rate is elevated",\n        event_occurred_at=NOW,\n        resource="device-gateway-xyz789",\n        namespace="fleet-edge",\n        cluster=INCIDENT_CLUSTER,\n    )\n\n\ndef kubernetes_result(\n    *,\n    cluster: str | None = INCIDENT_CLUSTER,\n) -> dict[str, Any]:\n    result = {\n        "success": True,\n        "source": "kubernetes",\n        "mode": "read_only",\n        "production_signal": True,\n        "observed_at": NOW.isoformat(),\n        "data": {\n            "phase": "Running",\n            "ready": True,\n            "scheduled": True,\n            "oom_killed": False,\n            "containers": [],\n        },\n    }\n\n    if cluster is not None:\n        result[\n            "cluster"\n        ] = cluster\n\n    return result\n\n\ndef prometheus_result(\n    *,\n    cluster: str | None = INCIDENT_CLUSTER,\n) -> dict[str, Any]:\n    result = {\n        "success": True,\n        "source": "prometheus",\n        "mode": "read_only",\n        "production_signal": True,\n        "observed_at": NOW.isoformat(),\n        "data": {\n            "resultType": "vector",\n            "result": [\n                {\n                    "metric": {},\n                    "value": [\n                        NOW.timestamp(),\n                        "7",\n                    ],\n                }\n            ],\n        },\n    }\n\n    if cluster is not None:\n        result[\n            "cluster"\n        ] = cluster\n\n    return result\n\n\ndef test_matching_kubernetes_and_prometheus_evidence_are_cluster_verified():\n    executor = (\n        ReadOnlyInvestigationProbeExecutor()\n    )\n\n    kubernetes = (\n        executor._normalize_kubernetes(\n            scope=scope(),\n            probe=(\n                InvestigationProbe\n                .KUBERNETES_POD_STATE\n            ),\n            result=kubernetes_result(),\n        )\n    )\n\n    prometheus = (\n        executor._normalize_prometheus(\n            scope=scope(),\n            probe=(\n                InvestigationProbe\n                .PROMETHEUS_RESTART_COUNT\n            ),\n            result=prometheus_result(),\n        )\n    )\n\n    assert (\n        kubernetes.cluster\n        == INCIDENT_CLUSTER\n    )\n\n    assert (\n        prometheus.cluster\n        == INCIDENT_CLUSTER\n    )\n\n    assert (\n        kubernetes.cluster_verified\n        is True\n    )\n\n    assert (\n        prometheus.cluster_verified\n        is True\n    )\n\n    assert (\n        kubernetes.cluster\n        == prometheus.cluster\n        == scope().cluster\n    )\n\n\n@pytest.mark.parametrize(\n    ("normalizer_name", "probe", "result"),\n    [\n        (\n            "_normalize_kubernetes",\n            InvestigationProbe.KUBERNETES_POD_STATE,\n            kubernetes_result(\n                cluster=WRONG_CLUSTER\n            ),\n        ),\n        (\n            "_normalize_prometheus",\n            InvestigationProbe.PROMETHEUS_RESTART_COUNT,\n            prometheus_result(\n                cluster=WRONG_CLUSTER\n            ),\n        ),\n    ],\n)\ndef test_explicit_tool_cluster_mismatch_is_rejected_before_evidence(\n    normalizer_name,\n    probe,\n    result,\n):\n    executor = (\n        ReadOnlyInvestigationProbeExecutor()\n    )\n\n    normalizer = getattr(\n        executor,\n        normalizer_name,\n    )\n\n    with pytest.raises(\n        InvestigationProbeResponseError,\n        match=(\n            "cluster does not match trusted scope"\n        ),\n    ):\n        normalizer(\n            scope=scope(),\n            probe=probe,\n            result=result,\n        )\n\n\ndef test_identityless_legacy_prometheus_evidence_remains_compatible_but_unverified():\n    evidence = (\n        ReadOnlyInvestigationProbeExecutor()\n        ._normalize_prometheus(\n            scope=scope(),\n            probe=(\n                InvestigationProbe\n                .PROMETHEUS_RESTART_COUNT\n            ),\n            result=prometheus_result(\n                cluster=None\n            ),\n        )\n    )\n\n    assert evidence.success is True\n    assert evidence.trusted is True\n    assert evidence.cluster is None\n    assert (\n        evidence.cluster_verified\n        is False\n    )\n\n\ndef test_cluster_verified_model_requires_trusted_cluster_identity():\n    with pytest.raises(\n        ValueError,\n        match=(\n            "cluster-verified evidence requires a cluster identity"\n        ),\n    ):\n        EvidenceItem(\n            probe=(\n                InvestigationProbe\n                .KUBERNETES_POD_STATE\n            ),\n            source="kubernetes",\n            success=True,\n            trusted=True,\n            production_signal=True,\n            reliability=1.0,\n            observed_at=NOW,\n            cluster_verified=True,\n            facts={},\n        )\n\n\nclass TwoStepReasoner(\n    BaseInvestigationReasoner\n):\n    def __init__(\n        self,\n    ) -> None:\n        self.calls = 0\n\n    async def decide(\n        self,\n        scope,\n        state,\n    ) -> InvestigationDecision:\n        self.calls += 1\n\n        hypothesis = IncidentHypothesis(\n            hypothesis_id="cluster-contract",\n            cause="collect bounded evidence",\n            confidence=0.1,\n            supporting_evidence_ids=[],\n            conflicting_evidence_ids=[],\n            missing_evidence=[\n                "root cause evidence"\n            ],\n            optional_evidence=[],\n        )\n\n        if self.calls == 1:\n            return InvestigationDecision(\n                hypotheses=[\n                    hypothesis\n                ],\n                rationale_summary=(\n                    "collect Kubernetes evidence"\n                ),\n                stop=False,\n                next_probe=(\n                    InvestigationProbe\n                    .KUBERNETES_POD_STATE\n                ),\n            )\n\n        return InvestigationDecision(\n            hypotheses=[\n                hypothesis\n            ],\n            rationale_summary=(\n                "stop after cluster integrity check"\n            ),\n            stop=True,\n            stop_reason=(\n                InvestigationStopReason\n                .INSUFFICIENT_EVIDENCE\n            ),\n            next_probe=None,\n            conclusion=None,\n        )\n\n\nclass ForgedMismatchProbeExecutor:\n    async def collect(\n        self,\n        context,\n        scope,\n        probe,\n    ) -> EvidenceItem:\n        return EvidenceItem(\n            evidence_id="forged-cluster-evidence",\n            probe=probe,\n            source="forged-read-tool",\n            success=True,\n            trusted=True,\n            production_signal=True,\n            reliability=1.0,\n            observed_at=NOW,\n            cluster=WRONG_CLUSTER,\n            cluster_verified=False,\n            facts={\n                "ready": True,\n            },\n        )\n\n\n@pytest.mark.asyncio\nasync def test_coordinator_replaces_custom_executor_mismatch_before_reasoner_reuse():\n    event = SimpleNamespace(\n        header=SimpleNamespace(\n            occurred_at=NOW,\n        ),\n        signal=SimpleNamespace(\n            name="PodRestartHigh",\n            message="restart rate elevated",\n        ),\n        resources=[\n            SimpleNamespace(\n                name=(\n                    "device-gateway-xyz789"\n                ),\n                namespace="fleet-edge",\n                cluster=INCIDENT_CLUSTER,\n            )\n        ],\n    )\n\n    context = SimpleNamespace(\n        event=event,\n        metadata={},\n    )\n\n    coordinator = (\n        EvidenceDrivenInvestigationCoordinator(\n            reasoner=TwoStepReasoner(),\n            probe_executor=(\n                ForgedMismatchProbeExecutor()\n            ),\n            limits=InvestigationLimits(\n                max_iterations=3,\n                max_tool_calls=2,\n                timeout_seconds=10,\n            ),\n            utc_clock=lambda: NOW,\n        )\n    )\n\n    result = await coordinator.investigate(\n        context\n    )\n\n    assert len(\n        result.evidence\n    ) == 1\n\n    rejected = result.evidence[\n        0\n    ]\n\n    assert rejected.success is False\n    assert rejected.trusted is False\n\n    assert rejected.error_code == (\n        "ClusterEvidenceMismatch"\n    )\n\n    assert rejected.cluster is None\n    assert rejected.facts == {}\n\n\ndef build_plan() -> ActionPlan:\n    return ActionPlan(\n        type=(\n            ActionType\n            .INCREASE_MEMORY_LIMIT\n        ),\n        target=(\n            "device-gateway-xyz789"\n        ),\n        risk=ActionRisk.MEDIUM,\n        metadata={},\n    )\n\n\ndef test_verification_profile_routes_cluster_to_kubernetes_and_prometheus_tools():\n    profile = (\n        VerificationProfileFactory()\n        .create(\n            build_plan(),\n            namespace="fleet-edge",\n            cluster=INCIDENT_CLUSTER,\n        )\n    )\n\n    assert profile.cluster == (\n        INCIDENT_CLUSTER\n    )\n\n    for probe in profile.probes:\n        assert (\n            probe.arguments.get(\n                "cluster"\n            )\n            == INCIDENT_CLUSTER\n        )\n\n\nclass OneResponseTools:\n    def __init__(\n        self,\n        response,\n    ) -> None:\n        self.response = response\n        self.calls = []\n\n    async def call(\n        self,\n        name,\n        context=None,\n        **kwargs,\n    ):\n        self.calls.append(\n            {\n                "name": name,\n                "kwargs": kwargs,\n            }\n        )\n\n        return self.response\n\n\ndef metric_probe(\n    evaluator,\n) -> VerificationProbe:\n    return VerificationProbe(\n        name="cluster_metric",\n        source=VerificationSource.METRIC,\n        tool="prometheus",\n        provider="prometheus",\n        arguments={\n            "query": (\n                \'up{cluster="prod-us-03"}\'\n            ),\n            "cluster": INCIDENT_CLUSTER,\n        },\n        evaluator=evaluator,\n        required=True,\n    )\n\n\n@pytest.mark.asyncio\nasync def test_verification_collector_rejects_explicit_cluster_mismatch_before_evaluator():\n    evaluator_calls = []\n\n    def evaluator(\n        evidence,\n    ):\n        evaluator_calls.append(\n            evidence\n        )\n\n        return VerificationEvaluation(\n            passed=True\n        )\n\n    tools = OneResponseTools(\n        prometheus_result(\n            cluster=WRONG_CLUSTER\n        )\n    )\n\n    collector = (\n        VerificationEvidenceCollector(\n            tools=tools,\n            clock=lambda: NOW,\n        )\n    )\n\n    check = await collector.collect_one(\n        metric_probe(\n            evaluator\n        )\n    )\n\n    assert check.passed is None\n\n    assert (\n        check.metadata[\n            "trusted"\n        ]\n        is False\n    )\n\n    assert (\n        "cluster does not match expected scope"\n        in check.metadata[\n            "rejection_reasons"\n        ]\n    )\n\n    assert evaluator_calls == []\n\n\n@pytest.mark.asyncio\nasync def test_verification_collector_records_matching_cluster_as_verified():\n    def evaluator(\n        evidence,\n    ):\n        return VerificationEvaluation(\n            passed=True,\n            observed_value=7.0,\n            expected_value=7.0,\n            message="cluster matched",\n        )\n\n    collector = (\n        VerificationEvidenceCollector(\n            tools=OneResponseTools(\n                prometheus_result()\n            ),\n            clock=lambda: NOW,\n        )\n    )\n\n    check = await collector.collect_one(\n        metric_probe(\n            evaluator\n        )\n    )\n\n    assert check.passed is True\n\n    assert (\n        check.metadata[\n            "evidence_cluster"\n        ]\n        == INCIDENT_CLUSTER\n    )\n\n    assert (\n        check.metadata[\n            "cluster_verified"\n        ]\n        is True\n    )\n\n\n@pytest.mark.asyncio\nasync def test_verification_legacy_identityless_result_is_visible_as_unverified():\n    def evaluator(\n        evidence,\n    ):\n        return VerificationEvaluation(\n            passed=True,\n            message="legacy compatible",\n        )\n\n    collector = (\n        VerificationEvidenceCollector(\n            tools=OneResponseTools(\n                prometheus_result(\n                    cluster=None\n                )\n            ),\n            clock=lambda: NOW,\n        )\n    )\n\n    check = await collector.collect_one(\n        metric_probe(\n            evaluator\n        )\n    )\n\n    assert check.passed is True\n\n    assert (\n        check.metadata[\n            "evidence_cluster"\n        ]\n        is None\n    )\n\n    assert (\n        check.metadata[\n            "cluster_verified"\n        ]\n        is False\n    )\n'


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


def raw_sha256(
    path: Path,
) -> str:
    return hashlib.sha256(
        path.read_bytes()
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


def verify_raw_hash(
    *,
    root: Path,
    relative: str,
) -> None:
    path = root / relative

    if not path.exists():
        raise RuntimeError(
            f"Required current file is missing: {relative}"
        )

    actual = raw_sha256(
        path
    )

    expected = EXPECTED_RAW_HASHES[
        relative
    ]

    if actual != expected:
        raise RuntimeError(
            (
                f"{relative} changed after the reviewed Evidence snapshot. "
                f"expected_raw_sha256={expected} actual_raw_sha256={actual}. "
                "Refusing stale Cross-Source Cluster Evidence installation."
            )
        )


def require_tests(
    *,
    root: Path,
    relative_paths: list[str],
    label: str,
) -> list[str]:
    missing = [
        relative
        for relative in relative_paths
        if not (
            root
            / relative
        ).exists()
    ]

    if missing:
        raise RuntimeError(
            (
                f"Required {label} tests are missing: "
                + ", ".join(
                    missing
                )
            )
        )

    return relative_paths


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

    coordinator_file = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "investigation"
        / "coordinator.py"
    )

    collector_file = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "verification"
        / "collector.py"
    )

    profiles_file = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "verification"
        / "profiles.py"
    )

    test_file = (
        root
        / "services"
        / "agent_runtime"
        / "tests"
        / "test_cross_source_cluster_evidence_consistency.py"
    )

    sources = {
        models_file: MODELS_SOURCE,
        probes_file: PROBES_SOURCE,
        coordinator_file: COORDINATOR_SOURCE,
        collector_file: COLLECTOR_SOURCE,
        profiles_file: PROFILES_SOURCE,
        test_file: TEST_SOURCE,
    }

    targets = list(
        sources
    )

    preexisting = {
        path: path.exists()
        for path in targets
    }

    backups = []

    report = [
        "Cross-Source Cluster Evidence Consistency Contract v1",
        f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
        "",
        "Trust boundary:",
        "- Investigation validates explicit Tool result cluster before EvidenceItem creation",
        "- Coordinator independently rejects explicit mismatched custom/replay EvidenceItem before Reasoner reuse",
        "- VerificationEvidenceCollector independently validates explicit result cluster before evaluator execution",
        "",
        "Evidence identity:",
        "- EvidenceItem gains optional cluster + cluster_verified",
        "- exact scope/result cluster match -> cluster_verified=True",
        "- explicit mismatch -> rejected",
        "- missing legacy cluster -> compatible but cluster_verified=False",
        "",
        "Production multi-cluster behavior:",
        "- Kubernetes live/change Tools already return cluster identity",
        "- Multi-Cluster Prometheus Router returns selected cluster identity",
        "- therefore routed production Kubernetes + Prometheus evidence can be proven against one Incident scope",
        "",
        "Verification routing:",
        "- Prometheus verification probes now pass cluster as a Tool routing argument in addition to bounded PromQL labels",
        "- Kubernetes verification routing remains unchanged",
        "",
        "Compatibility:",
        "- historical/evaluation/single-cluster result shapes without cluster remain accepted but visibly unverified",
        "- existing configs remain unchanged",
        "- no Router/Connection Factory protocol change",
        "",
        "Authority:",
        "- evidence validation only",
        "- no Action / Approval / remediation authority",
        "- no mutating Kubernetes/Prometheus call",
        "",
        "Installer sends no real Kubernetes/Prometheus/LLM request.",
    ]

    try:
        section(
            report,
            "CURRENT RAW HASH PREFLIGHT",
        )

        for relative in EXPECTED_RAW_HASHES:
            verify_raw_hash(
                root=root,
                relative=relative,
            )

            report.append(
                relative
                + "="
                + EXPECTED_RAW_HASHES[
                    relative
                ]
            )

        if test_file.exists():
            raise RuntimeError(
                "Cross-source consistency test already exists; refusing to overwrite an unreviewed test"
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
                "Cross-Source Cluster Evidence syntax failed"
            )

        focused_paths = require_tests(
            root=root,
            label="Evidence consistency focused",
            relative_paths=[
                (
                    "services/agent_runtime/tests/"
                    "test_cross_source_cluster_evidence_consistency.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_models.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_probes.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_evidence_consistency.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_verification_collector.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_verification_profiles.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_production_scope_integrity.py"
                ),
            ],
        )

        focused = run_command(
            root=root,
            name="Cross-Source Evidence focused suite",
            command=[
                "uv",
                "run",
                "pytest",
                *focused_paths,
                "-q",
            ],
        )

        add_command(
            report,
            focused,
        )

        if focused.returncode != 0:
            raise RuntimeError(
                "Cross-Source Evidence focused tests failed"
            )

        multi_cluster_paths = require_tests(
            root=root,
            label="Multi-Cluster routing",
            relative_paths=[
                (
                    "services/agent_runtime/tests/"
                    "test_multi_cluster_kubernetes_router.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_multi_cluster_prometheus_router.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_multi_cluster_connection_config.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_multi_cluster_prometheus_connection_config.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_production_tool_contract.py"
                ),
            ],
        )

        multi_cluster = run_command(
            root=root,
            name="Multi-Cluster routing compatibility suite",
            command=[
                "uv",
                "run",
                "pytest",
                *multi_cluster_paths,
                "-q",
            ],
        )

        add_command(
            report,
            multi_cluster,
        )

        if multi_cluster.returncode != 0:
            raise RuntimeError(
                "Cross-Source Evidence Multi-Cluster compatibility failed"
            )

        investigation_paths = require_tests(
            root=root,
            label="Investigation compatibility",
            relative_paths=[
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
            ],
        )

        investigation = run_command(
            root=root,
            name="Investigation / Historical compatibility suite",
            command=[
                "uv",
                "run",
                "pytest",
                *investigation_paths,
                "-q",
            ],
        )

        add_command(
            report,
            investigation,
        )

        if investigation.returncode != 0:
            raise RuntimeError(
                "Cross-Source Evidence Investigation compatibility failed"
            )

        verification_paths = require_tests(
            root=root,
            label="Verification compatibility",
            relative_paths=[
                (
                    "services/agent_runtime/tests/"
                    "test_verification_coordinator.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_verification_fail_closed_e2e.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_runtime_verification_profile_wiring.py"
                ),
            ],
        )

        verification = run_command(
            root=root,
            name="Verification compatibility suite",
            command=[
                "uv",
                "run",
                "pytest",
                *verification_paths,
                "-q",
            ],
        )

        add_command(
            report,
            verification,
        )

        if verification.returncode != 0:
            raise RuntimeError(
                "Cross-Source Evidence Verification compatibility failed"
            )

        preflight = run_command(
            root=root,
            name="Cluster evidence architecture preflight",
            command=[
                "uv",
                "run",
                "python",
                "-c",
                (
                    "from pathlib import Path; "
                    "m=Path(r'services/agent_runtime/app/investigation/models.py').read_text(encoding='utf-8'); "
                    "p=Path(r'services/agent_runtime/app/investigation/probes.py').read_text(encoding='utf-8'); "
                    "c=Path(r'services/agent_runtime/app/investigation/coordinator.py').read_text(encoding='utf-8'); "
                    "vc=Path(r'services/agent_runtime/app/verification/collector.py').read_text(encoding='utf-8'); "
                    "vp=Path(r'services/agent_runtime/app/verification/profiles.py').read_text(encoding='utf-8'); "
                    "print('evidence_cluster_field='+str('cluster: ShortText | None = None' in m)); "
                    "print('cluster_verified_field='+str('cluster_verified: bool = False' in m)); "
                    "print('probe_expected_cluster_calls='+str(p.count('expected_cluster=scope.cluster'))); "
                    "print('probe_mismatch_guard='+str('cluster does not match trusted scope' in p)); "
                    "print('coordinator_guard='+str('ClusterEvidenceMismatch' in c)); "
                    "print('verification_mismatch_guard='+str('cluster does not match expected scope' in vc)); "
                    "print('verification_prometheus_routes_cluster='+str(vp.count('] = cluster')>=3)); "
                    "assert 'cluster: ShortText | None = None' in m; "
                    "assert 'cluster_verified: bool = False' in m; "
                    "assert p.count('expected_cluster=scope.cluster') == 5; "
                    "assert 'cluster does not match trusted scope' in p; "
                    "assert 'ClusterEvidenceMismatch' in c; "
                    "assert 'cluster does not match expected scope' in vc; "
                    "assert vp.count('] = cluster') >= 3"
                ),
            ],
        )

        add_command(
            report,
            preflight,
        )

        if preflight.returncode != 0:
            raise RuntimeError(
                "Cross-Source Evidence architecture preflight failed"
            )

        authority = run_command(
            root=root,
            name="Read-only evidence authority boundary",
            command=[
                "uv",
                "run",
                "python",
                "-c",
                (
                    "from pathlib import Path; "
                    "files=["
                    "Path(r'services/agent_runtime/app/investigation/probes.py'),"
                    "Path(r'services/agent_runtime/app/verification/collector.py'),"
                    "Path(r'services/agent_runtime/app/verification/profiles.py')"
                    "]; "
                    "s='\\n'.join(x.read_text(encoding='utf-8') for x in files); "
                    "bad=[x for x in ['ActionRuntime','ApprovalService','KubernetesProductionExecutor','.post(','.patch(','.put(','.delete('] if x in s]; "
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
                "Cross-Source Evidence authority boundary failed"
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
                "Cross-Source Cluster Evidence Consistency Contract v1 is installed.",
                "",
                "Guarantee:",
                "- explicit Kubernetes/Prometheus cluster mismatch cannot become trusted Investigation evidence",
                "- custom/replay ProbeExecutor mismatch is stripped into failed fact-free evidence before Reasoner reuse",
                "- Verification rejects explicit cluster mismatch before evaluator execution",
                "- matching routed evidence records cluster_verified=True",
                "- identity-less legacy evidence remains compatible but cluster_verified=False",
                "",
                "Important interpretation:",
                "- routed multi-cluster production evidence is fully cluster-verifiable because current Kubernetes and Prometheus routers return cluster identity",
                "- legacy identity-less evidence is not falsely labeled verified",
                "",
                "Next recommended step:",
                "- Production Shadow Cluster-Verified Evidence Policy v1: when multi-cluster connections are enabled, require required RCA/Verification evidence to be cluster_verified=True instead of accepting legacy identity-less compatibility.",
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
            "CROSS-SOURCE CLUSTER EVIDENCE CONSISTENCY CONTRACT V1 PASSED"
        )
        print("=" * 72)
        print()
        print(
            "No real LLM/Kubernetes/Prometheus request was sent."
        )
        print()
        print(
            "Upload only:"
        )
        print(
            after
        )

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
                    + (
                        f"{type(rollback_exc).__name__}: "
                        f"{rollback_exc}"
                    )
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
                        + (
                            f"{type(rollback_exc).__name__}: "
                            f"{rollback_exc}"
                        )
                    )

        write_text(
            error,
            "\n".join(
                [
                    "Cross-Source Cluster Evidence Consistency Contract v1 FAILED",
                    (
                        "GeneratedAt: "
                        + datetime.now().astimezone().isoformat()
                    ),
                    "",
                    (
                        f"{type(exc).__name__}: {exc}"
                    ),
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
            "CROSS-SOURCE CLUSTER EVIDENCE CONSISTENCY CONTRACT V1 FAILED"
        )
        print("=" * 72)
        print()
        print(
            "Modified files were rolled back where possible."
        )
        print()
        print(
            "Upload only:"
        )
        print(
            error
        )

        return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
