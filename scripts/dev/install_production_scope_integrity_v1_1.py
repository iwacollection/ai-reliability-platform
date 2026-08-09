from __future__ import annotations

import hashlib
import shutil
import subprocess
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


VERSION = "production-scope-integrity-v1.1"

AFTER_NAME = (
    "production_scope_integrity_v1_1_after.txt"
)

ERROR_NAME = (
    "production_scope_integrity_v1_1_error.txt"
)

EXPECTED_HASHES = {'services/agent_runtime/app/investigation/probes.py': 'c85c8850bdb88c8f1d30302a5c3288659e5051849966d991db18d6bbc1391b39', 'services/agent_runtime/app/tools/kubernetes/tool.py': 'f9ceb57e457ad68a25c7c4da03476cad552cd45e484310cf02dd9993197f8424', 'services/agent_runtime/tests/test_investigation_probes.py': '8778d3e43121e230f65ba9f270222735b0343efd81b414c22c19660d80dcefa7', 'services/agent_runtime/tests/test_investigation_logs.py': 'ee249ba7750ba652938c6127545fc7adcf26abe0f9084c6a832bda26e0c5cebb'}

PROBES_SOURCE = 'import re\nfrom collections.abc import Mapping\nfrom datetime import UTC, datetime\nfrom math import isfinite\nfrom typing import Any\n\nfrom services.agent_runtime.app.investigation.evidence_time import (\n    InvestigationEvidenceTimeError,\n    InvestigationEvidenceTimePolicy,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    EvidenceItem,\n    InvestigationProbe,\n    InvestigationScope,\n    default_investigation_probes,\n)\n\n\nclass InvestigationProbeError(RuntimeError):\n    """\n    Base error for the bounded read-only probe adapter.\n    """\n\n\nclass InvestigationToolUnavailableError(\n    InvestigationProbeError\n):\n    """\n    Runtime ToolManager is unavailable.\n    """\n\n\nclass InvestigationProbeResponseError(\n    InvestigationProbeError\n):\n    """\n    A read-only tool returned evidence that cannot cross the\n    Investigation trust boundary.\n    """\n\n\nclass ReadOnlyInvestigationProbeExecutor:\n    """\n    Translate symbolic Investigation probes into exact read-only tool calls.\n\n    The reasoner selects only an InvestigationProbe enum value.\n\n    This adapter owns:\n\n    - fixed Kubernetes read-only actions;\n    - fixed bounded previous-container log collection;\n    - fixed Prometheus query templates;\n    - provider/source validation;\n    - read-only mode validation;\n    - production-signal validation;\n    - observed-at validation;\n    - bounded evidence normalization.\n\n    The reasoner cannot provide Kubernetes verbs, resource kinds, PromQL,\n    URLs, credentials or raw tool arguments.\n    """\n\n    _TRUSTED_MODE = "read_only"\n    _MAX_LOG_TOOL_CHARS = 4000\n    _MAX_LOG_EVIDENCE_CHARS = 1800\n    _MAX_LOG_LINES = 80\n\n    def __init__(\n        self,\n        time_policy: (\n            InvestigationEvidenceTimePolicy\n            | None\n        ) = None,\n    ) -> None:\n        self.time_policy = (\n            time_policy\n            if time_policy is not None\n            else InvestigationEvidenceTimePolicy()\n        )\n\n    @staticmethod\n    def available_probes(\n        context,\n    ) -> list[InvestigationProbe]:\n        probes = default_investigation_probes()\n\n        tools = getattr(\n            context,\n            "tools",\n            None,\n        )\n\n        registry = getattr(\n            tools,\n            "registry",\n            None,\n        )\n\n        getter = getattr(\n            registry,\n            "get",\n            None,\n        )\n\n        if not callable(\n            getter\n        ):\n            return probes\n\n        try:\n            change_tool = getter(\n                "kubernetes_change"\n            )\n        except KeyError:\n            return probes\n\n        if (\n            getattr(\n                change_tool,\n                "is_available",\n                True,\n            )\n            is not True\n        ):\n            return probes\n\n        probes.append(\n            InvestigationProbe.KUBERNETES_WORKLOAD_CHANGE\n        )\n\n        probes.append(\n            InvestigationProbe.KUBERNETES_CONFIG_CHANGE\n        )\n\n        return probes\n\n    async def collect(\n        self,\n        context,\n        scope: InvestigationScope,\n        probe: InvestigationProbe,\n    ) -> EvidenceItem:\n        tools = getattr(\n            context,\n            "tools",\n            None,\n        )\n\n        if tools is None:\n            raise InvestigationToolUnavailableError(\n                "Runtime tools are unavailable"\n            )\n\n        if (\n            probe\n            == InvestigationProbe.KUBERNETES_POD_STATE\n        ):\n            result = await tools.call(\n                "kubernetes",\n                context=context,\n                action="describe",\n                resource="pod",\n                target=scope.resource,\n                namespace=scope.namespace,\n                cluster=scope.cluster,\n            )\n\n            return self._normalize_kubernetes(\n                scope=scope,\n                probe=probe,\n                result=result,\n            )\n\n        if (\n            probe\n            == InvestigationProbe.KUBERNETES_PREVIOUS_CONTAINER_LOGS\n        ):\n            result = await tools.call(\n                "kubernetes",\n                context=context,\n                action="previous_logs",\n                resource="pod",\n                target=scope.resource,\n                namespace=scope.namespace,\n                cluster=scope.cluster,\n            )\n\n            return self._normalize_kubernetes_logs(\n                scope=scope,\n                probe=probe,\n                result=result,\n            )\n\n        if (\n            probe\n            == InvestigationProbe.KUBERNETES_WORKLOAD_CHANGE\n        ):\n            result = await tools.call(\n                "kubernetes_change",\n                context=context,\n                target=scope.resource,\n                namespace=scope.namespace,\n                cluster=scope.cluster,\n                incident_time=(\n                    scope.event_occurred_at.isoformat()\n                    if scope.event_occurred_at\n                    is not None\n                    else None\n                ),\n                view="workload",\n            )\n\n            return self._normalize_kubernetes_change(\n                scope=scope,\n                probe=probe,\n                result=result,\n            )\n\n        if (\n            probe\n            == InvestigationProbe.KUBERNETES_CONFIG_CHANGE\n        ):\n            result = await tools.call(\n                "kubernetes_change",\n                context=context,\n                target=scope.resource,\n                namespace=scope.namespace,\n                cluster=scope.cluster,\n                incident_time=(\n                    scope.event_occurred_at.isoformat()\n                    if scope.event_occurred_at\n                    is not None\n                    else None\n                ),\n                view="config",\n            )\n\n            return self._normalize_kubernetes_config_change(\n                scope=scope,\n                probe=probe,\n                result=result,\n            )\n\n        query = self._prometheus_query(\n            scope=scope,\n            probe=probe,\n        )\n\n        query_time = self.time_policy.query_time(\n            scope=scope,\n            probe=probe,\n        )\n\n        call_arguments = {\n            "query": query,\n        }\n\n        if query_time is not None:\n            call_arguments["time"] = (\n                query_time\n            )\n\n        result = await tools.call(\n            "prometheus",\n            context=context,\n            **call_arguments,\n        )\n\n        return self._normalize_prometheus(\n            scope=scope,\n            probe=probe,\n            result=result,\n        )\n\n    @classmethod\n    def _prometheus_query(\n        cls,\n        scope: InvestigationScope,\n        probe: InvestigationProbe,\n    ) -> str:\n        labels = [\n            (\n                \'pod="\'\n                f\'{cls._escape_label(scope.resource)}\'\n                \'"\'\n            ),\n            (\n                \'namespace="\'\n                f\'{cls._escape_label(scope.namespace)}\'\n                \'"\'\n            ),\n        ]\n\n        if scope.cluster:\n            labels.append(\n                \'cluster="\'\n                f\'{cls._escape_label(scope.cluster)}\'\n                \'"\'\n            )\n\n        selector = ",".join(\n            labels\n        )\n\n        if (\n            probe\n            == InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET\n        ):\n            return (\n                "sum(container_memory_working_set_bytes{"\n                f\'{selector},container!="POD",container!="",image!=""\'\n                "})"\n            )\n\n        if (\n            probe\n            == InvestigationProbe.PROMETHEUS_MEMORY_LIMIT\n        ):\n            return (\n                "sum(kube_pod_container_resource_limits{"\n                f\'{selector},resource="memory",unit="byte"\'\n                "})"\n            )\n\n        if (\n            probe\n            == InvestigationProbe.PROMETHEUS_RESTART_COUNT\n        ):\n            return (\n                "sum(kube_pod_container_status_restarts_total{"\n                f"{selector}"\n                "})"\n            )\n\n        raise InvestigationProbeError(\n            "Unsupported investigation probe"\n        )\n\n    def _normalize_kubernetes(\n        self,\n        scope: InvestigationScope,\n        probe: InvestigationProbe,\n        result: Any,\n    ) -> EvidenceItem:\n        data, observed_at = (\n            self._validate_tool_evidence(\n                result=result,\n                expected_source="kubernetes",\n            )\n        )\n\n        if "phase" not in data:\n            raise InvestigationProbeResponseError(\n                "Kubernetes evidence phase is missing"\n            )\n\n        containers = data.get(\n            "containers"\n        )\n\n        if not isinstance(\n            containers,\n            list,\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes evidence containers are invalid"\n            )\n\n        restart_counts: list[int] = []\n        state_reasons: set[str] = set()\n        termination_reasons: set[str] = set()\n\n        for container in containers[:32]:\n            if not isinstance(\n                container,\n                Mapping,\n            ):\n                continue\n\n            restart_count = container.get(\n                "restart_count"\n            )\n\n            if isinstance(\n                restart_count,\n                int,\n            ):\n                restart_counts.append(\n                    restart_count\n                )\n\n            state_reason = container.get(\n                "state_reason"\n            )\n\n            if (\n                isinstance(\n                    state_reason,\n                    str,\n                )\n                and state_reason\n            ):\n                state_reasons.add(\n                    state_reason[:128]\n                )\n\n            termination_reason = container.get(\n                "last_termination_reason"\n            )\n\n            if (\n                isinstance(\n                    termination_reason,\n                    str,\n                )\n                and termination_reason\n            ):\n                termination_reasons.add(\n                    termination_reason[:128]\n                )\n\n        facts = {\n            "temporal_basis": (\n                self.time_policy.temporal_basis(\n                    scope=scope,\n                    probe=probe,\n                )\n            ),\n            "phase": cls_scalar(\n                data.get("phase")\n            ),\n            "ready": cls_scalar(\n                data.get("ready")\n            ),\n            "scheduled": cls_scalar(\n                data.get("scheduled")\n            ),\n            "oom_killed": cls_scalar(\n                data.get("oom_killed")\n            ),\n            "max_restart_count": (\n                max(restart_counts)\n                if restart_counts\n                else None\n            ),\n            "state_reasons": (\n                ",".join(\n                    sorted(\n                        state_reasons\n                    )\n                )\n                if state_reasons\n                else None\n            ),\n            "last_termination_reasons": (\n                ",".join(\n                    sorted(\n                        termination_reasons\n                    )\n                )\n                if termination_reasons\n                else None\n            ),\n        }\n\n        return EvidenceItem(\n            probe=probe,\n            source="kubernetes",\n            success=True,\n            trusted=True,\n            production_signal=True,\n            reliability=1.0,\n            observed_at=observed_at,\n            facts=facts,\n        )\n\n    def _normalize_kubernetes_logs(\n        self,\n        scope: InvestigationScope,\n        probe: InvestigationProbe,\n        result: Any,\n    ) -> EvidenceItem:\n        data, observed_at = (\n            self._validate_tool_evidence(\n                result=result,\n                expected_source="kubernetes",\n            )\n        )\n\n        if (\n            data.get(\n                "previous"\n            )\n            is not True\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes log evidence is not previous-container output"\n            )\n\n        container_value = data.get(\n            "container_name"\n        )\n\n        if not isinstance(\n            container_value,\n            str,\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes log evidence container is invalid"\n            )\n\n        container_name = (\n            container_value\n            .strip()\n        )\n\n        if (\n            not container_name\n            or len(\n                container_name\n            )\n            > 128\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes log evidence container is invalid"\n            )\n\n        line_count = data.get(\n            "line_count"\n        )\n\n        if (\n            not isinstance(\n                line_count,\n                int,\n            )\n            or isinstance(\n                line_count,\n                bool,\n            )\n            or line_count < 0\n            or line_count > self._MAX_LOG_LINES\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes log evidence line count is invalid"\n            )\n\n        truncated = data.get(\n            "truncated"\n        )\n\n        if not isinstance(\n            truncated,\n            bool,\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes log evidence truncation flag is invalid"\n            )\n\n        redaction_count = data.get(\n            "redaction_count"\n        )\n\n        if (\n            not isinstance(\n                redaction_count,\n                int,\n            )\n            or isinstance(\n                redaction_count,\n                bool,\n            )\n            or redaction_count < 0\n            or redaction_count > 10000\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes log evidence redaction count is invalid"\n            )\n\n        excerpt_value = data.get(\n            "excerpt"\n        )\n\n        if not isinstance(\n            excerpt_value,\n            str,\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes log evidence excerpt is invalid"\n            )\n\n        if len(\n            excerpt_value\n        ) > self._MAX_LOG_TOOL_CHARS:\n            raise InvestigationProbeResponseError(\n                "Kubernetes log evidence excerpt is too large"\n            )\n\n        excerpt, local_redactions = (\n            redact_log_excerpt(\n                excerpt_value\n            )\n        )\n\n        redaction_count = (\n            redaction_count\n            + local_redactions\n        )\n\n        evidence_truncated = (\n            len(\n                excerpt\n            )\n            > self._MAX_LOG_EVIDENCE_CHARS\n        )\n\n        if evidence_truncated:\n            excerpt = excerpt[\n                -self._MAX_LOG_EVIDENCE_CHARS:\n            ]\n\n        facts = {\n            "temporal_basis": (\n                self.time_policy.temporal_basis(\n                    scope=scope,\n                    probe=probe,\n                )\n            ),\n            "container_name": container_name,\n            "previous": True,\n            "log_line_count": line_count,\n            "tool_truncated": truncated,\n            "evidence_truncated": (\n                evidence_truncated\n            ),\n            "redaction_count": (\n                redaction_count\n            ),\n            "log_excerpt": (\n                excerpt\n                if excerpt\n                else None\n            ),\n        }\n\n        return EvidenceItem(\n            probe=probe,\n            source="kubernetes",\n            success=True,\n            trusted=True,\n            production_signal=True,\n            reliability=1.0,\n            observed_at=observed_at,\n            facts=facts,\n        )\n\n    def _normalize_kubernetes_config_change(\n        self,\n        scope: InvestigationScope,\n        probe: InvestigationProbe,\n        result: Any,\n    ) -> EvidenceItem:\n        data, observed_at = (\n            self._validate_tool_evidence(\n                result=result,\n                expected_source="kubernetes_change",\n            )\n        )\n\n        if (\n            data.get(\n                "owner_chain_verified"\n            )\n            is not True\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes config change owner chain is untrusted"\n            )\n\n        if (\n            data.get(\n                "workload_kind"\n            )\n            != "Deployment"\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes config change workload kind is unsupported"\n            )\n\n        if (\n            data.get(\n                "secret_content_queried"\n            )\n            is not False\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes config change must not query Secret content"\n            )\n\n        if (\n            data.get(\n                "configmap_content_exposed"\n            )\n            is not False\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes config change must not expose ConfigMap content"\n            )\n\n        metadata_status = data.get(\n            "current_configmap_metadata_status"\n        )\n\n        if metadata_status not in {\n            "complete",\n            "partial",\n            "unavailable",\n            "not_applicable",\n        }:\n            raise InvestigationProbeResponseError(\n                "Kubernetes config metadata status is invalid"\n            )\n\n        facts = {\n            "temporal_basis": (\n                "workload_template_config_change"\n            ),\n            "owner_chain_verified": True,\n            "deployment_name": bounded_change_text(\n                data.get(\n                    "deployment_name"\n                ),\n                required=True,\n            ),\n            "revision_before": bounded_change_int(\n                data.get(\n                    "revision_before"\n                )\n            ),\n            "revision_after": bounded_change_int(\n                data.get(\n                    "revision_after"\n                )\n            ),\n            "configmap_refs_before": bounded_change_text(\n                data.get(\n                    "configmap_refs_before"\n                ),\n                required=False,\n                max_length=1024,\n            ),\n            "configmap_refs_after": bounded_change_text(\n                data.get(\n                    "configmap_refs_after"\n                ),\n                required=False,\n                max_length=1024,\n            ),\n            "configmap_refs_changed": bounded_change_bool(\n                data.get(\n                    "configmap_refs_changed"\n                )\n            ),\n            "configmap_refs_added": bounded_change_text(\n                data.get(\n                    "configmap_refs_added"\n                ),\n                required=False,\n                max_length=1024,\n            ),\n            "configmap_refs_removed": bounded_change_text(\n                data.get(\n                    "configmap_refs_removed"\n                ),\n                required=False,\n                max_length=1024,\n            ),\n            "secret_refs_before": bounded_change_text(\n                data.get(\n                    "secret_refs_before"\n                ),\n                required=False,\n                max_length=1024,\n            ),\n            "secret_refs_after": bounded_change_text(\n                data.get(\n                    "secret_refs_after"\n                ),\n                required=False,\n                max_length=1024,\n            ),\n            "secret_refs_changed": bounded_change_bool(\n                data.get(\n                    "secret_refs_changed"\n                )\n            ),\n            "secret_refs_added": bounded_change_text(\n                data.get(\n                    "secret_refs_added"\n                ),\n                required=False,\n                max_length=1024,\n            ),\n            "secret_refs_removed": bounded_change_text(\n                data.get(\n                    "secret_refs_removed"\n                ),\n                required=False,\n                max_length=1024,\n            ),\n            "config_annotation_keys_before": bounded_change_text(\n                data.get(\n                    "config_annotation_keys_before"\n                ),\n                required=False,\n                max_length=1024,\n            ),\n            "config_annotation_keys_after": bounded_change_text(\n                data.get(\n                    "config_annotation_keys_after"\n                ),\n                required=False,\n                max_length=1024,\n            ),\n            "config_annotation_fingerprint_before": bounded_change_text(\n                data.get(\n                    "config_annotation_fingerprint_before"\n                ),\n                required=False,\n                max_length=128,\n            ),\n            "config_annotation_fingerprint_after": bounded_change_text(\n                data.get(\n                    "config_annotation_fingerprint_after"\n                ),\n                required=False,\n                max_length=128,\n            ),\n            "config_annotation_changed": bounded_change_bool(\n                data.get(\n                    "config_annotation_changed"\n                )\n            ),\n            "current_configmap_metadata_status": (\n                metadata_status\n            ),\n            "current_configmap_metadata_summary": bounded_change_text(\n                data.get(\n                    "current_configmap_metadata_summary"\n                ),\n                required=False,\n                max_length=1536,\n            ),\n            "current_configmap_metadata_error": bounded_change_text(\n                data.get(\n                    "current_configmap_metadata_error"\n                ),\n                required=False,\n                max_length=512,\n            ),\n            "secret_content_queried": False,\n            "configmap_content_exposed": False,\n        }\n\n        return EvidenceItem(\n            probe=probe,\n            source="kubernetes_change",\n            success=True,\n            trusted=True,\n            production_signal=True,\n            reliability=1.0,\n            observed_at=observed_at,\n            facts=facts,\n        )\n\n    def _normalize_kubernetes_change(\n        self,\n        scope: InvestigationScope,\n        probe: InvestigationProbe,\n        result: Any,\n    ) -> EvidenceItem:\n        data, observed_at = (\n            self._validate_tool_evidence(\n                result=result,\n                expected_source="kubernetes_change",\n            )\n        )\n\n        if (\n            data.get(\n                "owner_chain_verified"\n            )\n            is not True\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes change owner chain is untrusted"\n            )\n\n        if (\n            data.get(\n                "workload_kind"\n            )\n            != "Deployment"\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes change workload kind is unsupported"\n            )\n\n        deployment_name = bounded_change_text(\n            data.get(\n                "deployment_name"\n            ),\n            required=True,\n        )\n\n        rollout_started_at = bounded_change_text(\n            data.get(\n                "rollout_started_at"\n            ),\n            required=False,\n        )\n\n        rollout_offset_seconds = None\n        recent_rollout_before_incident = None\n\n        if (\n            rollout_started_at is not None\n            and scope.event_occurred_at\n            is not None\n        ):\n            rollout_time = parse_observed_at(\n                rollout_started_at\n            )\n\n            rollout_offset_seconds = (\n                scope.event_occurred_at\n                .astimezone(\n                    UTC\n                )\n                - rollout_time\n            ).total_seconds()\n\n            recent_rollout_before_incident = (\n                0.0\n                <= rollout_offset_seconds\n                <= 1800.0\n            )\n\n        facts = {\n            "temporal_basis": (\n                "workload_change_history"\n            ),\n            "owner_chain_verified": True,\n            "deployment_name": (\n                deployment_name\n            ),\n            "revision_before": bounded_change_int(\n                data.get(\n                    "revision_before"\n                )\n            ),\n            "revision_after": bounded_change_int(\n                data.get(\n                    "revision_after"\n                )\n            ),\n            "revision_changed": bounded_change_bool(\n                data.get(\n                    "revision_changed"\n                )\n            ),\n            "image_before": bounded_change_text(\n                data.get(\n                    "image_before"\n                ),\n                required=False,\n            ),\n            "image_after": bounded_change_text(\n                data.get(\n                    "image_after"\n                ),\n                required=False,\n            ),\n            "image_changed": bounded_change_bool(\n                data.get(\n                    "image_changed"\n                )\n            ),\n            "rollout_started_at": (\n                rollout_started_at\n            ),\n            "rollout_offset_seconds": (\n                rollout_offset_seconds\n            ),\n            "recent_rollout_before_incident": (\n                recent_rollout_before_incident\n            ),\n            "generation": bounded_change_int(\n                data.get(\n                    "generation"\n                )\n            ),\n            "observed_generation": bounded_change_int(\n                data.get(\n                    "observed_generation"\n                )\n            ),\n            "replicas_desired": bounded_change_int(\n                data.get(\n                    "replicas_desired"\n                )\n            ),\n            "replicas_updated": bounded_change_int(\n                data.get(\n                    "replicas_updated"\n                )\n            ),\n            "replicas_ready": bounded_change_int(\n                data.get(\n                    "replicas_ready"\n                )\n            ),\n            "replicas_available": bounded_change_int(\n                data.get(\n                    "replicas_available"\n                )\n            ),\n            "replicas_unavailable": bounded_change_int(\n                data.get(\n                    "replicas_unavailable"\n                )\n            ),\n            "history_complete": bounded_change_bool(\n                data.get(\n                    "history_complete"\n                )\n            ),\n            "rollout_condition_summary": bounded_change_text(\n                data.get(\n                    "rollout_condition_summary"\n                ),\n                required=False,\n                max_length=512,\n            ),\n            "generation_observed": bounded_change_bool(\n                data.get(\n                    "generation_observed"\n                )\n            ),\n            "rollout_complete": bounded_change_bool(\n                data.get(\n                    "rollout_complete"\n                )\n            ),\n            "rollout_failure_signal": bounded_change_bool(\n                data.get(\n                    "rollout_failure_signal"\n                )\n            ),\n            "rollout_failure_reason": bounded_change_text(\n                data.get(\n                    "rollout_failure_reason"\n                ),\n                required=False,\n            ),\n            "events_status": bounded_change_events_status(\n                data.get(\n                    "events_status"\n                )\n            ),\n            "events_error_code": bounded_change_text(\n                data.get(\n                    "events_error_code"\n                ),\n                required=False,\n            ),\n            "recent_event_count": bounded_change_int(\n                data.get(\n                    "recent_event_count"\n                )\n            ),\n            "recent_warning_count": bounded_change_int(\n                data.get(\n                    "recent_warning_count"\n                )\n            ),\n            "recent_event_reasons": bounded_change_text(\n                data.get(\n                    "recent_event_reasons"\n                ),\n                required=False,\n                max_length=512,\n            ),\n            "recent_event_summary": bounded_change_text(\n                data.get(\n                    "recent_event_summary"\n                ),\n                required=False,\n                max_length=1536,\n            ),\n        }\n\n        return EvidenceItem(\n            probe=probe,\n            source="kubernetes_change",\n            success=True,\n            trusted=True,\n            production_signal=True,\n            reliability=1.0,\n            observed_at=observed_at,\n            facts=facts,\n        )\n\n    def _normalize_prometheus(\n        self,\n        scope: InvestigationScope,\n        probe: InvestigationProbe,\n        result: Any,\n    ) -> EvidenceItem:\n        data, observed_at = (\n            self._validate_tool_evidence(\n                result=result,\n                expected_source="prometheus",\n            )\n        )\n\n        result_type_value = data.get(\n            "resultType"\n        )\n\n        if (\n            not isinstance(\n                result_type_value,\n                str,\n            )\n            or result_type_value\n            not in {\n                "vector",\n                "matrix",\n                "scalar",\n                "string",\n            }\n        ):\n            raise InvestigationProbeResponseError(\n                "Prometheus evidence result type is invalid"\n            )\n\n        result_type = (\n            result_type_value[:64]\n        )\n\n        samples = extract_numeric_samples(\n            result_type=result_type,\n            value=data.get(\n                "result"\n            ),\n        )\n\n        if not samples:\n            raise InvestigationProbeResponseError(\n                "Prometheus evidence contains no numeric samples"\n            )\n\n        try:\n            event_offset_seconds = (\n                self.time_policy.validate_observed_at(\n                    scope=scope,\n                    probe=probe,\n                    observed_at=observed_at,\n                )\n            )\n        except InvestigationEvidenceTimeError as exc:\n            raise InvestigationProbeResponseError(\n                "Prometheus evidence is not "\n                "temporally relevant"\n            ) from exc\n\n        facts = {\n            "temporal_basis": (\n                self.time_policy.temporal_basis(\n                    scope=scope,\n                    probe=probe,\n                )\n            ),\n            "event_offset_seconds": (\n                event_offset_seconds\n            ),\n            "result_type": result_type,\n            "sample_count": len(\n                samples\n            ),\n            "value_sum": sum(\n                samples\n            ),\n            "value_min": min(\n                samples\n            ),\n            "value_max": max(\n                samples\n            ),\n        }\n\n        return EvidenceItem(\n            probe=probe,\n            source="prometheus",\n            success=True,\n            trusted=True,\n            production_signal=True,\n            reliability=1.0,\n            observed_at=observed_at,\n            facts=facts,\n        )\n\n    @classmethod\n    def _validate_tool_evidence(\n        cls,\n        *,\n        result: Any,\n        expected_source: str,\n    ) -> tuple[\n        Mapping[str, Any],\n        datetime,\n    ]:\n        if not isinstance(\n            result,\n            Mapping,\n        ):\n            raise InvestigationProbeResponseError(\n                "Investigation tool result is invalid"\n            )\n\n        if (\n            result.get(\n                "success"\n            )\n            is not True\n        ):\n            raise InvestigationProbeResponseError(\n                "Investigation tool result was unsuccessful"\n            )\n\n        source_value = result.get(\n            "source"\n        )\n\n        if not isinstance(\n            source_value,\n            str,\n        ):\n            raise InvestigationProbeResponseError(\n                "Investigation evidence source is invalid"\n            )\n\n        source = (\n            source_value\n            .strip()\n            .lower()\n        )\n\n        if source != expected_source:\n            raise InvestigationProbeResponseError(\n                "Investigation evidence source is untrusted"\n            )\n\n        mode_value = result.get(\n            "mode"\n        )\n\n        if not isinstance(\n            mode_value,\n            str,\n        ):\n            raise InvestigationProbeResponseError(\n                "Investigation evidence mode is invalid"\n            )\n\n        mode = (\n            mode_value\n            .strip()\n            .lower()\n        )\n\n        if mode != cls._TRUSTED_MODE:\n            raise InvestigationProbeResponseError(\n                "Investigation evidence mode is not read-only"\n            )\n\n        if (\n            result.get(\n                "production_signal"\n            )\n            is not True\n        ):\n            raise InvestigationProbeResponseError(\n                "Investigation evidence is not a production signal"\n            )\n\n        observed_at = parse_observed_at(\n            result.get(\n                "observed_at"\n            )\n        )\n\n        data = result.get(\n            "data"\n        )\n\n        if not isinstance(\n            data,\n            Mapping,\n        ):\n            raise InvestigationProbeResponseError(\n                "Investigation evidence data is invalid"\n            )\n\n        return (\n            data,\n            observed_at,\n        )\n\n    @staticmethod\n    def _escape_label(\n        value: str,\n    ) -> str:\n        return (\n            value\n            .replace(\n                "\\\\",\n                "\\\\\\\\",\n            )\n            .replace(\n                "\\n",\n                "\\\\n",\n            )\n            .replace(\n                "\\r",\n                "\\\\r",\n            )\n            .replace(\n                \'"\',\n                \'\\\\"\',\n            )\n        )\n\n\ndef redact_log_excerpt(\n    value: str,\n) -> tuple[str, int]:\n    """\n    Defense-in-depth redaction at the Investigation trust boundary.\n\n    KubernetesTool redacts before ToolManager tracing. This second pass keeps\n    injected or forged ToolManager responses from placing obvious credentials\n    into bounded InvestigationState.\n    """\n\n    text = value\n    total = 0\n\n    patterns = [\n        (\n            re.compile(\n                (\n                    r"\\beyJ[A-Za-z0-9_-]{10,}"\n                    r"\\.[A-Za-z0-9_-]{10,}"\n                    r"\\.[A-Za-z0-9_-]{10,}\\b"\n                )\n            ),\n            "[REDACTED_JWT]",\n        ),\n        (\n            re.compile(\n                (\n                    r"(?i)\\b("\n                    r"bearer|basic"\n                    r")\\s+"\n                    r"[A-Za-z0-9._~+/=-]{8,}"\n                )\n            ),\n            None,\n        ),\n        (\n            re.compile(\n                (\n                    r"(?i)\\b("\n                    r"password|passwd|pwd|secret|token|"\n                    r"api[_-]?key|access[_-]?key|"\n                    r"client[_-]?secret"\n                    r")\\b"\n                    r"(\\s*[:=]\\s*)"\n                    r"([\\"\']?)"\n                    r"([^\\s,;\\"\']{4,})"\n                    r"([\\"\']?)"\n                )\n            ),\n            None,\n        ),\n    ]\n\n    text, count = patterns[0][0].subn(\n        patterns[0][1],\n        text,\n    )\n\n    total += count\n\n    text, count = patterns[1][0].subn(\n        lambda match: (\n            match.group(1)\n            + " [REDACTED]"\n        ),\n        text,\n    )\n\n    total += count\n\n    text, count = patterns[2][0].subn(\n        lambda match: (\n            match.group(1)\n            + match.group(2)\n            + "[REDACTED]"\n        ),\n        text,\n    )\n\n    total += count\n\n    return (\n        text,\n        total,\n    )\n\n\ndef bounded_change_text(\n    value: Any,\n    *,\n    required: bool,\n    max_length: int = 512,\n) -> str | None:\n    if value is None:\n        if required:\n            raise InvestigationProbeResponseError(\n                "Kubernetes change text fact is missing"\n            )\n        return None\n\n    if not isinstance(\n        value,\n        str,\n    ):\n        raise InvestigationProbeResponseError(\n            "Kubernetes change text fact is invalid"\n        )\n\n    normalized = value.strip()\n\n    if not normalized:\n        if required:\n            raise InvestigationProbeResponseError(\n                "Kubernetes change text fact is missing"\n            )\n        return None\n\n    if len(\n        normalized\n    ) > max_length:\n        raise InvestigationProbeResponseError(\n            "Kubernetes change text fact is too large"\n        )\n\n    return normalized\n\n\ndef bounded_change_int(\n    value: Any,\n) -> int | None:\n    if value is None:\n        return None\n\n    if (\n        isinstance(\n            value,\n            bool,\n        )\n        or not isinstance(\n            value,\n            int,\n        )\n        or value < 0\n        or value > 1_000_000_000\n    ):\n        raise InvestigationProbeResponseError(\n            "Kubernetes change integer fact is invalid"\n        )\n\n    return value\n\n\ndef bounded_change_events_status(\n    value: Any,\n) -> str | None:\n    if value is None:\n        return None\n\n    if value not in {\n        "complete",\n        "partial",\n        "unavailable",\n    }:\n        raise InvestigationProbeResponseError(\n            "Kubernetes event evidence status is invalid"\n        )\n\n    return value\n\n\ndef bounded_change_bool(\n    value: Any,\n) -> bool | None:\n    if value is None:\n        return None\n\n    if not isinstance(\n        value,\n        bool,\n    ):\n        raise InvestigationProbeResponseError(\n            "Kubernetes change boolean fact is invalid"\n        )\n\n    return value\n\n\ndef cls_scalar(\n    value: Any,\n):\n    if (\n        value is None\n        or isinstance(\n            value,\n            (\n                bool,\n                int,\n                float,\n                str,\n            ),\n        )\n    ):\n        return value\n\n    return str(\n        value\n    )[:256]\n\n\ndef parse_observed_at(\n    value: Any,\n) -> datetime:\n    if isinstance(\n        value,\n        datetime,\n    ):\n        parsed = value\n\n    elif isinstance(\n        value,\n        str,\n    ):\n        text = value.strip()\n\n        if not text:\n            raise InvestigationProbeResponseError(\n                "Investigation evidence observed_at is invalid"\n            )\n\n        if text.endswith(\n            "Z"\n        ):\n            text = (\n                f"{text[:-1]}+00:00"\n            )\n\n        try:\n            parsed = datetime.fromisoformat(\n                text\n            )\n        except ValueError as exc:\n            raise InvestigationProbeResponseError(\n                "Investigation evidence observed_at is invalid"\n            ) from exc\n\n    else:\n        raise InvestigationProbeResponseError(\n            "Investigation evidence observed_at is invalid"\n        )\n\n    if parsed.tzinfo is None:\n        raise InvestigationProbeResponseError(\n            "Investigation evidence observed_at must be timezone-aware"\n        )\n\n    return parsed.astimezone(\n        UTC\n    )\n\n\ndef extract_numeric_samples(\n    result_type: str | None,\n    value: Any,\n) -> list[float]:\n    samples: list[float] = []\n\n    def add_sample(\n        sample: Any,\n    ) -> None:\n        if (\n            not isinstance(\n                sample,\n                list,\n            )\n            or len(sample) < 2\n            or len(samples) >= 32\n        ):\n            return\n\n        try:\n            numeric_value = float(\n                sample[1]\n            )\n        except (\n            TypeError,\n            ValueError,\n        ):\n            return\n\n        if not isfinite(\n            numeric_value\n        ):\n            return\n\n        samples.append(\n            numeric_value\n        )\n\n    if result_type in {\n        "scalar",\n        "string",\n    }:\n        add_sample(\n            value\n        )\n\n    elif (\n        result_type == "vector"\n        and isinstance(\n            value,\n            list,\n        )\n    ):\n        for item in value[:32]:\n            if isinstance(\n                item,\n                Mapping,\n            ):\n                add_sample(\n                    item.get(\n                        "value"\n                    )\n                )\n\n    elif (\n        result_type == "matrix"\n        and isinstance(\n            value,\n            list,\n        )\n    ):\n        for item in value[:32]:\n            if not isinstance(\n                item,\n                Mapping,\n            ):\n                continue\n\n            values = item.get(\n                "values"\n            )\n\n            if (\n                isinstance(\n                    values,\n                    list,\n                )\n                and values\n            ):\n                add_sample(\n                    values[-1]\n                )\n\n    return samples\n\n\n__all__ = [\n    "InvestigationProbeError",\n    "InvestigationProbeResponseError",\n    "InvestigationToolUnavailableError",\n    "ReadOnlyInvestigationProbeExecutor",\n    "extract_numeric_samples",\n    "parse_observed_at",\n]\n'
KUBERNETES_TOOL_SOURCE = 'import os\nimport re\nimport ssl\nfrom collections.abc import Callable, Mapping\nfrom datetime import UTC, datetime\nfrom pathlib import Path\nfrom typing import Any\nfrom urllib.parse import quote, urlencode, urlparse\n\nimport httpx\n\nfrom services.agent_runtime.app.tools.base import (\n    BaseTool,\n)\n\n\nclass KubernetesToolError(RuntimeError):\n    """\n    Base error raised by KubernetesTool.\n    """\n\n\nclass KubernetesConfigurationError(\n    KubernetesToolError\n):\n    """\n    Kubernetes API configuration is invalid or unavailable.\n    """\n\n\nclass KubernetesQueryError(\n    KubernetesToolError\n):\n    """\n    Kubernetes API query failed.\n    """\n\n\nclass KubernetesAuthorizationError(\n    KubernetesQueryError\n):\n    """\n    Kubernetes rejected the configured identity.\n    """\n\n\nclass KubernetesResourceNotFoundError(\n    KubernetesQueryError\n):\n    """\n    Requested Kubernetes resource does not exist.\n    """\n\n\nclass KubernetesOperationNotAllowedError(\n    KubernetesToolError\n):\n    """\n    Operation is outside the read-only verification boundary.\n    """\n\n\nclass KubernetesTool(BaseTool):\n    """\n    Read-only Kubernetes Pod evidence tool.\n\n    Live mode uses the Kubernetes Core API directly through httpx.\n    Previous-container logs are collected through the Pod log subresource\n    using fixed platform-owned bounds and secret redaction before results\n    can enter ToolManager traces or Investigation evidence.\n    It supports an explicit API URL and in-cluster discovery.\n\n    A temporary dry-run fallback is retained for compatibility.\n    It is marked production_signal=False and is rejected by the\n    VerificationEvidenceCollector.\n    """\n\n    _READ_ONLY_ACTIONS = {\n        "describe",\n        "get",\n        "previous_logs",\n    }\n\n    _LOG_TAIL_LINES = 80\n    _LOG_LIMIT_BYTES = 16384\n    _LOG_RETURN_MAX_CHARS = 4000\n\n    _POD_RESOURCES = {\n        "pod",\n        "pods",\n    }\n\n    _DEFAULT_TOKEN_FILE = Path(\n        "/var/run/secrets/kubernetes.io/"\n        "serviceaccount/token"\n    )\n\n    _DEFAULT_CA_FILE = Path(\n        "/var/run/secrets/kubernetes.io/"\n        "serviceaccount/ca.crt"\n    )\n\n    def __init__(\n        self,\n        api_url: str | None = None,\n        timeout_seconds: float | None = None,\n        verify_tls: bool | None = None,\n        bearer_token: str | None = None,\n        token_file: str | Path | None = None,\n        ca_file: str | Path | None = None,\n        cluster_name: str | None = None,\n        allow_dry_run_fallback: bool | None = None,\n        client: httpx.AsyncClient | None = None,\n        clock: Callable[[], datetime] | None = None,\n    ) -> None:\n        configured_url = (\n            api_url\n            if api_url is not None\n            else os.getenv("KUBERNETES_API_URL")\n        )\n\n        self.in_cluster = False\n\n        if not configured_url:\n            configured_url = (\n                self._discover_in_cluster_url()\n            )\n            self.in_cluster = bool(\n                configured_url\n            )\n\n        self.api_url = (\n            configured_url.rstrip("/")\n            if configured_url\n            else None\n        )\n\n        if self.api_url:\n            parsed_url = urlparse(\n                self.api_url\n            )\n            if parsed_url.scheme not in {\n                "http",\n                "https",\n            } or not parsed_url.netloc:\n                raise KubernetesConfigurationError(\n                    "Kubernetes API URL is invalid"\n                )\n\n        self.timeout_seconds = (\n            timeout_seconds\n            if timeout_seconds is not None\n            else self._read_positive_float(\n                "KUBERNETES_TIMEOUT_SECONDS",\n                default=5.0,\n            )\n        )\n\n        self.verify_tls = (\n            verify_tls\n            if verify_tls is not None\n            else self._read_bool(\n                "KUBERNETES_VERIFY_TLS",\n                default=True,\n            )\n        )\n\n        configured_token_file = (\n            token_file\n            if token_file is not None\n            else os.getenv("KUBERNETES_TOKEN_FILE")\n        )\n\n        if (\n            configured_token_file is None\n            and self.in_cluster\n            and self._DEFAULT_TOKEN_FILE.exists()\n        ):\n            configured_token_file = (\n                self._DEFAULT_TOKEN_FILE\n            )\n\n        self.token_file = (\n            Path(configured_token_file)\n            if configured_token_file\n            else None\n        )\n\n        self.bearer_token = (\n            bearer_token\n            if bearer_token is not None\n            else os.getenv(\n                "KUBERNETES_BEARER_TOKEN"\n            )\n        )\n\n        if (\n            not self.bearer_token\n            and self.token_file is not None\n        ):\n            self.bearer_token = self._read_token(\n                self.token_file\n            )\n\n        configured_ca_file = (\n            ca_file\n            if ca_file is not None\n            else os.getenv("KUBERNETES_CA_FILE")\n        )\n\n        if (\n            configured_ca_file is None\n            and self.in_cluster\n            and self._DEFAULT_CA_FILE.exists()\n        ):\n            configured_ca_file = (\n                self._DEFAULT_CA_FILE\n            )\n\n        self.ca_file = (\n            Path(configured_ca_file)\n            if configured_ca_file\n            else None\n        )\n\n        self.cluster_name = (\n            cluster_name\n            if cluster_name is not None\n            else os.getenv(\n                "KUBERNETES_CLUSTER_NAME"\n            )\n        )\n\n        self.allow_dry_run_fallback = (\n            allow_dry_run_fallback\n            if allow_dry_run_fallback is not None\n            else self._read_bool(\n                "KUBERNETES_ALLOW_DRY_RUN_FALLBACK",\n                default=True,\n            )\n        )\n\n        self.client = client\n        self._clock = clock or (\n            lambda: datetime.now(UTC)\n        )\n\n        if self.timeout_seconds <= 0:\n            raise KubernetesConfigurationError(\n                "Kubernetes timeout must be positive"\n            )\n\n    @property\n    def name(self) -> str:\n        return "kubernetes"\n\n    async def execute(\n        self,\n        action: str,\n        resource: str,\n        target: str,\n        namespace: str = "default",\n        cluster: str | None = None,\n        **kwargs: Any,\n    ) -> dict[str, Any]:\n        normalized_action = self._required_text(\n            action,\n            "action",\n        ).lower()\n        normalized_resource = self._required_text(\n            resource,\n            "resource",\n        ).lower()\n        normalized_target = self._required_text(\n            target,\n            "target",\n        )\n        normalized_namespace = self._required_text(\n            namespace,\n            "namespace",\n        )\n\n        normalized_cluster = (\n            self._required_text(\n                cluster,\n                "cluster",\n            )\n            if cluster is not None\n            else None\n        )\n\n        configured_cluster = (\n            self.cluster_name.strip()\n            if isinstance(\n                self.cluster_name,\n                str,\n            )\n            and self.cluster_name.strip()\n            else None\n        )\n\n        if (\n            normalized_cluster is not None\n            and configured_cluster is not None\n            and normalized_cluster\n            != configured_cluster\n        ):\n            raise KubernetesConfigurationError(\n                "Requested cluster does not match configured Kubernetes cluster"\n            )\n\n        if normalized_action not in (\n            self._READ_ONLY_ACTIONS\n        ):\n            if self.allow_dry_run_fallback:\n                return self._dry_run_response(\n                    action=normalized_action,\n                    resource=normalized_resource,\n                    target=normalized_target,\n                    namespace=normalized_namespace,\n                )\n\n            raise KubernetesOperationNotAllowedError(\n                "KubernetesTool only allows bounded read-only "\n                "get, describe, and previous_logs actions"\n            )\n\n        if normalized_resource not in (\n            self._POD_RESOURCES\n        ):\n            raise KubernetesOperationNotAllowedError(\n                "KubernetesTool currently supports Pod "\n                "evidence only"\n            )\n\n        if self.api_url is None:\n            if not self.allow_dry_run_fallback:\n                raise KubernetesConfigurationError(\n                    "KUBERNETES_API_URL is not configured"\n                )\n\n            return self._dry_run_response(\n                action=normalized_action,\n                resource="pod",\n                target=normalized_target,\n                namespace=normalized_namespace,\n            )\n\n        payload = await self._get_pod(\n            namespace=normalized_namespace,\n            target=normalized_target,\n        )\n\n        if normalized_action == "previous_logs":\n            container_name = (\n                self._select_previous_log_container(\n                    payload\n                )\n            )\n\n            data = await (\n                self._get_previous_container_logs(\n                    namespace=normalized_namespace,\n                    target=normalized_target,\n                    container=container_name,\n                )\n            )\n        else:\n            data = self._normalize_pod(\n                payload\n            )\n\n        observed_at = self._now()\n\n        return {\n            "success": True,\n            "source": "kubernetes",\n            "mode": "read_only",\n            "production_signal": True,\n            "observed_at": observed_at.isoformat(),\n            "action": normalized_action,\n            "resource": "pod",\n            "target": normalized_target,\n            "namespace": normalized_namespace,\n            "cluster": self.cluster_name,\n            "data": data,\n        }\n\n    async def _get_pod(\n        self,\n        namespace: str,\n        target: str,\n    ) -> dict[str, Any]:\n        url = self._pod_url(\n            namespace=namespace,\n            target=target,\n        )\n\n        try:\n            if self.client is not None:\n                response = await self.client.get(\n                    url,\n                    headers=self._headers,\n                )\n            else:\n                async with httpx.AsyncClient(\n                    timeout=self.timeout_seconds,\n                    verify=self._httpx_verify,\n                    headers=self._headers,\n                ) as client:\n                    response = await client.get(\n                        url\n                    )\n\n            response.raise_for_status()\n        except httpx.TimeoutException as exc:\n            raise KubernetesQueryError(\n                "Kubernetes API query timed out"\n            ) from exc\n        except httpx.HTTPStatusError as exc:\n            status_code = exc.response.status_code\n\n            if status_code in {\n                401,\n                403,\n            }:\n                raise KubernetesAuthorizationError(\n                    "Kubernetes API authorization failed"\n                ) from exc\n\n            if status_code == 404:\n                raise KubernetesResourceNotFoundError(\n                    "Kubernetes Pod was not found"\n                ) from exc\n\n            raise KubernetesQueryError(\n                "Kubernetes API returned HTTP "\n                f"{status_code}"\n            ) from exc\n        except httpx.RequestError as exc:\n            raise KubernetesQueryError(\n                "Kubernetes API request failed"\n            ) from exc\n\n        try:\n            payload = response.json()\n        except ValueError as exc:\n            raise KubernetesQueryError(\n                "Kubernetes API returned invalid JSON"\n            ) from exc\n\n        if not isinstance(payload, dict):\n            raise KubernetesQueryError(\n                "Kubernetes API response is not an object"\n            )\n\n        if (\n            payload.get("kind") == "Status"\n            and payload.get("status") == "Failure"\n        ):\n            reason = payload.get(\n                "reason",\n                "Unknown",\n            )\n            raise KubernetesQueryError(\n                "Kubernetes API returned failure "\n                f"[{reason}]"\n            )\n\n        return payload\n\n    @classmethod\n    def _select_previous_log_container(\n        cls,\n        payload: Mapping[str, Any],\n    ) -> str:\n        status = payload.get(\n            "status"\n        )\n\n        if not isinstance(\n            status,\n            Mapping,\n        ):\n            raise KubernetesQueryError(\n                "Kubernetes Pod status is invalid"\n            )\n\n        statuses = status.get(\n            "containerStatuses"\n        )\n\n        if not isinstance(\n            statuses,\n            list,\n        ):\n            raise KubernetesQueryError(\n                "Kubernetes Pod container statuses are unavailable"\n            )\n\n        candidates = []\n\n        for item in statuses:\n            if not isinstance(\n                item,\n                Mapping,\n            ):\n                continue\n\n            name = item.get(\n                "name"\n            )\n\n            restart_count = cls._safe_int(\n                item.get(\n                    "restartCount"\n                )\n            )\n\n            last_state = item.get(\n                "lastState"\n            )\n\n            terminated = (\n                last_state.get(\n                    "terminated"\n                )\n                if isinstance(\n                    last_state,\n                    Mapping,\n                )\n                else None\n            )\n\n            if (\n                isinstance(\n                    name,\n                    str,\n                )\n                and name.strip()\n                and restart_count > 0\n                and isinstance(\n                    terminated,\n                    Mapping,\n                )\n            ):\n                candidates.append(\n                    name.strip()\n                )\n\n        unique = sorted(\n            set(\n                candidates\n            )\n        )\n\n        if len(\n            unique\n        ) != 1:\n            raise KubernetesQueryError(\n                "Kubernetes previous-log container selection is ambiguous"\n            )\n\n        return unique[0]\n\n    async def _get_previous_container_logs(\n        self,\n        *,\n        namespace: str,\n        target: str,\n        container: str,\n    ) -> dict[str, Any]:\n        url = self._pod_log_url(\n            namespace=namespace,\n            target=target,\n            container=container,\n        )\n\n        try:\n            if self.client is not None:\n                response = await self.client.get(\n                    url,\n                    headers=self._headers,\n                )\n            else:\n                async with httpx.AsyncClient(\n                    timeout=self.timeout_seconds,\n                    verify=self._httpx_verify,\n                    headers=self._headers,\n                ) as client:\n                    response = await client.get(\n                        url\n                    )\n\n            response.raise_for_status()\n\n        except httpx.TimeoutException as exc:\n            raise KubernetesQueryError(\n                "Kubernetes previous-log query timed out"\n            ) from exc\n\n        except httpx.HTTPStatusError as exc:\n            status_code = (\n                exc.response.status_code\n            )\n\n            if status_code in {\n                401,\n                403,\n            }:\n                raise KubernetesAuthorizationError(\n                    "Kubernetes previous-log authorization failed"\n                ) from exc\n\n            if status_code == 404:\n                raise KubernetesResourceNotFoundError(\n                    "Kubernetes previous container logs were not found"\n                ) from exc\n\n            raise KubernetesQueryError(\n                "Kubernetes previous-log API returned HTTP "\n                f"{status_code}"\n            ) from exc\n\n        except httpx.RequestError as exc:\n            raise KubernetesQueryError(\n                "Kubernetes previous-log request failed"\n            ) from exc\n\n        raw_text = response.text\n\n        if not isinstance(\n            raw_text,\n            str,\n        ):\n            raise KubernetesQueryError(\n                "Kubernetes previous-log response is invalid"\n            )\n\n        bounded_text, truncated = (\n            self._bound_log_text(\n                raw_text\n            )\n        )\n\n        redacted_text, redaction_count = (\n            self._redact_log_text(\n                bounded_text\n            )\n        )\n\n        if len(\n            redacted_text\n        ) > self._LOG_RETURN_MAX_CHARS:\n            redacted_text = redacted_text[\n                -self._LOG_RETURN_MAX_CHARS:\n            ]\n\n            truncated = True\n\n        lines = (\n            redacted_text.splitlines()\n            if redacted_text\n            else []\n        )\n\n        if len(\n            lines\n        ) > self._LOG_TAIL_LINES:\n            lines = lines[\n                -self._LOG_TAIL_LINES:\n            ]\n\n            redacted_text = "\\n".join(\n                lines\n            )\n\n            truncated = True\n\n        return {\n            "container_name": container,\n            "previous": True,\n            "line_count": len(\n                lines\n            ),\n            "truncated": (\n                truncated\n            ),\n            "redaction_count": (\n                redaction_count\n            ),\n            "excerpt": redacted_text,\n        }\n\n    @classmethod\n    def _bound_log_text(\n        cls,\n        value: str,\n    ) -> tuple[str, bool]:\n        normalized = (\n            value\n            .replace(\n                "\\r\\n",\n                "\\n",\n            )\n            .replace(\n                "\\r",\n                "\\n",\n            )\n            .replace(\n                "\\x00",\n                "",\n            )\n        )\n\n        encoded = normalized.encode(\n            "utf-8",\n            errors="replace",\n        )\n\n        truncated = (\n            len(\n                encoded\n            )\n            > cls._LOG_LIMIT_BYTES\n        )\n\n        if truncated:\n            encoded = encoded[\n                -cls._LOG_LIMIT_BYTES:\n            ]\n\n            normalized = encoded.decode(\n                "utf-8",\n                errors="replace",\n            )\n\n        return (\n            normalized,\n            truncated,\n        )\n\n    @staticmethod\n    def _redact_log_text(\n        value: str,\n    ) -> tuple[str, int]:\n        text = re.sub(\n            r"\\x1b\\[[0-?]*[ -/]*[@-~]",\n            "",\n            value,\n        )\n\n        total = 0\n\n        private_key_pattern = re.compile(\n            (\n                r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"\n                r".*?"\n                r"-----END [A-Z0-9 ]*PRIVATE KEY-----"\n            ),\n            re.IGNORECASE\n            | re.DOTALL,\n        )\n\n        text, count = (\n            private_key_pattern.subn(\n                "[REDACTED_PRIVATE_KEY]",\n                text,\n            )\n        )\n\n        total += count\n\n        jwt_pattern = re.compile(\n            (\n                r"\\beyJ[A-Za-z0-9_-]{10,}"\n                r"\\.[A-Za-z0-9_-]{10,}"\n                r"\\.[A-Za-z0-9_-]{10,}\\b"\n            )\n        )\n\n        text, count = jwt_pattern.subn(\n            "[REDACTED_JWT]",\n            text,\n        )\n\n        total += count\n\n        auth_pattern = re.compile(\n            (\n                r"(?i)\\b("\n                r"bearer|basic"\n                r")\\s+"\n                r"[A-Za-z0-9._~+/=-]{8,}"\n            )\n        )\n\n        text, count = auth_pattern.subn(\n            lambda match: (\n                match.group(1)\n                + " [REDACTED]"\n            ),\n            text,\n        )\n\n        total += count\n\n        key_value_pattern = re.compile(\n            (\n                r"(?i)\\b("\n                r"password|passwd|pwd|secret|token|"\n                r"api[_-]?key|access[_-]?key|"\n                r"client[_-]?secret"\n                r")\\b"\n                r"(\\s*[:=]\\s*)"\n                r"([\\"\']?)"\n                r"([^\\s,;\\"\']{4,})"\n                r"([\\"\']?)"\n            )\n        )\n\n        def replace_key_value(\n            match: re.Match[str],\n        ) -> str:\n            return (\n                match.group(1)\n                + match.group(2)\n                + "[REDACTED]"\n            )\n\n        text, count = (\n            key_value_pattern.subn(\n                replace_key_value,\n                text,\n            )\n        )\n\n        total += count\n\n        aws_key_pattern = re.compile(\n            r"\\bAKIA[0-9A-Z]{16}\\b"\n        )\n\n        text, count = (\n            aws_key_pattern.subn(\n                "[REDACTED_ACCESS_KEY]",\n                text,\n            )\n        )\n\n        total += count\n\n        return (\n            text,\n            total,\n        )\n\n    def _normalize_pod(\n        self,\n        payload: Mapping[str, Any],\n    ) -> dict[str, Any]:\n        metadata = payload.get("metadata")\n        status = payload.get("status")\n        spec = payload.get("spec")\n\n        if not isinstance(metadata, Mapping):\n            raise KubernetesQueryError(\n                "Kubernetes Pod metadata is invalid"\n            )\n\n        if not isinstance(status, Mapping):\n            raise KubernetesQueryError(\n                "Kubernetes Pod status is invalid"\n            )\n\n        if not isinstance(spec, Mapping):\n            spec = {}\n\n        conditions = self._normalize_conditions(\n            status.get("conditions")\n        )\n        containers = self._normalize_containers(\n            status.get("containerStatuses")\n        )\n\n        ready_condition = any(\n            condition["type"] == "Ready"\n            and condition["status"] == "True"\n            for condition in conditions\n        )\n        scheduled = any(\n            condition["type"] == "PodScheduled"\n            and condition["status"] == "True"\n            for condition in conditions\n        )\n        all_containers_ready = (\n            bool(containers)\n            and all(\n                container["ready"] is True\n                for container in containers\n            )\n        )\n        phase = status.get("phase")\n        ready = (\n            phase == "Running"\n            and ready_condition\n            and all_containers_ready\n        )\n        oom_killed = any(\n            container.get("state_reason")\n            == "OOMKilled"\n            or container.get(\n                "last_termination_reason"\n            )\n            == "OOMKilled"\n            for container in containers\n        )\n\n        return {\n            "api_version": payload.get(\n                "apiVersion"\n            ),\n            "kind": payload.get("kind"),\n            "uid": metadata.get("uid"),\n            "resource_version": metadata.get(\n                "resourceVersion"\n            ),\n            "creation_timestamp": metadata.get(\n                "creationTimestamp"\n            ),\n            "deletion_timestamp": metadata.get(\n                "deletionTimestamp"\n            ),\n            "labels": dict(\n                metadata.get("labels") or {}\n            ),\n            "phase": phase,\n            "ready": ready,\n            "scheduled": scheduled,\n            "oom_killed": oom_killed,\n            "pod_ip": status.get("podIP"),\n            "host_ip": status.get("hostIP"),\n            "node_name": spec.get("nodeName"),\n            "conditions": conditions,\n            "containers": containers,\n        }\n\n    @staticmethod\n    def _normalize_conditions(\n        value: Any,\n    ) -> list[dict[str, Any]]:\n        if not isinstance(value, list):\n            return []\n\n        conditions = []\n\n        for condition in value:\n            if not isinstance(condition, Mapping):\n                continue\n\n            conditions.append(\n                {\n                    "type": condition.get("type"),\n                    "status": condition.get("status"),\n                    "reason": condition.get("reason"),\n                    "message": condition.get("message"),\n                    "last_transition_time": (\n                        condition.get(\n                            "lastTransitionTime"\n                        )\n                    ),\n                }\n            )\n\n        return conditions\n\n    @classmethod\n    def _normalize_containers(\n        cls,\n        value: Any,\n    ) -> list[dict[str, Any]]:\n        if not isinstance(value, list):\n            return []\n\n        containers = []\n\n        for container in value:\n            if not isinstance(container, Mapping):\n                continue\n\n            state, state_reason = (\n                cls._container_state(\n                    container.get("state")\n                )\n            )\n            last_reason, last_finished_at = (\n                cls._last_termination(\n                    container.get("lastState")\n                )\n            )\n\n            containers.append(\n                {\n                    "name": container.get("name"),\n                    "ready": container.get("ready")\n                    is True,\n                    "restart_count": cls._safe_int(\n                        container.get("restartCount")\n                    ),\n                    "state": state,\n                    "state_reason": state_reason,\n                    "last_termination_reason": (\n                        last_reason\n                    ),\n                    "last_terminated_at": (\n                        last_finished_at\n                    ),\n                    "image": container.get("image"),\n                    "image_id": container.get(\n                        "imageID"\n                    ),\n                }\n            )\n\n        return containers\n\n    @staticmethod\n    def _container_state(\n        value: Any,\n    ) -> tuple[str | None, str | None]:\n        if not isinstance(value, Mapping):\n            return None, None\n\n        for name in (\n            "waiting",\n            "running",\n            "terminated",\n        ):\n            details = value.get(name)\n            if isinstance(details, Mapping):\n                return name, details.get("reason")\n\n        return None, None\n\n    @staticmethod\n    def _last_termination(\n        value: Any,\n    ) -> tuple[str | None, str | None]:\n        if not isinstance(value, Mapping):\n            return None, None\n\n        terminated = value.get("terminated")\n\n        if not isinstance(terminated, Mapping):\n            return None, None\n\n        return (\n            terminated.get("reason"),\n            terminated.get("finishedAt"),\n        )\n\n    @staticmethod\n    def _safe_int(\n        value: Any,\n    ) -> int:\n        try:\n            return int(value)\n        except (TypeError, ValueError):\n            return 0\n\n    def _pod_url(\n        self,\n        namespace: str,\n        target: str,\n    ) -> str:\n        if self.api_url is None:\n            raise KubernetesConfigurationError(\n                "KUBERNETES_API_URL is not configured"\n            )\n\n        safe_namespace = quote(\n            namespace,\n            safe="",\n        )\n        safe_target = quote(\n            target,\n            safe="",\n        )\n\n        return (\n            f"{self.api_url}/api/v1/namespaces/"\n            f"{safe_namespace}/pods/{safe_target}"\n        )\n\n    def _pod_log_url(\n        self,\n        *,\n        namespace: str,\n        target: str,\n        container: str,\n    ) -> str:\n        base = self._pod_url(\n            namespace=namespace,\n            target=target,\n        )\n\n        query = urlencode(\n            {\n                "container": container,\n                "previous": "true",\n                "tailLines": (\n                    self._LOG_TAIL_LINES\n                ),\n                "limitBytes": (\n                    self._LOG_LIMIT_BYTES\n                ),\n                "timestamps": "true",\n            }\n        )\n\n        return (\n            f"{base}/log?{query}"\n        )\n\n    @property\n    def _headers(self) -> dict[str, str]:\n        headers = {\n            "Accept": "application/json"\n        }\n\n        if self.bearer_token:\n            headers["Authorization"] = (\n                f"Bearer {self.bearer_token}"\n            )\n\n        return headers\n\n    @property\n    def _httpx_verify(\n        self,\n    ) -> bool | ssl.SSLContext:\n        if not self.verify_tls:\n            return False\n\n        if self.ca_file is None:\n            return True\n\n        if not self.ca_file.is_file():\n            raise KubernetesConfigurationError(\n                "Kubernetes CA file was not found"\n            )\n\n        try:\n            return ssl.create_default_context(\n                cafile=str(self.ca_file)\n            )\n        except OSError as exc:\n            raise KubernetesConfigurationError(\n                "Kubernetes CA file is invalid"\n            ) from exc\n\n    def _dry_run_response(\n        self,\n        action: str,\n        resource: str,\n        target: str,\n        namespace: str,\n    ) -> dict[str, Any]:\n        return {\n            "success": True,\n            "source": "mock_kubernetes",\n            "mode": "dry_run",\n            "production_signal": False,\n            "observed_at": self._now().isoformat(),\n            "action": action,\n            "resource": resource,\n            "target": target,\n            "namespace": namespace,\n            "message": (\n                "Kubernetes action simulated"\n            ),\n        }\n\n    def _now(self) -> datetime:\n        value = self._clock()\n\n        if value.tzinfo is None:\n            raise KubernetesConfigurationError(\n                "Kubernetes clock must return a "\n                "timezone-aware datetime"\n            )\n\n        return value.astimezone(UTC)\n\n    @classmethod\n    def _discover_in_cluster_url(\n        cls,\n    ) -> str | None:\n        host = os.getenv(\n            "KUBERNETES_SERVICE_HOST"\n        )\n\n        if not host:\n            return None\n\n        port = os.getenv(\n            "KUBERNETES_SERVICE_PORT_HTTPS",\n            os.getenv(\n                "KUBERNETES_SERVICE_PORT",\n                "443",\n            ),\n        )\n\n        normalized_host = host.strip()\n\n        if ":" in normalized_host and not (\n            normalized_host.startswith("[")\n            and normalized_host.endswith("]")\n        ):\n            normalized_host = (\n                f"[{normalized_host}]"\n            )\n\n        return (\n            f"https://{normalized_host}:{port}"\n        )\n\n    @staticmethod\n    def _read_token(\n        path: Path,\n    ) -> str:\n        try:\n            token = path.read_text(\n                encoding="utf-8"\n            ).strip()\n        except OSError as exc:\n            raise KubernetesConfigurationError(\n                "Kubernetes token file could not be read"\n            ) from exc\n\n        if not token:\n            raise KubernetesConfigurationError(\n                "Kubernetes token file is empty"\n            )\n\n        return token\n\n    @staticmethod\n    def _required_text(\n        value: Any,\n        name: str,\n    ) -> str:\n        if not isinstance(value, str):\n            raise KubernetesToolError(\n                f"Kubernetes {name} must be text"\n            )\n\n        normalized = value.strip()\n\n        if not normalized:\n            raise KubernetesToolError(\n                f"Kubernetes {name} cannot be empty"\n            )\n\n        return normalized\n\n    @staticmethod\n    def _read_bool(\n        name: str,\n        default: bool,\n    ) -> bool:\n        raw = os.getenv(name)\n\n        if raw is None:\n            return default\n\n        normalized = raw.strip().lower()\n\n        if normalized in {\n            "1",\n            "true",\n            "yes",\n            "on",\n        }:\n            return True\n\n        if normalized in {\n            "0",\n            "false",\n            "no",\n            "off",\n        }:\n            return False\n\n        raise KubernetesConfigurationError(\n            f"{name} must be a boolean"\n        )\n\n    @staticmethod\n    def _read_positive_float(\n        name: str,\n        default: float,\n    ) -> float:\n        raw = os.getenv(name)\n\n        if raw is None:\n            return default\n\n        try:\n            value = float(raw)\n        except ValueError as exc:\n            raise KubernetesConfigurationError(\n                f"{name} must be a number"\n            ) from exc\n\n        if value <= 0:\n            raise KubernetesConfigurationError(\n                f"{name} must be positive"\n            )\n\n        return value\n'
SCOPE_TEST_SOURCE = 'from __future__ import annotations\n\nfrom datetime import UTC, datetime\nfrom types import SimpleNamespace\n\nimport httpx\nimport pytest\n\nfrom common.domain.raw_event import RawEvent\nfrom services.gateway.app.parser.factory import (\n    create_parser_registry,\n)\nfrom services.agent_runtime.app.investigation.coordinator import (\n    EvidenceDrivenInvestigationCoordinator,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    InvestigationDecision,\n    InvestigationProbe,\n    InvestigationScope,\n    InvestigationStopReason,\n)\nfrom services.agent_runtime.app.investigation.probes import (\n    ReadOnlyInvestigationProbeExecutor,\n)\nfrom services.agent_runtime.app.investigation.reasoner import (\n    BaseInvestigationReasoner,\n)\nfrom services.agent_runtime.app.tools.kubernetes.tool import (\n    KubernetesConfigurationError,\n    KubernetesTool,\n)\n\n\nINCIDENT_TIME = datetime(\n    2026,\n    8,\n    11,\n    2,\n    30,\n    tzinfo=UTC,\n)\n\nCLUSTER = "prod-sg-17"\nNAMESPACE = "printing-control"\nPOD = "printer-session-api-abc123"\n\nOTHER_CLUSTER = "prod-us-03"\nOTHER_NAMESPACE = "fleet-edge"\nOTHER_POD = "device-gateway-xyz789"\n\n\ndef alertmanager_payload(\n    *,\n    cluster: str,\n    namespace: str,\n    pod: str,\n) -> dict:\n    return {\n        "receiver": "production",\n        "alerts": [\n            {\n                "status": "firing",\n                "labels": {\n                    "alertname": "PodRestartHigh",\n                    "severity": "critical",\n                    "cluster": cluster,\n                    "namespace": namespace,\n                    "pod": pod,\n                },\n                "annotations": {\n                    "summary": (\n                        "pod restart rate is elevated"\n                    )\n                },\n                "startsAt": (\n                    INCIDENT_TIME.isoformat()\n                ),\n            }\n        ],\n    }\n\n\ndef parse_event(\n    *,\n    cluster: str = CLUSTER,\n    namespace: str = NAMESPACE,\n    pod: str = POD,\n):\n    parser = (\n        create_parser_registry()\n        .get(\n            "alertmanager"\n        )\n    )\n\n    return parser.parse(\n        RawEvent(\n            source="alertmanager",\n            payload=alertmanager_payload(\n                cluster=cluster,\n                namespace=namespace,\n                pod=pod,\n            ),\n            headers={},\n        )\n    )\n\n\nclass TerminalReasoner(\n    BaseInvestigationReasoner\n):\n    def __init__(\n        self,\n    ) -> None:\n        self.scopes = []\n\n    async def decide(\n        self,\n        scope,\n        state,\n    ) -> InvestigationDecision:\n        self.scopes.append(\n            scope\n        )\n\n        return InvestigationDecision(\n            hypotheses=[\n                {\n                    "hypothesis_id": "scope-check",\n                    "cause": (\n                        "scope integrity test has no RCA"\n                    ),\n                    "confidence": 0.1,\n                    "supporting_evidence_ids": [],\n                    "conflicting_evidence_ids": [],\n                    "missing_evidence": [\n                        "root-cause evidence"\n                    ],\n                    "optional_evidence": [],\n                }\n            ],\n            rationale_summary=(\n                "scope integrity test terminates without probes"\n            ),\n            stop=True,\n            stop_reason=(\n                InvestigationStopReason\n                .INSUFFICIENT_EVIDENCE\n            ),\n            next_probe=None,\n            conclusion=None,\n        )\n\n\nclass NeverProbeExecutor:\n    def __init__(\n        self,\n    ) -> None:\n        self.calls = 0\n\n    def available_probes(\n        self,\n        context,\n    ):\n        return [\n            InvestigationProbe.KUBERNETES_POD_STATE,\n        ]\n\n    async def collect(\n        self,\n        context,\n        scope,\n        probe,\n    ):\n        self.calls += 1\n        raise AssertionError(\n            "terminal scope test must not collect evidence"\n        )\n\n\nclass RecordingTools:\n    def __init__(\n        self,\n    ) -> None:\n        self.calls = []\n\n    async def call(\n        self,\n        name,\n        context=None,\n        **kwargs,\n    ):\n        self.calls.append(\n            {\n                "name": name,\n                "kwargs": kwargs,\n            }\n        )\n\n        if name == "kubernetes":\n            if (\n                kwargs.get(\n                    "action"\n                )\n                == "previous_logs"\n            ):\n                return {\n                    "success": True,\n                    "source": "kubernetes",\n                    "mode": "read_only",\n                    "production_signal": True,\n                    "observed_at": (\n                        INCIDENT_TIME.isoformat()\n                    ),\n                    "cluster": kwargs.get(\n                        "cluster"\n                    ),\n                    "data": {\n                        "container_name": "app",\n                        "previous": True,\n                        "line_count": 1,\n                        "truncated": False,\n                        "redaction_count": 0,\n                        "excerpt": (\n                            "safe test log"\n                        ),\n                    },\n                }\n\n            return {\n                "success": True,\n                "source": "kubernetes",\n                "mode": "read_only",\n                "production_signal": True,\n                "observed_at": (\n                    INCIDENT_TIME.isoformat()\n                ),\n                "cluster": kwargs.get(\n                    "cluster"\n                ),\n                "data": {\n                    "phase": "Running",\n                    "ready": True,\n                    "scheduled": True,\n                    "oom_killed": False,\n                    "containers": [],\n                },\n            }\n\n        if name == "prometheus":\n            return {\n                "success": True,\n                "source": "prometheus",\n                "mode": "read_only",\n                "production_signal": True,\n                "observed_at": (\n                    INCIDENT_TIME.isoformat()\n                ),\n                "data": {\n                    "resultType": "vector",\n                    "result": [\n                        {\n                            "metric": {},\n                            "value": [\n                                (\n                                    INCIDENT_TIME\n                                    .timestamp()\n                                ),\n                                "1",\n                            ],\n                        }\n                    ],\n                },\n            }\n\n        raise AssertionError(\n            f"unexpected tool: {name}"\n        )\n\n\ndef test_gateway_parser_preserves_non_demo_production_scope():\n    event = parse_event()\n\n    assert len(\n        event.resources\n    ) == 1\n\n    resource = event.resources[\n        0\n    ]\n\n    assert resource.name == POD\n    assert resource.namespace == NAMESPACE\n    assert resource.cluster == CLUSTER\n\n    serialized = str(\n        event.model_dump(\n            mode="json"\n        )\n    )\n\n    assert "payment-api-6df78" not in serialized\n    assert \'"payment"\' not in serialized\n\n\n@pytest.mark.asyncio\nasync def test_parser_to_investigation_scope_preserves_exact_scope():\n    event = parse_event()\n\n    reasoner = TerminalReasoner()\n    probes = NeverProbeExecutor()\n\n    coordinator = (\n        EvidenceDrivenInvestigationCoordinator(\n            reasoner=reasoner,\n            probe_executor=probes,\n            utc_clock=lambda: INCIDENT_TIME,\n        )\n    )\n\n    result = await coordinator.investigate(\n        SimpleNamespace(\n            event=event,\n            metadata={},\n            tools=None,\n        )\n    )\n\n    assert result.scope.resource == POD\n    assert result.scope.namespace == NAMESPACE\n    assert result.scope.cluster == CLUSTER\n\n    assert len(\n        reasoner.scopes\n    ) == 1\n\n    assert reasoner.scopes[\n        0\n    ] == result.scope\n\n    assert probes.calls == 0\n\n\n@pytest.mark.asyncio\nasync def test_pod_state_probe_forwards_cluster_namespace_and_resource():\n    tools = RecordingTools()\n\n    context = SimpleNamespace(\n        tools=tools,\n        trace=None,\n    )\n\n    scope = InvestigationScope(\n        alert_name="PodRestartHigh",\n        alert_message="restart rate elevated",\n        event_occurred_at=INCIDENT_TIME,\n        resource=POD,\n        namespace=NAMESPACE,\n        cluster=CLUSTER,\n    )\n\n    await (\n        ReadOnlyInvestigationProbeExecutor()\n        .collect(\n            context,\n            scope,\n            InvestigationProbe.KUBERNETES_POD_STATE,\n        )\n    )\n\n    assert tools.calls == [\n        {\n            "name": "kubernetes",\n            "kwargs": {\n                "action": "describe",\n                "resource": "pod",\n                "target": POD,\n                "namespace": NAMESPACE,\n                "cluster": CLUSTER,\n            },\n        }\n    ]\n\n\n@pytest.mark.asyncio\nasync def test_previous_logs_probe_forwards_cluster_namespace_and_resource():\n    tools = RecordingTools()\n\n    context = SimpleNamespace(\n        tools=tools,\n        trace=None,\n    )\n\n    scope = InvestigationScope(\n        alert_name="PodRestartHigh",\n        alert_message="restart rate elevated",\n        event_occurred_at=INCIDENT_TIME,\n        resource=POD,\n        namespace=NAMESPACE,\n        cluster=CLUSTER,\n    )\n\n    await (\n        ReadOnlyInvestigationProbeExecutor()\n        .collect(\n            context,\n            scope,\n            (\n                InvestigationProbe\n                .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n            ),\n        )\n    )\n\n    assert tools.calls == [\n        {\n            "name": "kubernetes",\n            "kwargs": {\n                "action": "previous_logs",\n                "resource": "pod",\n                "target": POD,\n                "namespace": NAMESPACE,\n                "cluster": CLUSTER,\n            },\n        }\n    ]\n\n\n@pytest.mark.asyncio\nasync def test_prometheus_scope_contains_exact_cluster_namespace_and_resource():\n    tools = RecordingTools()\n\n    context = SimpleNamespace(\n        tools=tools,\n        trace=None,\n    )\n\n    scope = InvestigationScope(\n        alert_name="PodRestartHigh",\n        alert_message="restart rate elevated",\n        event_occurred_at=INCIDENT_TIME,\n        resource=POD,\n        namespace=NAMESPACE,\n        cluster=CLUSTER,\n    )\n\n    await (\n        ReadOnlyInvestigationProbeExecutor()\n        .collect(\n            context,\n            scope,\n            (\n                InvestigationProbe\n                .PROMETHEUS_RESTART_COUNT\n            ),\n        )\n    )\n\n    query = tools.calls[\n        0\n    ][\n        "kwargs"\n    ][\n        "query"\n    ]\n\n    assert (\n        f\'pod="{POD}"\'\n        in query\n    )\n\n    assert (\n        f\'namespace="{NAMESPACE}"\'\n        in query\n    )\n\n    assert (\n        f\'cluster="{CLUSTER}"\'\n        in query\n    )\n\n    assert "payment-api" not in query\n    assert \'namespace="payment"\' not in query\n\n\n@pytest.mark.asyncio\nasync def test_kubernetes_tool_rejects_cross_cluster_request_before_http():\n    http_calls = []\n\n    def handler(\n        request: httpx.Request,\n    ) -> httpx.Response:\n        http_calls.append(\n            request\n        )\n\n        return httpx.Response(\n            500,\n            request=request,\n        )\n\n    transport = httpx.MockTransport(\n        handler\n    )\n\n    async with httpx.AsyncClient(\n        transport=transport,\n    ) as client:\n        tool = KubernetesTool(\n            api_url=(\n                "https://sg-cluster.invalid"\n            ),\n            cluster_name=CLUSTER,\n            bearer_token="unit-token",\n            allow_dry_run_fallback=False,\n            client=client,\n            clock=lambda: INCIDENT_TIME,\n        )\n\n        with pytest.raises(\n            KubernetesConfigurationError,\n            match=(\n                "Requested cluster does not match "\n                "configured Kubernetes cluster"\n            ),\n        ):\n            await tool.execute(\n                action="describe",\n                resource="pod",\n                target=OTHER_POD,\n                namespace=OTHER_NAMESPACE,\n                cluster=OTHER_CLUSTER,\n            )\n\n    assert http_calls == []\n\n\n@pytest.mark.asyncio\nasync def test_two_cluster_scopes_do_not_bleed_between_parsed_events():\n    first = parse_event(\n        cluster=CLUSTER,\n        namespace=NAMESPACE,\n        pod=POD,\n    )\n\n    second = parse_event(\n        cluster=OTHER_CLUSTER,\n        namespace=OTHER_NAMESPACE,\n        pod=OTHER_POD,\n    )\n\n    first_resource = (\n        first.resources[\n            0\n        ]\n    )\n\n    second_resource = (\n        second.resources[\n            0\n        ]\n    )\n\n    assert (\n        first_resource.cluster,\n        first_resource.namespace,\n        first_resource.name,\n    ) == (\n        CLUSTER,\n        NAMESPACE,\n        POD,\n    )\n\n    assert (\n        second_resource.cluster,\n        second_resource.namespace,\n        second_resource.name,\n    ) == (\n        OTHER_CLUSTER,\n        OTHER_NAMESPACE,\n        OTHER_POD,\n    )\n\n    assert (\n        first_resource.cluster\n        != second_resource.cluster\n    )\n\n    assert (\n        first_resource.namespace\n        != second_resource.namespace\n    )\n\n    assert (\n        first_resource.name\n        != second_resource.name\n    )\n'
PROBE_TEST_SOURCE = 'from datetime import UTC, datetime\nfrom types import SimpleNamespace\n\nimport pytest\n\nfrom services.agent_runtime.app.investigation.models import (\n    InvestigationProbe,\n    InvestigationScope,\n)\nfrom services.agent_runtime.app.investigation.probes import (\n    ReadOnlyInvestigationProbeExecutor,\n)\n\n\nNOW = datetime(\n    2026,\n    8,\n    9,\n    15,\n    0,\n    tzinfo=UTC,\n)\n\n\nclass FakeToolManager:\n    def __init__(self):\n        self.calls = []\n\n    async def call(\n        self,\n        name,\n        context=None,\n        **kwargs,\n    ):\n        self.calls.append(\n            {\n                "name": name,\n                "context": context,\n                "kwargs": kwargs,\n            }\n        )\n\n        if name == "kubernetes":\n            return {\n                "success": True,\n                "source": "kubernetes",\n                "mode": "read_only",\n                "production_signal": True,\n                "observed_at": NOW.isoformat(),\n                "data": {\n                    "uid": "must-not-be-retained",\n                    "resource_version": "secret-version",\n                    "phase": "Running",\n                    "ready": False,\n                    "scheduled": True,\n                    "oom_killed": True,\n                    "containers": [\n                        {\n                            "restart_count": 7,\n                            "state_reason": (\n                                "CrashLoopBackOff"\n                            ),\n                            "last_termination_reason": (\n                                "OOMKilled"\n                            ),\n                        }\n                    ],\n                },\n            }\n\n        return {\n            "success": True,\n            "source": "prometheus",\n            "mode": "read_only",\n            "production_signal": True,\n            "observed_at": NOW.isoformat(),\n            "query": "must-not-be-retained",\n            "data": {\n                "resultType": "vector",\n                "result": [\n                    {\n                        "metric": {\n                            "pod": "payment-api"\n                        },\n                        "value": [\n                            1786300000,\n                            "123.5",\n                        ],\n                    }\n                ],\n            },\n        }\n\n\ndef scope() -> InvestigationScope:\n    return InvestigationScope(\n        alert_name="PodOOMKilled",\n        alert_message="Pod restarted",\n        resource=\'payment"api\',\n        namespace="team\\\\blue",\n        cluster="prod\\nwest",\n    )\n\n\n@pytest.mark.asyncio\nasync def test_kubernetes_probe_has_fixed_read_only_call():\n    tools = FakeToolManager()\n    context = SimpleNamespace(\n        tools=tools\n    )\n    executor = ReadOnlyInvestigationProbeExecutor()\n\n    evidence = await executor.collect(\n        context,\n        scope(),\n        InvestigationProbe.KUBERNETES_POD_STATE,\n    )\n\n    assert tools.calls == [\n        {\n            "name": "kubernetes",\n            "context": context,\n            "kwargs": {\n                "action": "describe",\n                "resource": "pod",\n                "target": \'payment"api\',\n                "namespace": "team\\\\blue",\n                "cluster": "prod\\nwest",\n            },\n        }\n    ]\n    assert evidence.trusted is True\n    assert evidence.facts["oom_killed"] is True\n    assert evidence.facts["max_restart_count"] == 7\n\n    payload = evidence.model_dump(\n        mode="json"\n    )\n    serialized = str(payload)\n\n    assert "must-not-be-retained" not in serialized\n    assert "secret-version" not in serialized\n\n\n@pytest.mark.asyncio\n@pytest.mark.parametrize(\n    ("probe", "metric"),\n    [\n        (\n            InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,\n            "container_memory_working_set_bytes",\n        ),\n        (\n            InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,\n            "kube_pod_container_resource_limits",\n        ),\n        (\n            InvestigationProbe.PROMETHEUS_RESTART_COUNT,\n            "kube_pod_container_status_restarts_total",\n        ),\n    ],\n)\nasync def test_prometheus_probe_uses_bounded_template(\n    probe,\n    metric,\n):\n    tools = FakeToolManager()\n    context = SimpleNamespace(\n        tools=tools\n    )\n    executor = ReadOnlyInvestigationProbeExecutor()\n\n    evidence = await executor.collect(\n        context,\n        scope(),\n        probe,\n    )\n\n    assert len(tools.calls) == 1\n    call = tools.calls[0]\n    assert call["name"] == "prometheus"\n    assert set(call["kwargs"]) == {"query"}\n\n    query = call["kwargs"]["query"]\n    assert metric in query\n    assert \'pod="payment\\\\"api"\' in query\n    assert \'namespace="team\\\\\\\\blue"\' in query\n    assert \'cluster="prod\\\\nwest"\' in query\n    assert "\\n" not in query\n\n    assert evidence.source == "prometheus"\n    assert evidence.facts["sample_count"] == 1\n    assert evidence.facts["value_sum"] == 123.5\n    assert "query" not in evidence.model_dump()\n\n\n@pytest.mark.asyncio\nasync def test_probe_requires_tool_manager():\n    executor = ReadOnlyInvestigationProbeExecutor()\n\n    with pytest.raises(\n        RuntimeError,\n        match="tools are unavailable",\n    ):\n        await executor.collect(\n            SimpleNamespace(tools=None),\n            scope(),\n            InvestigationProbe.KUBERNETES_POD_STATE,\n        )\n'
LOGS_TEST_SOURCE = 'from __future__ import annotations\n\nfrom datetime import UTC, datetime\nfrom types import SimpleNamespace\nfrom urllib.parse import parse_qs, urlparse\n\nimport httpx\nimport pytest\n\nfrom services.agent_runtime.app.investigation.evidence_time import (\n    InvestigationEvidenceTimePolicy,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    InvestigationProbe,\n    InvestigationScope,\n    InvestigationState,\n)\nfrom services.agent_runtime.app.investigation.probes import (\n    InvestigationProbeResponseError,\n    ReadOnlyInvestigationProbeExecutor,\n)\nfrom services.agent_runtime.app.investigation.reasoner import (\n    LLMInvestigationReasoner,\n)\nfrom services.agent_runtime.app.tools.kubernetes.tool import (\n    KubernetesQueryError,\n    KubernetesTool,\n)\nfrom services.agent_runtime.app.tools.manager import (\n    ToolManager,\n)\nfrom services.agent_runtime.app.tools.registry import (\n    ToolRegistry,\n)\n\n\nNOW = datetime(\n    2026,\n    8,\n    10,\n    13,\n    0,\n    tzinfo=UTC,\n)\n\n\ndef scope() -> InvestigationScope:\n    return InvestigationScope(\n        alert_name="PodRestartHigh",\n        alert_message="payment-api is restarting",\n        event_occurred_at=NOW,\n        resource="payment-api",\n        namespace="payment",\n        cluster="benchmark-lab",\n    )\n\n\ndef pod_payload(\n    *,\n    containers=None,\n):\n    if containers is None:\n        containers = [\n            {\n                "name": "payment-api",\n                "ready": False,\n                "restartCount": 9,\n                "state": {\n                    "waiting": {\n                        "reason": "CrashLoopBackOff",\n                    }\n                },\n                "lastState": {\n                    "terminated": {\n                        "reason": "Error",\n                        "finishedAt": (\n                            "2026-08-10T12:59:30Z"\n                        ),\n                    }\n                },\n                "image": "payment-api:v2",\n                "imageID": "sha256:test",\n            }\n        ]\n\n    return {\n        "apiVersion": "v1",\n        "kind": "Pod",\n        "metadata": {\n            "name": "payment-api",\n            "namespace": "payment",\n            "uid": "pod-uid",\n            "resourceVersion": "123",\n        },\n        "spec": {\n            "nodeName": "worker-1",\n        },\n        "status": {\n            "phase": "Running",\n            "conditions": [],\n            "containerStatuses": containers,\n        },\n    }\n\n\nclass FakeToolManager:\n    def __init__(\n        self,\n        result,\n    ):\n        self.result = result\n        self.calls = []\n\n    async def call(\n        self,\n        name,\n        context=None,\n        **kwargs,\n    ):\n        self.calls.append(\n            {\n                "name": name,\n                "context": context,\n                "kwargs": kwargs,\n            }\n        )\n\n        return self.result\n\n\ndef valid_log_result(\n    *,\n    excerpt=(\n        "2026-08-10T12:59:30Z "\n        "panic: invalid configuration\\n"\n        "password=[REDACTED]"\n    ),\n):\n    return {\n        "success": True,\n        "source": "kubernetes",\n        "mode": "read_only",\n        "production_signal": True,\n        "observed_at": NOW.isoformat(),\n        "action": "previous_logs",\n        "resource": "pod",\n        "target": "payment-api",\n        "namespace": "payment",\n        "cluster": "benchmark-lab",\n        "data": {\n            "container_name": "payment-api",\n            "previous": True,\n            "line_count": 2,\n            "truncated": False,\n            "redaction_count": 1,\n            "excerpt": excerpt,\n        },\n    }\n\n\n@pytest.mark.asyncio\nasync def test_previous_logs_probe_has_fixed_platform_owned_call():\n    result = valid_log_result()\n    tools = FakeToolManager(\n        result\n    )\n    context = SimpleNamespace(\n        tools=tools,\n        trace=None,\n    )\n\n    evidence = await (\n        ReadOnlyInvestigationProbeExecutor()\n        .collect(\n            context,\n            scope(),\n            InvestigationProbe.KUBERNETES_PREVIOUS_CONTAINER_LOGS,\n        )\n    )\n\n    assert tools.calls == [\n        {\n            "name": "kubernetes",\n            "context": context,\n            "kwargs": {\n                "action": "previous_logs",\n                "resource": "pod",\n                "target": "payment-api",\n                "namespace": "payment",\n                "cluster": "benchmark-lab",\n            },\n        }\n    ]\n\n    assert evidence.trusted is True\n    assert evidence.production_signal is True\n    assert evidence.source == "kubernetes"\n    assert (\n        evidence.facts["temporal_basis"]\n        == "previous_container"\n    )\n    assert (\n        evidence.facts["container_name"]\n        == "payment-api"\n    )\n    assert evidence.facts["previous"] is True\n    assert (\n        "panic: invalid configuration"\n        in evidence.facts["log_excerpt"]\n    )\n\n\n@pytest.mark.asyncio\nasync def test_investigation_boundary_redacts_forged_secret_again():\n    secret = "super-secret-value"\n    tools = FakeToolManager(\n        valid_log_result(\n            excerpt=(\n                "panic: startup failure\\n"\n                f"password={secret}\\n"\n                "token=abcdefghijk12345"\n            )\n        )\n    )\n\n    evidence = await (\n        ReadOnlyInvestigationProbeExecutor()\n        .collect(\n            SimpleNamespace(\n                tools=tools,\n                trace=None,\n            ),\n            scope(),\n            InvestigationProbe.KUBERNETES_PREVIOUS_CONTAINER_LOGS,\n        )\n    )\n\n    serialized = str(\n        evidence.model_dump(\n            mode="json"\n        )\n    )\n\n    assert secret not in serialized\n    assert "abcdefghijk12345" not in serialized\n    assert "[REDACTED]" in serialized\n    assert (\n        evidence.facts["redaction_count"]\n        >= 3\n    )\n\n\n@pytest.mark.asyncio\nasync def test_logs_probe_rejects_non_previous_or_oversized_tool_contract():\n    invalid = valid_log_result()\n    invalid["data"] = dict(\n        invalid["data"]\n    )\n    invalid["data"]["previous"] = False\n\n    with pytest.raises(\n        InvestigationProbeResponseError,\n        match="previous-container",\n    ):\n        await (\n            ReadOnlyInvestigationProbeExecutor()\n            .collect(\n                SimpleNamespace(\n                    tools=FakeToolManager(\n                        invalid\n                    ),\n                    trace=None,\n                ),\n                scope(),\n                InvestigationProbe.KUBERNETES_PREVIOUS_CONTAINER_LOGS,\n            )\n        )\n\n    oversized = valid_log_result(\n        excerpt=(\n            "x" * 4001\n        )\n    )\n\n    with pytest.raises(\n        InvestigationProbeResponseError,\n        match="too large",\n    ):\n        await (\n            ReadOnlyInvestigationProbeExecutor()\n            .collect(\n                SimpleNamespace(\n                    tools=FakeToolManager(\n                        oversized\n                    ),\n                    trace=None,\n                ),\n                scope(),\n                InvestigationProbe.KUBERNETES_PREVIOUS_CONTAINER_LOGS,\n            )\n        )\n\n\ndef test_previous_logs_have_distinct_temporal_basis():\n    policy = (\n        InvestigationEvidenceTimePolicy()\n    )\n\n    assert (\n        policy.temporal_basis(\n            scope=scope(),\n            probe=(\n                InvestigationProbe\n                .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n            ),\n        )\n        == "previous_container"\n    )\n\n    assert (\n        policy.query_time(\n            scope=scope(),\n            probe=(\n                InvestigationProbe\n                .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n            ),\n        )\n        is None\n    )\n\n\ndef test_reasoner_exposes_symbolic_log_probe_without_raw_log_parameters():\n    current_scope = scope()\n\n    prompt = (\n        LLMInvestigationReasoner\n        ._build_prompt(\n            scope=current_scope,\n            state=InvestigationState(\n                scope=current_scope\n            ),\n        )\n    )\n\n    assert (\n        "kubernetes_previous_container_logs"\n        in prompt\n    )\n\n    assert "tailLines" not in prompt\n    assert "limitBytes" not in prompt\n    assert "previous=true" not in prompt\n\n\n@pytest.mark.asyncio\nasync def test_kubernetes_previous_logs_are_bounded_redacted_and_fixed():\n    seen = []\n\n    raw_password = (\n        "dont-print-this-password"\n    )\n\n    raw_token = (\n        "abcdefghijk123456789"\n    )\n\n    def handler(\n        request: httpx.Request,\n    ) -> httpx.Response:\n        seen.append(\n            str(\n                request.url\n            )\n        )\n\n        if (\n            request.url.path\n            == (\n                "/api/v1/namespaces/payment/"\n                "pods/payment-api"\n            )\n        ):\n            return httpx.Response(\n                200,\n                json=pod_payload(),\n            )\n\n        if (\n            request.url.path\n            == (\n                "/api/v1/namespaces/payment/"\n                "pods/payment-api/log"\n            )\n        ):\n            return httpx.Response(\n                200,\n                text=(\n                    "2026-08-10T12:59:30Z "\n                    "panic: invalid configuration MAX_CONNECTIONS\\n"\n                    f"password={raw_password}\\n"\n                    f"token={raw_token}\\n"\n                    "Authorization: Bearer abcdefghijklmnop"\n                ),\n                headers={\n                    "content-type": (\n                        "text/plain; charset=utf-8"\n                    )\n                },\n            )\n\n        return httpx.Response(\n            404\n        )\n\n    transport = httpx.MockTransport(\n        handler\n    )\n\n    async with httpx.AsyncClient(\n        transport=transport\n    ) as client:\n        tool = KubernetesTool(\n            api_url="https://kubernetes.test",\n            bearer_token="test-token",\n            cluster_name="benchmark-lab",\n            allow_dry_run_fallback=False,\n            client=client,\n            clock=lambda: NOW,\n        )\n\n        result = await tool.execute(\n            action="previous_logs",\n            resource="pod",\n            target="payment-api",\n            namespace="payment",\n            # These untrusted extras are deliberately ignored.\n            container="attacker-selected",\n            tail_lines=999999,\n        )\n\n    assert len(\n        seen\n    ) == 2\n\n    parsed = urlparse(\n        seen[1]\n    )\n\n    params = parse_qs(\n        parsed.query\n    )\n\n    assert params == {\n        "container": [\n            "payment-api"\n        ],\n        "previous": [\n            "true"\n        ],\n        "tailLines": [\n            "80"\n        ],\n        "limitBytes": [\n            "16384"\n        ],\n        "timestamps": [\n            "true"\n        ],\n    }\n\n    assert (\n        result["source"]\n        == "kubernetes"\n    )\n    assert (\n        result["mode"]\n        == "read_only"\n    )\n    assert (\n        result["production_signal"]\n        is True\n    )\n\n    data = result["data"]\n\n    assert (\n        data["container_name"]\n        == "payment-api"\n    )\n    assert data["previous"] is True\n    assert (\n        "panic: invalid configuration"\n        in data["excerpt"]\n    )\n    assert raw_password not in str(\n        result\n    )\n    assert raw_token not in str(\n        result\n    )\n    assert (\n        "abcdefghijklmnop"\n        not in str(\n            result\n        )\n    )\n    assert "[REDACTED]" in (\n        data["excerpt"]\n    )\n    assert (\n        data["redaction_count"]\n        >= 3\n    )\n\n\n@pytest.mark.asyncio\nasync def test_kubernetes_previous_logs_fail_closed_on_ambiguous_container():\n    containers = [\n        {\n            "name": "app",\n            "restartCount": 3,\n            "lastState": {\n                "terminated": {\n                    "reason": "Error"\n                }\n            },\n        },\n        {\n            "name": "sidecar",\n            "restartCount": 2,\n            "lastState": {\n                "terminated": {\n                    "reason": "Error"\n                }\n            },\n        },\n    ]\n\n    log_calls = 0\n\n    def handler(\n        request: httpx.Request,\n    ) -> httpx.Response:\n        nonlocal log_calls\n\n        if request.url.path.endswith(\n            "/log"\n        ):\n            log_calls += 1\n\n        return httpx.Response(\n            200,\n            json=pod_payload(\n                containers=containers\n            ),\n        )\n\n    transport = httpx.MockTransport(\n        handler\n    )\n\n    async with httpx.AsyncClient(\n        transport=transport\n    ) as client:\n        tool = KubernetesTool(\n            api_url="https://kubernetes.test",\n            bearer_token="test-token",\n            allow_dry_run_fallback=False,\n            client=client,\n            clock=lambda: NOW,\n        )\n\n        with pytest.raises(\n            KubernetesQueryError,\n            match="selection is ambiguous",\n        ):\n            await tool.execute(\n                action="previous_logs",\n                resource="pod",\n                target="payment-api",\n                namespace="payment",\n            )\n\n    assert log_calls == 0\n\n\n@pytest.mark.asyncio\nasync def test_real_tool_manager_path_returns_trusted_redacted_log_evidence():\n    raw_secret = (\n        "another-secret-value"\n    )\n\n    def handler(\n        request: httpx.Request,\n    ) -> httpx.Response:\n        if request.url.path.endswith(\n            "/log"\n        ):\n            return httpx.Response(\n                200,\n                text=(\n                    "panic: invalid configuration\\n"\n                    f"client_secret={raw_secret}"\n                ),\n            )\n\n        return httpx.Response(\n            200,\n            json=pod_payload(),\n        )\n\n    transport = httpx.MockTransport(\n        handler\n    )\n\n    async with httpx.AsyncClient(\n        transport=transport\n    ) as client:\n        registry = ToolRegistry()\n        registry.register(\n            KubernetesTool(\n                api_url="https://kubernetes.test",\n                bearer_token="test-token",\n                cluster_name="benchmark-lab",\n                allow_dry_run_fallback=False,\n                client=client,\n                clock=lambda: NOW,\n            )\n        )\n\n        manager = ToolManager(\n            registry\n        )\n\n        context = SimpleNamespace(\n            tools=manager,\n            trace=None,\n        )\n\n        evidence = await (\n            ReadOnlyInvestigationProbeExecutor()\n            .collect(\n                context,\n                scope(),\n                (\n                    InvestigationProbe\n                    .KUBERNETES_PREVIOUS_CONTAINER_LOGS\n                ),\n            )\n        )\n\n    serialized = str(\n        evidence.model_dump(\n            mode="json"\n        )\n    )\n\n    assert evidence.trusted is True\n    assert raw_secret not in serialized\n    assert (\n        "panic: invalid configuration"\n        in evidence.facts["log_excerpt"]\n    )\n'


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
                f"{relative} changed after the reviewed rolled-back baseline. "
                f"expected_sha256={expected} actual_sha256={actual}. "
                "Refusing stale Production Scope Integrity v1.1 installation."
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

    probes_file = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "investigation"
        / "probes.py"
    )

    kubernetes_file = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "tools"
        / "kubernetes"
        / "tool.py"
    )

    scope_test_file = (
        root
        / "services"
        / "agent_runtime"
        / "tests"
        / "test_production_scope_integrity.py"
    )

    probe_test_file = (
        root
        / "services"
        / "agent_runtime"
        / "tests"
        / "test_investigation_probes.py"
    )

    logs_test_file = (
        root
        / "services"
        / "agent_runtime"
        / "tests"
        / "test_investigation_logs.py"
    )

    sources = {
        probes_file: PROBES_SOURCE,
        kubernetes_file: KUBERNETES_TOOL_SOURCE,
        scope_test_file: SCOPE_TEST_SOURCE,
        probe_test_file: PROBE_TEST_SOURCE,
        logs_test_file: LOGS_TEST_SOURCE,
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
        "Production Scope Integrity Contract v1.1",
        f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
        "",
        "v1 failure diagnosis:",
        "- production changes compiled successfully",
        "- rollback completed successfully",
        "- new Previous Logs fixture used obsolete log_line_count/log_excerpt field names",
        "- two existing tests correctly failed because their call expectations did not yet include cluster scope",
        "- services/gateway/tests/test_parser.py has an unrelated existing severity.value assertion mismatch and is not modified by this installer",
        "",
        "v1.1 contract:",
        "- arbitrary Alertmanager cluster/namespace/pod survives Parser -> StandardEvent -> InvestigationScope",
        "- Pod State receives resource + namespace + cluster",
        "- Previous Logs receives resource + namespace + cluster",
        "- Prometheus query retains resource + namespace + cluster",
        "- KubernetesTool rejects configured/requested cluster mismatch before HTTP",
        "- two distinct cluster events cannot bleed scope",
        "",
        "Test compatibility:",
        "- existing Pod State call-contract test is updated to require cluster",
        "- existing Previous Logs call-contract test is updated to require cluster",
        "- Gateway scope is tested directly in the new scope-integrity suite without changing Gateway production code",
        "",
        "Safety:",
        "- no multi-cluster router is introduced yet",
        "- no write Kubernetes verb added",
        "- no Action / Approval / Verification change",
        "- no real LLM/Kubernetes/Prometheus request",
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
                "Production Scope Integrity v1.1 syntax failed"
            )

        focused = run_command(
            root=root,
            name="Production Scope Integrity focused suite",
            command=[
                "uv",
                "run",
                "pytest",
                (
                    "services/agent_runtime/tests/"
                    "test_production_scope_integrity.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_probes.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_logs.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_kubernetes_tool.py"
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
                "Production Scope Integrity v1.1 focused tests failed"
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
                    "test_investigation_config_change_capability.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_change_capability.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_change_rollout_evidence.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_evidence_consistency.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_final_synthesis_budget_discipline.py"
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
                "Production Scope Integrity v1.1 compatibility tests failed"
            )

        preflight = run_command(
            root=root,
            name="Scope propagation preflight",
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
                    "print('cluster_scope_calls='+str(p.count('cluster=scope.cluster'))); "
                    "print('kubernetes_cluster_parameter='+str('cluster: str | None = None' in k)); "
                    "print('cluster_mismatch_guard='+str('Requested cluster does not match configured Kubernetes cluster' in k)); "
                    "assert p.count('cluster=scope.cluster')>=4; "
                    "assert 'cluster: str | None = None' in k; "
                    "assert 'Requested cluster does not match configured Kubernetes cluster' in k"
                ),
            ],
        )

        add_command(
            report,
            preflight,
        )

        if preflight.returncode != 0:
            raise RuntimeError(
                "Production Scope Integrity v1.1 preflight failed"
            )

        authority = run_command(
            root=root,
            name="Read-only authority boundary",
            command=[
                "uv",
                "run",
                "python",
                "-c",
                (
                    "from pathlib import Path; "
                    "files=["
                    "Path(r'services/agent_runtime/app/investigation/probes.py'),"
                    "Path(r'services/agent_runtime/app/tools/kubernetes/tool.py')"
                    "]; "
                    "s='\\n'.join(x.read_text(encoding='utf-8') for x in files); "
                    "bad=[x for x in ['ActionRuntime','ApprovalService','VerificationRuntime',"
                    "'.post(','.patch(','.put(','.delete('] if x in s]; "
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
                "Production Scope Integrity v1.1 authority boundary failed"
            )

        section(
            report,
            "RESULT",
        )

        report.extend(
            [
                "PASSED",
                "",
                "Production Scope Integrity Contract v1.1 is installed.",
                "",
                "Current guarantee:",
                "- Event scope is not tied to payment/payment-api fixtures",
                "- Kubernetes Pod State and Previous Logs retain cluster identity",
                "- a cluster-bound KubernetesTool cannot silently query a different cluster",
                "",
                "Still intentionally not implemented:",
                "- multi-cluster Kubernetes client routing",
                "",
                "Next architecture step after this contract:",
                "- Cluster Registry + Multi-Cluster Kubernetes Tool Router v1",
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
            "PRODUCTION SCOPE INTEGRITY CONTRACT V1.1 PASSED"
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
                    "Production Scope Integrity Contract v1.1 FAILED",
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
            "PRODUCTION SCOPE INTEGRITY CONTRACT V1.1 FAILED"
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
