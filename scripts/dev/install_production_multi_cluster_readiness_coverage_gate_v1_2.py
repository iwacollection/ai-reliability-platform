from __future__ import annotations

import hashlib
import shutil
import subprocess
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


VERSION = "production-multi-cluster-readiness-coverage-gate-v1.2"

AFTER_NAME = (
    "production_multi_cluster_readiness_coverage_gate_v1_2_after.txt"
)

ERROR_NAME = (
    "production_multi_cluster_readiness_coverage_gate_v1_2_error.txt"
)

EXPECTED_RAW_HASHES = {'services/agent_runtime/app/runtime/runtime.py': 'a1bc796965f9424a2ad4aba28534602ce2c6284884e451ab29621e3088943fc4'}

READINESS_SOURCE = 'from __future__ import annotations\n\nfrom dataclasses import (\n    asdict,\n    dataclass,\n    replace,\n)\nfrom pathlib import Path\nfrom typing import Any\nfrom urllib.parse import urlparse\n\nfrom services.agent_runtime.app.investigation.models import (\n    InvestigationProbe,\n    default_investigation_probes,\n)\nfrom services.agent_runtime.app.tools.kubernetes.router import (\n    KubernetesClusterRegistry,\n    MultiClusterKubernetesChangeToolRouter,\n    MultiClusterKubernetesToolRouter,\n)\nfrom services.agent_runtime.app.tools.manager import (\n    ToolManager,\n)\nfrom services.agent_runtime.app.tools.prometheus.router import (\n    MultiClusterPrometheusToolRouter,\n    PrometheusClusterRegistry,\n)\n\n\nclass ProductionMultiClusterReadinessError(\n    RuntimeError\n):\n    """\n    Production Shadow cannot prove complete multi-cluster read coverage.\n    """\n\n\n@dataclass(\n    frozen=True,\n    slots=True,\n)\nclass ProductionClusterReadinessReport:\n    schema_version: str\n    read_only: bool\n    decision_influence: bool\n    applicable: bool\n    ready: bool\n    cluster: str | None\n    strict_evidence_required: bool\n    kubernetes_registry_present: bool\n    prometheus_registry_present: bool\n    kubernetes_route_ready: bool\n    kubernetes_change_route_ready: bool\n    prometheus_route_ready: bool\n    required_investigation_probes: tuple[\n        str,\n        ...,\n    ]\n    covered_investigation_probes: tuple[\n        str,\n        ...,\n    ]\n    required_verification_tools: tuple[\n        str,\n        ...,\n    ]\n    covered_verification_tools: tuple[\n        str,\n        ...,\n    ]\n    issues: tuple[\n        str,\n        ...,\n    ]\n\n    def snapshot(\n        self,\n    ) -> dict[str, Any]:\n        value = asdict(\n            self\n        )\n\n        value[\n            "required_investigation_probes"\n        ] = list(\n            self.required_investigation_probes\n        )\n\n        value[\n            "covered_investigation_probes"\n        ] = list(\n            self.covered_investigation_probes\n        )\n\n        value[\n            "required_verification_tools"\n        ] = list(\n            self.required_verification_tools\n        )\n\n        value[\n            "covered_verification_tools"\n        ] = list(\n            self.covered_verification_tools\n        )\n\n        value[\n            "issues"\n        ] = list(\n            self.issues\n        )\n\n        return value\n\n\n@dataclass(\n    frozen=True,\n    slots=True,\n)\nclass ProductionMultiClusterCoverageReport:\n    schema_version: str\n    read_only: bool\n    decision_influence: bool\n    applicable: bool\n    ready: bool\n    clusters: tuple[\n        ProductionClusterReadinessReport,\n        ...,\n    ]\n    issues: tuple[\n        str,\n        ...,\n    ]\n\n    def snapshot(\n        self,\n    ) -> dict[str, Any]:\n        return {\n            "schema_version": self.schema_version,\n            "read_only": self.read_only,\n            "decision_influence": (\n                self.decision_influence\n            ),\n            "applicable": self.applicable,\n            "ready": self.ready,\n            "clusters": [\n                item.snapshot()\n                for item in self.clusters\n            ],\n            "issues": list(\n                self.issues\n            ),\n        }\n\n\nclass ProductionMultiClusterReadinessGate:\n    """\n    Static production-read coverage proof.\n\n    The Gate performs no Kubernetes or Prometheus request. It inspects only\n    already-built registries, Router identity, endpoint hardening, and the\n    required Investigation/Verification provider coverage.\n\n    A production-ready cluster must have:\n    - exact Kubernetes binding,\n    - exact Prometheus binding,\n    - Runtime ToolManager wired to those exact registries,\n    - hardened live-read child Tools,\n    - complete default Investigation probe-family coverage,\n    - Kubernetes + Prometheus coverage for current required Verification.\n\n    The current default Investigation probe list is read from\n    default_investigation_probes() instead of copied into this module.\n    """\n\n    _REQUIRED_VERIFICATION_TOOLS = (\n        "kubernetes",\n        "prometheus",\n    )\n\n    def __init__(\n        self,\n        *,\n        kubernetes_cluster_registry: (\n            KubernetesClusterRegistry\n            | None\n        ),\n        prometheus_cluster_registry: (\n            PrometheusClusterRegistry\n            | None\n        ),\n        tools: ToolManager,\n        strict_evidence_required: bool,\n    ) -> None:\n        if (\n            kubernetes_cluster_registry\n            is not None\n            and not isinstance(\n                kubernetes_cluster_registry,\n                KubernetesClusterRegistry,\n            )\n        ):\n            raise TypeError(\n                "Production readiness Kubernetes registry is invalid"\n            )\n\n        if (\n            prometheus_cluster_registry\n            is not None\n            and not isinstance(\n                prometheus_cluster_registry,\n                PrometheusClusterRegistry,\n            )\n        ):\n            raise TypeError(\n                "Production readiness Prometheus registry is invalid"\n            )\n\n        if not isinstance(\n            tools,\n            ToolManager,\n        ):\n            raise TypeError(\n                "Production readiness ToolManager is invalid"\n            )\n\n        if not isinstance(\n            strict_evidence_required,\n            bool,\n        ):\n            raise TypeError(\n                "Production readiness strict-evidence policy is invalid"\n            )\n\n        self.kubernetes_cluster_registry = (\n            kubernetes_cluster_registry\n        )\n\n        self.prometheus_cluster_registry = (\n            prometheus_cluster_registry\n        )\n\n        self.tools = tools\n\n        self.strict_evidence_required = (\n            strict_evidence_required\n        )\n\n    @property\n    def applicable(\n        self,\n    ) -> bool:\n        return (\n            self.kubernetes_cluster_registry\n            is not None\n            or self.prometheus_cluster_registry\n            is not None\n        )\n\n    def evaluate_event(\n        self,\n        event: Any,\n    ) -> ProductionClusterReadinessReport:\n        cluster, scope_issues = (\n            self._cluster_from_event(\n                event\n            )\n        )\n\n        report = self.evaluate(\n            cluster\n        )\n\n        if not scope_issues:\n            return report\n\n        issues = self._unique(\n            (\n                *scope_issues,\n                *report.issues,\n            )\n        )\n\n        return replace(\n            report,\n            ready=False,\n            issues=issues,\n        )\n\n    def evaluate(\n        self,\n        cluster: str | None,\n    ) -> ProductionClusterReadinessReport:\n        issues: list[str] = []\n\n        if not self.applicable:\n            issues.append(\n                "multi_cluster_read_plane_inactive"\n            )\n\n        normalized_cluster = (\n            self._normalize_cluster(\n                cluster\n            )\n        )\n\n        if normalized_cluster is None:\n            issues.append(\n                "incident_cluster_missing"\n            )\n\n        if not self.strict_evidence_required:\n            issues.append(\n                "cluster_verified_evidence_policy_inactive"\n            )\n\n        kubernetes_present = (\n            self.kubernetes_cluster_registry\n            is not None\n        )\n\n        prometheus_present = (\n            self.prometheus_cluster_registry\n            is not None\n        )\n\n        if not kubernetes_present:\n            issues.append(\n                "kubernetes_registry_missing"\n            )\n\n        if not prometheus_present:\n            issues.append(\n                "prometheus_registry_missing"\n            )\n\n        kubernetes_route_ready = False\n        kubernetes_change_route_ready = False\n        prometheus_route_ready = False\n\n        if (\n            kubernetes_present\n            and normalized_cluster\n            is not None\n        ):\n            (\n                kubernetes_route_ready,\n                kubernetes_change_route_ready,\n                kubernetes_issues,\n            ) = self._check_kubernetes(\n                normalized_cluster\n            )\n\n            issues.extend(\n                kubernetes_issues\n            )\n\n        if (\n            prometheus_present\n            and normalized_cluster\n            is not None\n        ):\n            (\n                prometheus_route_ready,\n                prometheus_issues,\n            ) = self._check_prometheus(\n                normalized_cluster\n            )\n\n            issues.extend(\n                prometheus_issues\n            )\n\n        required_probes = tuple(\n            probe.value\n            for probe\n            in default_investigation_probes()\n        )\n\n        covered_probes: list[str] = []\n\n        for probe in default_investigation_probes():\n            provider = (\n                self._investigation_probe_provider(\n                    probe\n                )\n            )\n\n            if provider == "kubernetes":\n                if kubernetes_route_ready:\n                    covered_probes.append(\n                        probe.value\n                    )\n\n                continue\n\n            if provider == "prometheus":\n                if prometheus_route_ready:\n                    covered_probes.append(\n                        probe.value\n                    )\n\n                continue\n\n            issues.append(\n                "unsupported_required_investigation_probe:"\n                + probe.value\n            )\n\n        if len(\n            covered_probes\n        ) != len(\n            required_probes\n        ):\n            issues.append(\n                "required_investigation_probe_coverage_incomplete"\n            )\n\n        covered_verification_tools: list[\n            str\n        ] = []\n\n        if kubernetes_route_ready:\n            covered_verification_tools.append(\n                "kubernetes"\n            )\n\n        if prometheus_route_ready:\n            covered_verification_tools.append(\n                "prometheus"\n            )\n\n        if tuple(\n            covered_verification_tools\n        ) != self._REQUIRED_VERIFICATION_TOOLS:\n            issues.append(\n                "required_verification_tool_coverage_incomplete"\n            )\n\n        normalized_issues = self._unique(\n            issues\n        )\n\n        ready = (\n            self.applicable\n            and normalized_cluster\n            is not None\n            and self.strict_evidence_required\n            and kubernetes_route_ready\n            and kubernetes_change_route_ready\n            and prometheus_route_ready\n            and len(\n                covered_probes\n            )\n            == len(\n                required_probes\n            )\n            and tuple(\n                covered_verification_tools\n            )\n            == self._REQUIRED_VERIFICATION_TOOLS\n            and not normalized_issues\n        )\n\n        return ProductionClusterReadinessReport(\n            schema_version="v1",\n            read_only=True,\n            decision_influence=False,\n            applicable=self.applicable,\n            ready=ready,\n            cluster=normalized_cluster,\n            strict_evidence_required=(\n                self.strict_evidence_required\n            ),\n            kubernetes_registry_present=(\n                kubernetes_present\n            ),\n            prometheus_registry_present=(\n                prometheus_present\n            ),\n            kubernetes_route_ready=(\n                kubernetes_route_ready\n            ),\n            kubernetes_change_route_ready=(\n                kubernetes_change_route_ready\n            ),\n            prometheus_route_ready=(\n                prometheus_route_ready\n            ),\n            required_investigation_probes=(\n                required_probes\n            ),\n            covered_investigation_probes=tuple(\n                covered_probes\n            ),\n            required_verification_tools=(\n                self._REQUIRED_VERIFICATION_TOOLS\n            ),\n            covered_verification_tools=tuple(\n                covered_verification_tools\n            ),\n            issues=normalized_issues,\n        )\n\n    def evaluate_all(\n        self,\n    ) -> ProductionMultiClusterCoverageReport:\n        issues: list[str] = []\n\n        kubernetes_names = (\n            set(\n                self.kubernetes_cluster_registry\n                .cluster_names\n            )\n            if self.kubernetes_cluster_registry\n            is not None\n            else set()\n        )\n\n        prometheus_names = (\n            set(\n                self.prometheus_cluster_registry\n                .cluster_names\n            )\n            if self.prometheus_cluster_registry\n            is not None\n            else set()\n        )\n\n        cluster_names = sorted(\n            kubernetes_names\n            | prometheus_names\n        )\n\n        if not self.applicable:\n            issues.append(\n                "multi_cluster_read_plane_inactive"\n            )\n\n        if (\n            self.applicable\n            and kubernetes_names\n            != prometheus_names\n        ):\n            issues.append(\n                "cluster_registry_coverage_sets_differ"\n            )\n\n        if (\n            self.applicable\n            and not cluster_names\n        ):\n            issues.append(\n                "no_cluster_bindings"\n            )\n\n        reports = tuple(\n            self.evaluate(\n                cluster\n            )\n            for cluster in cluster_names\n        )\n\n        if any(\n            not report.ready\n            for report in reports\n        ):\n            issues.append(\n                "one_or_more_clusters_not_ready"\n            )\n\n        normalized_issues = self._unique(\n            issues\n        )\n\n        ready = (\n            self.applicable\n            and bool(\n                reports\n            )\n            and not normalized_issues\n            and all(\n                report.ready\n                for report in reports\n            )\n        )\n\n        return ProductionMultiClusterCoverageReport(\n            schema_version="v1",\n            read_only=True,\n            decision_influence=False,\n            applicable=self.applicable,\n            ready=ready,\n            clusters=reports,\n            issues=normalized_issues,\n        )\n\n    def assert_event_ready(\n        self,\n        event: Any,\n    ) -> ProductionClusterReadinessReport:\n        report = self.evaluate_event(\n            event\n        )\n\n        if not report.ready:\n            raise ProductionMultiClusterReadinessError(\n                "Production multi-cluster read coverage is not ready"\n            )\n\n        return report\n\n    def _check_kubernetes(\n        self,\n        cluster: str,\n    ) -> tuple[\n        bool,\n        bool,\n        tuple[str, ...],\n    ]:\n        issues: list[str] = []\n\n        assert (\n            self.kubernetes_cluster_registry\n            is not None\n        )\n\n        try:\n            child = (\n                self.kubernetes_cluster_registry\n                .resolve(\n                    cluster\n                )\n            )\n\n        except Exception:\n            return (\n                False,\n                False,\n                (\n                    "kubernetes_cluster_binding_missing",\n                ),\n            )\n\n        if child.cluster_name != cluster:\n            issues.append(\n                "kubernetes_child_cluster_identity_invalid"\n            )\n\n        if not self._clean_https_origin(\n            child.api_url\n        ):\n            issues.append(\n                "kubernetes_https_endpoint_required"\n            )\n\n        if child.verify_tls is not True:\n            issues.append(\n                "kubernetes_tls_verification_required"\n            )\n\n        if (\n            child.allow_dry_run_fallback\n            is not False\n        ):\n            issues.append(\n                "kubernetes_dry_run_fallback_must_be_disabled"\n            )\n\n        token = getattr(\n            child,\n            "bearer_token",\n            None,\n        )\n\n        if (\n            not isinstance(\n                token,\n                str,\n            )\n            or not token.strip()\n        ):\n            issues.append(\n                "kubernetes_credential_unresolved"\n            )\n\n        ca_file = getattr(\n            child,\n            "ca_file",\n            None,\n        )\n\n        if (\n            ca_file is not None\n            and not Path(\n                ca_file\n            ).is_file()\n        ):\n            issues.append(\n                "kubernetes_ca_file_unavailable"\n            )\n\n        kubernetes_router_ready = False\n        kubernetes_change_router_ready = False\n\n        try:\n            router = (\n                self.tools.registry.get(\n                    "kubernetes"\n                )\n            )\n\n            kubernetes_router_ready = (\n                isinstance(\n                    router,\n                    MultiClusterKubernetesToolRouter,\n                )\n                and router.clusters\n                is self.kubernetes_cluster_registry\n            )\n\n        except Exception:\n            kubernetes_router_ready = False\n\n        if not kubernetes_router_ready:\n            issues.append(\n                "kubernetes_runtime_router_mismatch"\n            )\n\n        try:\n            change_router = (\n                self.tools.registry.get(\n                    "kubernetes_change"\n                )\n            )\n\n            kubernetes_change_router_ready = (\n                isinstance(\n                    change_router,\n                    MultiClusterKubernetesChangeToolRouter,\n                )\n                and change_router.clusters\n                is self.kubernetes_cluster_registry\n            )\n\n        except Exception:\n            kubernetes_change_router_ready = False\n\n        if not kubernetes_change_router_ready:\n            issues.append(\n                "kubernetes_change_runtime_router_mismatch"\n            )\n\n        child_ready = not any(\n            issue\n            for issue in issues\n            if issue.startswith(\n                "kubernetes_"\n            )\n            and issue\n            not in {\n                "kubernetes_runtime_router_mismatch",\n                "kubernetes_change_runtime_router_mismatch",\n            }\n        )\n\n        return (\n            (\n                child_ready\n                and kubernetes_router_ready\n            ),\n            (\n                child_ready\n                and kubernetes_change_router_ready\n            ),\n            self._unique(\n                issues\n            ),\n        )\n\n    def _check_prometheus(\n        self,\n        cluster: str,\n    ) -> tuple[\n        bool,\n        tuple[str, ...],\n    ]:\n        issues: list[str] = []\n\n        assert (\n            self.prometheus_cluster_registry\n            is not None\n        )\n\n        try:\n            (\n                selected_cluster,\n                child,\n            ) = (\n                self.prometheus_cluster_registry\n                .resolve(\n                    cluster\n                )\n            )\n\n        except Exception:\n            return (\n                False,\n                (\n                    "prometheus_cluster_binding_missing",\n                ),\n            )\n\n        if selected_cluster != cluster:\n            issues.append(\n                "prometheus_child_cluster_identity_invalid"\n            )\n\n        if not self._clean_https_origin(\n            child.base_url\n        ):\n            issues.append(\n                "prometheus_https_endpoint_required"\n            )\n\n        if child.verify_tls is not True:\n            issues.append(\n                "prometheus_tls_verification_required"\n            )\n\n        if (\n            child.allow_mock_fallback\n            is not False\n        ):\n            issues.append(\n                "prometheus_mock_fallback_must_be_disabled"\n            )\n\n        ca_file = getattr(\n            child,\n            "ca_file",\n            None,\n        )\n\n        if (\n            ca_file is not None\n            and not Path(\n                ca_file\n            ).is_file()\n        ):\n            issues.append(\n                "prometheus_ca_file_unavailable"\n            )\n\n        router_ready = False\n\n        try:\n            router = (\n                self.tools.registry.get(\n                    "prometheus"\n                )\n            )\n\n            router_ready = (\n                isinstance(\n                    router,\n                    MultiClusterPrometheusToolRouter,\n                )\n                and router.clusters\n                is self.prometheus_cluster_registry\n            )\n\n        except Exception:\n            router_ready = False\n\n        if not router_ready:\n            issues.append(\n                "prometheus_runtime_router_mismatch"\n            )\n\n        child_ready = not any(\n            issue\n            for issue in issues\n            if issue.startswith(\n                "prometheus_"\n            )\n            and issue\n            != "prometheus_runtime_router_mismatch"\n        )\n\n        return (\n            (\n                child_ready\n                and router_ready\n            ),\n            self._unique(\n                issues\n            ),\n        )\n\n    @staticmethod\n    def _investigation_probe_provider(\n        probe: InvestigationProbe,\n    ) -> str | None:\n        value = probe.value\n\n        if value.startswith(\n            "kubernetes_"\n        ):\n            return "kubernetes"\n\n        if value.startswith(\n            "prometheus_"\n        ):\n            return "prometheus"\n\n        return None\n\n    @staticmethod\n    def _cluster_from_event(\n        event: Any,\n    ) -> tuple[\n        str | None,\n        tuple[str, ...],\n    ]:\n        resources = getattr(\n            event,\n            "resources",\n            None,\n        )\n\n        if not isinstance(\n            resources,\n            (\n                list,\n                tuple,\n            ),\n        ) or not resources:\n            return (\n                None,\n                (\n                    "incident_resource_missing",\n                ),\n            )\n\n        clusters = set()\n\n        for resource in resources:\n            value = getattr(\n                resource,\n                "cluster",\n                None,\n            )\n\n            if value is None:\n                continue\n\n            normalized = str(\n                value\n            ).strip()\n\n            if normalized:\n                clusters.add(\n                    normalized\n                )\n\n        if not clusters:\n            return (\n                None,\n                (\n                    "incident_cluster_missing",\n                ),\n            )\n\n        if len(\n            clusters\n        ) != 1:\n            return (\n                None,\n                (\n                    "incident_cluster_ambiguous",\n                ),\n            )\n\n        return (\n            next(\n                iter(\n                    clusters\n                )\n            ),\n            (),\n        )\n\n    @staticmethod\n    def _normalize_cluster(\n        value: Any,\n    ) -> str | None:\n        if value is None:\n            return None\n\n        if not isinstance(\n            value,\n            str,\n        ):\n            return None\n\n        normalized = value.strip()\n\n        if (\n            not normalized\n            or normalized != value\n            or len(\n                normalized\n            )\n            > 256\n            or "\\x00" in normalized\n        ):\n            return None\n\n        return normalized\n\n    @staticmethod\n    def _clean_https_origin(\n        value: Any,\n    ) -> bool:\n        if not isinstance(\n            value,\n            str,\n        ):\n            return False\n\n        parsed = urlparse(\n            value\n        )\n\n        return (\n            parsed.scheme == "https"\n            and bool(\n                parsed.netloc\n            )\n            and parsed.username is None\n            and parsed.password is None\n            and not parsed.query\n            and not parsed.fragment\n            and parsed.path in {\n                "",\n                "/",\n            }\n        )\n\n    @staticmethod\n    def _unique(\n        values,\n    ) -> tuple[str, ...]:\n        return tuple(\n            dict.fromkeys(\n                str(\n                    value\n                )\n                for value in values\n            )\n        )\n\n\n__all__ = [\n    "ProductionClusterReadinessReport",\n    "ProductionMultiClusterCoverageReport",\n    "ProductionMultiClusterReadinessError",\n    "ProductionMultiClusterReadinessGate",\n]\n'
RUNTIME_SOURCE = 'from copy import deepcopy\n\nfrom services.agent_runtime.app.registry.factory import (\n    create_agent_registry,\n)\nfrom services.agent_runtime.app.llm.gateway.factory import (\n    create_llm_gateway,\n)\nfrom services.agent_runtime.app.llm.gateway.gateway import (\n    LLMGateway,\n)\nfrom services.agent_runtime.app.planner.agent_planner import (\n    AgentPlanner,\n)\nfrom services.agent_runtime.app.pipeline.planner_pipeline import (\n    PlannerPipeline,\n)\nfrom services.agent_runtime.app.memory.store import (\n    MemoryStore,\n)\nfrom services.agent_runtime.app.tools.factory import (\n    create_tool_manager,\n)\nfrom services.agent_runtime.app.tools.kubernetes.router import (\n    KubernetesClusterRegistry,\n)\nfrom services.agent_runtime.app.tools.kubernetes.connection_factory import (\n    create_kubernetes_cluster_registry,\n)\nfrom services.agent_runtime.app.tools.prometheus.router import (\n    PrometheusClusterRegistry,\n)\nfrom services.agent_runtime.app.tools.prometheus.connection_factory import (\n    create_prometheus_cluster_registry,\n)\nfrom services.agent_runtime.app.skills.factory import (\n    create_skill_registry,\n)\nfrom services.agent_runtime.app.mcp.factory import (\n    create_mcp_registry,\n)\nfrom services.agent_runtime.app.observability.collector import (\n    TraceCollector,\n)\nfrom services.agent_runtime.app.evaluation.factory import (\n    create_evaluation_registry,\n)\nfrom services.agent_runtime.app.policy.factory import (\n    create_policy_engine,\n)\nfrom services.agent_runtime.app.approval.service import (\n    ApprovalService,\n)\nfrom services.agent_runtime.app.incident.store import (\n    IncidentStore,\n)\nfrom services.agent_runtime.app.incident.service import (\n    IncidentService,\n)\nfrom services.agent_runtime.app.investigation.comparison import (\n    build_rca_investigation_comparison,\n)\nfrom services.agent_runtime.app.investigation.factory import (\n    create_investigation_coordinator,\n)\nfrom services.agent_runtime.app.investigation.llm_gateway_adapter import (\n    InvestigationLLMGatewayAdapter,\n)\nfrom services.agent_runtime.app.investigation.reasoner import (\n    BaseInvestigationReasoner,\n    LLMInvestigationReasoner,\n)\nfrom services.agent_runtime.app.investigation.settings import (\n    InvestigationSettings,\n)\nfrom services.agent_runtime.app.investigation.multi_cluster_readiness import (\n    ProductionMultiClusterReadinessError,\n    ProductionMultiClusterReadinessGate,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    InvestigationState,\n)\nfrom services.agent_runtime.app.model.context import (\n    AgentContext,\n)\nfrom services.agent_runtime.app.workflow.service import (\n    WorkflowService,\n)\nfrom services.agent_runtime.app.action.execution_service import (\n    ActionExecutionService,\n)\nfrom services.agent_runtime.app.action.execution_store import (\n    ActionExecutionStore,\n)\nfrom services.agent_runtime.app.action.kubernetes_preflight import (\n    KubernetesPreflightResolver,\n)\nfrom services.agent_runtime.app.action.kubernetes_preflight_factory import (\n    create_kubernetes_preflight_resolver,\n)\nfrom services.agent_runtime.app.action.kubernetes_production_executor import (\n    KubernetesProductionExecutor,\n)\nfrom services.agent_runtime.app.action.kubernetes_production_factory import (\n    create_kubernetes_production_executor,\n)\nfrom services.agent_runtime.app.action.preflight_artifact_service import (\n    PreflightArtifactService,\n)\nfrom services.agent_runtime.app.action.preflight_artifact_store import (\n    PreflightArtifactStore,\n)\nfrom services.agent_runtime.app.action.production_action_preparation import (\n    ProductionActionPreparationService,\n)\nfrom services.agent_runtime.app.action.production_action_query import (\n    ProductionActionQueryService,\n)\nfrom services.agent_runtime.app.action.production_action_guard import (\n    ProductionActionExpiryGuard,\n)\nfrom services.agent_runtime.app.action.production_pilot import (\n    KubernetesProductionPilotControl,\n    ProductionPilotReadinessService,\n)\nfrom services.agent_runtime.app.action.production_pilot_factory import (\n    create_kubernetes_production_pilot_control,\n)\nfrom services.agent_runtime.app.action.production_pilot_budget_service import (\n    ProductionPilotBudgetService,\n)\nfrom services.agent_runtime.app.action.production_pilot_budget_store import (\n    ProductionPilotBudgetStore,\n)\nfrom services.agent_runtime.app.action.production_pilot_rehearsal import (\n    ProductionPilotRehearsalService,\n)\nfrom services.agent_runtime.app.action.production_pilot_crash_rehearsal import (\n    ProductionPilotCrashRecoveryRehearsalService,\n)\nfrom services.agent_runtime.app.action.production_pilot_pre_enable_evidence import (\n    ProductionPilotPreEnableEvidenceService,\n)\nfrom services.agent_runtime.app.action.production_pilot_final_handoff import (\n    ProductionPilotFinalHandoffRehearsalService,\n)\nfrom services.agent_runtime.app.action.production_pilot_live_probe import (\n    ProductionPilotLiveReadinessProbe,\n    create_production_pilot_live_readiness_probe,\n)\nfrom services.agent_runtime.app.action.production_pilot_go_no_go_service import (\n    ProductionPilotGoNoGoService,\n)\nfrom services.agent_runtime.app.action.production_pilot_go_no_go_store import (\n    ProductionPilotGoNoGoStore,\n)\nfrom services.agent_runtime.app.action.production_pilot_ceremony_service import (\n    ProductionPilotCeremonyService,\n)\nfrom services.agent_runtime.app.action.production_pilot_ceremony_store import (\n    ProductionPilotCeremonyStore,\n)\nfrom services.agent_runtime.app.verification.collector import (\n    VerificationEvidenceCollector,\n)\nfrom services.agent_runtime.app.verification.coordinator import (\n    VerificationCoordinator,\n)\nfrom services.agent_runtime.app.verification.profiles import (\n    VerificationProfileFactory,\n)\nfrom services.agent_runtime.app.verification.service import (\n    VerificationService,\n)\nfrom services.agent_runtime.app.verification.store import (\n    VerificationStore,\n)\nfrom services.agent_runtime.app.runtime.action_runtime import (\n    ActionRuntime,\n)\nfrom services.agent_runtime.app.runtime.verification_runtime import (\n    VerificationRuntime,\n)\nfrom services.agent_runtime.app.security.factory import (\n    create_authentication_service,\n)\nfrom services.agent_runtime.app.security.policy import (\n    SecurityPolicyEngine,\n)\nfrom services.agent_runtime.app.security.service import (\n    AuthenticationService,\n)\nfrom services.sandbox.executor.local import (\n    LocalSandboxExecutor,\n)\nfrom services.sandbox.policy.validator import (\n    SandboxPolicyValidator,\n)\n\n\nfrom services.agent_runtime.app.incident_evidence.recorder import (\n    ProductionIncidentEvidenceRecorder,\n)\nfrom services.agent_runtime.app.incident_evidence.settings import (\n    IncidentEvidenceRecorderSettings,\n)\n\nclass AgentRuntime:\n    """\n    Runtime container.\n\n    Owns and shares security and runtime infrastructure\n    across Pipeline, Action and Verification.\n\n    security_policy is the RBAC authorization policy. The existing policy\n    attribute remains the remediation business policy engine.\n    """\n\n    def __init__(\n        self,\n        authentication_service: (\n            AuthenticationService | None\n        ) = None,\n        security_policy: (\n            SecurityPolicyEngine | None\n        ) = None,\n        kubernetes_preflight: (\n            KubernetesPreflightResolver | None\n        ) = None,\n        kubernetes_production_executor: (\n            KubernetesProductionExecutor | None\n        ) = None,\n        production_pilot_control: (\n            KubernetesProductionPilotControl | None\n        ) = None,\n        production_pilot_budget_service: (\n            ProductionPilotBudgetService | None\n        ) = None,\n        production_pilot_live_probe: (\n            ProductionPilotLiveReadinessProbe | None\n        ) = None,\n        kubernetes_cluster_registry: (\n            KubernetesClusterRegistry | None\n        ) = None,\n        prometheus_cluster_registry: (\n            PrometheusClusterRegistry | None\n        ) = None,\n        llm_gateway: (\n            LLMGateway | None\n        ) = None,\n        investigation_reasoner: (\n            BaseInvestigationReasoner | None\n        ) = None,\n        investigation_settings: (\n            InvestigationSettings | None\n        ) = None,\n    ) -> None:\n        # Validate every injected security component before factories, stores\n        # or other runtime components can produce side effects.\n        if (\n            authentication_service is not None\n            and not isinstance(\n                authentication_service,\n                AuthenticationService,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime authentication service is invalid"\n            )\n\n        if (\n            security_policy is not None\n            and not isinstance(\n                security_policy,\n                SecurityPolicyEngine,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime security policy is invalid"\n            )\n\n        if (\n            kubernetes_preflight is not None\n            and not isinstance(\n                kubernetes_preflight,\n                KubernetesPreflightResolver,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Kubernetes preflight resolver is invalid"\n            )\n\n        if (\n            kubernetes_production_executor is not None\n            and not isinstance(\n                kubernetes_production_executor,\n                KubernetesProductionExecutor,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Kubernetes production executor is invalid"\n            )\n\n        if (\n            production_pilot_control is not None\n            and not isinstance(\n                production_pilot_control,\n                KubernetesProductionPilotControl,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Kubernetes production pilot control is invalid"\n            )\n\n        if (\n            production_pilot_budget_service is not None\n            and not isinstance(\n                production_pilot_budget_service,\n                ProductionPilotBudgetService,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Kubernetes production pilot budget service is invalid"\n            )\n\n        if (\n            production_pilot_live_probe is not None\n            and not isinstance(\n                production_pilot_live_probe,\n                ProductionPilotLiveReadinessProbe,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Production Pilot live probe is invalid"\n            )\n\n        if (\n            kubernetes_cluster_registry is not None\n            and not isinstance(\n                kubernetes_cluster_registry,\n                KubernetesClusterRegistry,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Kubernetes cluster registry is invalid"\n            )\n\n        if (\n            prometheus_cluster_registry is not None\n            and not isinstance(\n                prometheus_cluster_registry,\n                PrometheusClusterRegistry,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Prometheus cluster registry is invalid"\n            )\n\n        if (\n            llm_gateway is not None\n            and not isinstance(\n                llm_gateway,\n                LLMGateway,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime shared LLM gateway is invalid"\n            )\n\n        if (\n            investigation_reasoner is not None\n            and not isinstance(\n                investigation_reasoner,\n                BaseInvestigationReasoner,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Investigation reasoner is invalid"\n            )\n\n        if (\n            investigation_settings is not None\n            and not isinstance(\n                investigation_settings,\n                InvestigationSettings,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Investigation settings are invalid"\n            )\n\n        # Resolve disabled-default Investigation configuration before any\n        # Runtime store, tool, credential, network or LLM component is created.\n        self.investigation_settings = (\n            investigation_settings\n            if investigation_settings is not None\n            else InvestigationSettings.from_environment()\n        )\n\n        investigation_shared_gateway = None\n\n        # An enabled LLM-backed Investigation must use the exact shared\n        # LLMGateway instance that AgentRuntime will provide to its Agents.\n        #\n        # Disabled Investigation deliberately does not inspect or touch the\n        # supplied reasoner\'s LLM adapter.\n        if (\n            self.investigation_settings.enabled\n            and isinstance(\n                investigation_reasoner,\n                LLMInvestigationReasoner,\n            )\n        ):\n            investigation_llm = (\n                investigation_reasoner.investigation_llm\n            )\n\n            if not isinstance(\n                investigation_llm,\n                InvestigationLLMGatewayAdapter,\n            ):\n                raise TypeError(\n                    "AgentRuntime LLM Investigation requires "\n                    "InvestigationLLMGatewayAdapter"\n                )\n\n            investigation_shared_gateway = (\n                investigation_llm.llm_gateway\n            )\n\n            if not isinstance(\n                investigation_shared_gateway,\n                LLMGateway,\n            ):\n                raise TypeError(\n                    "AgentRuntime Investigation shared LLM gateway is invalid"\n                )\n\n            if (\n                llm_gateway is not None\n                and investigation_shared_gateway\n                is not llm_gateway\n            ):\n                raise TypeError(\n                    "AgentRuntime Investigation LLM gateway must be shared"\n                )\n\n        # Preserve the existing fail-closed Investigation assembly boundary.\n        # Enabled mode without an explicit reasoner still fails here before\n        # any Runtime or LLM infrastructure is constructed.\n        self.investigation_coordinator = (\n            create_investigation_coordinator(\n                reasoner=investigation_reasoner,\n                settings=self.investigation_settings,\n            )\n        )\n\n        # Do not construct a default Gateway yet. Keeping this unresolved\n        # preserves the previous initialization order. If Investigation\n        # already carries the approved Gateway Adapter, Runtime adopts that\n        # exact Gateway object as its shared instance.\n        self.llm_gateway = (\n            llm_gateway\n            if llm_gateway is not None\n            else investigation_shared_gateway\n        )\n\n        self.authentication = (\n            authentication_service\n            if authentication_service is not None\n            else create_authentication_service()\n        )\n\n        self.security_policy = (\n            security_policy\n            if security_policy is not None\n            else SecurityPolicyEngine()\n        )\n\n        self.kubernetes_preflight = (\n            kubernetes_preflight\n            if kubernetes_preflight is not None\n            else create_kubernetes_preflight_resolver()\n        )\n\n        self.production_pilot_control = (\n            production_pilot_control\n            if production_pilot_control is not None\n            else create_kubernetes_production_pilot_control()\n        )\n\n        # This independent gate may read both credential values at startup,\n        # but can construct only a two-GET probe. Disabled mode returns before\n        # any credential or CA access.\n        self.production_pilot_live_probe = (\n            production_pilot_live_probe\n            if production_pilot_live_probe is not None\n            else create_production_pilot_live_readiness_probe()\n        )\n\n        self.production_pilot_budget_store = None\n        self.production_pilot_budget_service = (\n            production_pilot_budget_service\n        )\n        if (\n            self.production_pilot_budget_service is None\n            and self.production_pilot_control.config.enabled\n        ):\n            self.production_pilot_budget_store = (\n                ProductionPilotBudgetStore()\n            )\n            self.production_pilot_budget_service = (\n                ProductionPilotBudgetService(\n                    store=(\n                        self.production_pilot_budget_store\n                    )\n                )\n            )\n\n        self.kubernetes_production_executor = (\n            kubernetes_production_executor\n            if kubernetes_production_executor is not None\n            else create_kubernetes_production_executor(\n                pilot_control=(\n                    self.production_pilot_control\n                ),\n                pilot_budget_service=(\n                    self.production_pilot_budget_service\n                ),\n            )\n        )\n\n        if self.kubernetes_production_executor is not None:\n            executor_control = getattr(\n                self.kubernetes_production_executor,\n                "pilot_control",\n                None,\n            )\n            if executor_control is None:\n                self.kubernetes_production_executor.pilot_control = (\n                    self.production_pilot_control\n                )\n            elif executor_control is not self.production_pilot_control:\n                raise TypeError(\n                    "AgentRuntime Kubernetes production pilot control must be shared"\n                )\n            executor_budget = getattr(\n                self.kubernetes_production_executor,\n                "pilot_budget_service",\n                None,\n            )\n            if executor_budget is None:\n                if self.production_pilot_budget_service is None:\n                    raise TypeError(\n                        "AgentRuntime Kubernetes production pilot budget is unavailable"\n                    )\n                self.kubernetes_production_executor.pilot_budget_service = (\n                    self.production_pilot_budget_service\n                )\n            elif executor_budget is not self.production_pilot_budget_service:\n                raise TypeError(\n                    "AgentRuntime Kubernetes production pilot budget must be shared"\n                )\n\n        if (\n            self.kubernetes_production_executor is not None\n            and self.kubernetes_preflight is None\n        ):\n            raise TypeError(\n                "AgentRuntime Kubernetes production executor requires "\n                "trusted preflight"\n            )\n\n        self.production_pilot_readiness = (\n            ProductionPilotReadinessService(\n                control=(\n                    self.production_pilot_control\n                ),\n                production_executor_configured=(\n                    self.kubernetes_production_executor\n                    is not None\n                ),\n            )\n        )\n        self.production_pilot_rehearsal = (\n            ProductionPilotRehearsalService(\n                control=(\n                    self.production_pilot_control\n                ),\n                budget_service=(\n                    self.production_pilot_budget_service\n                ),\n                production_executor_configured=(\n                    self.kubernetes_production_executor\n                    is not None\n                ),\n            )\n        )\n        # Pure recovery-policy proof. It owns no store, credential, network\n        # client or executor and is available while the production gate is\n        # disabled so operators can rehearse recovery before enablement.\n        self.production_pilot_crash_recovery_rehearsal = (\n            ProductionPilotCrashRecoveryRehearsalService()\n        )\n\n        self.memory = MemoryStore()\n\n        if (\n            kubernetes_cluster_registry\n            is None\n        ):\n            self.kubernetes_cluster_registry = (\n                create_kubernetes_cluster_registry()\n            )\n        else:\n            self.kubernetes_cluster_registry = (\n                kubernetes_cluster_registry\n            )\n\n        if (\n            prometheus_cluster_registry\n            is None\n        ):\n            self.prometheus_cluster_registry = (\n                create_prometheus_cluster_registry()\n            )\n        else:\n            self.prometheus_cluster_registry = (\n                prometheus_cluster_registry\n            )\n\n        self.cluster_verified_evidence_required = (\n            self.kubernetes_cluster_registry\n            is not None\n            or self.prometheus_cluster_registry\n            is not None\n        )\n\n        if (\n            self.investigation_coordinator\n            is not None\n        ):\n            self.investigation_coordinator.require_cluster_verified_evidence = (\n                self.cluster_verified_evidence_required\n            )\n\n        tool_manager_kwargs = {}\n\n        if (\n            self.kubernetes_cluster_registry\n            is not None\n        ):\n            tool_manager_kwargs[\n                "kubernetes_cluster_registry"\n            ] = self.kubernetes_cluster_registry\n\n        if (\n            self.prometheus_cluster_registry\n            is not None\n        ):\n            tool_manager_kwargs[\n                "prometheus_cluster_registry"\n            ] = self.prometheus_cluster_registry\n\n        if tool_manager_kwargs:\n            self.tools = create_tool_manager(\n                **tool_manager_kwargs\n            )\n        else:\n            self.tools = create_tool_manager()\n\n        readiness_registry_types_valid = (\n            (\n                self.kubernetes_cluster_registry\n                is None\n                or isinstance(\n                    self.kubernetes_cluster_registry,\n                    KubernetesClusterRegistry,\n                )\n            )\n            and (\n                self.prometheus_cluster_registry\n                is None\n                or isinstance(\n                    self.prometheus_cluster_registry,\n                    PrometheusClusterRegistry,\n                )\n            )\n        )\n\n        self.production_multi_cluster_readiness = None\n        self.production_multi_cluster_coverage = None\n\n        if readiness_registry_types_valid:\n            self.production_multi_cluster_readiness = (\n                ProductionMultiClusterReadinessGate(\n                    kubernetes_cluster_registry=(\n                        self.kubernetes_cluster_registry\n                    ),\n                    prometheus_cluster_registry=(\n                        self.prometheus_cluster_registry\n                    ),\n                    tools=self.tools,\n                    strict_evidence_required=(\n                        self.cluster_verified_evidence_required\n                    ),\n                )\n            )\n\n            self.production_multi_cluster_coverage = (\n                self.production_multi_cluster_readiness\n                .evaluate_all()\n            )\n\n        self.skills = create_skill_registry()\n        self.mcp = create_mcp_registry()\n        self.tracer = TraceCollector()\n        self.evaluators = create_evaluation_registry()\n\n        # Remediation business policy. This is intentionally separate from\n        # security_policy, which authorizes operator-facing operations.\n        self.policy = create_policy_engine()\n\n        self.preflight_artifact_store = None\n        self.preflight_artifact_service = None\n        self.production_action_guard = None\n        self.production_action_preparation = None\n        self.production_action_query = None\n\n        if self.kubernetes_preflight is not None:\n            self.preflight_artifact_store = PreflightArtifactStore()\n            self.preflight_artifact_service = PreflightArtifactService(\n                store=self.preflight_artifact_store\n            )\n            self.production_action_guard = (\n                ProductionActionExpiryGuard(\n                    artifact_service=(\n                        self.preflight_artifact_service\n                    )\n                )\n            )\n\n        self.approval = ApprovalService()\n\n        if self.production_action_guard is not None:\n            self.approval.manager.set_transition_guard(\n                self.production_action_guard\n            )\n\n        if self.preflight_artifact_service is not None:\n            self.production_action_preparation = (\n                ProductionActionPreparationService(\n                    resolver=self.kubernetes_preflight,\n                    artifact_service=self.preflight_artifact_service,\n                    approval_service=self.approval,\n                )\n            )\n\n        self.production_pilot_ceremony_store = None\n        self.production_pilot_ceremony = None\n        if (\n            self.production_pilot_control.config.enabled\n            and self.production_pilot_budget_service is not None\n            and self.preflight_artifact_service is not None\n        ):\n            self.production_pilot_ceremony_store = (\n                ProductionPilotCeremonyStore()\n            )\n            self.production_pilot_ceremony = (\n                ProductionPilotCeremonyService(\n                    store=(\n                        self.production_pilot_ceremony_store\n                    ),\n                    control=(\n                        self.production_pilot_control\n                    ),\n                    rehearsal=(\n                        self.production_pilot_rehearsal\n                    ),\n                    budget_service=(\n                        self.production_pilot_budget_service\n                    ),\n                    approval_service=self.approval,\n                    artifact_service=(\n                        self.preflight_artifact_service\n                    ),\n                )\n            )\n\n        self.incident_store = IncidentStore()\n\n        if self.preflight_artifact_service is not None:\n            self.production_action_query = (\n                ProductionActionQueryService(\n                    artifact_service=(\n                        self.preflight_artifact_service\n                    ),\n                    approval_service=self.approval,\n                    incident_store=self.incident_store,\n                )\n            )\n\n        self.incident_service = IncidentService(\n            store=self.incident_store\n        )\n\n        self.workflow_service = WorkflowService(\n            incident_service=self.incident_service\n        )\n\n        self.action_execution_store = ActionExecutionStore()\n\n        self.action_execution_service = ActionExecutionService(\n            store=self.action_execution_store\n        )\n\n        self.action_runtime = ActionRuntime(\n            approval_service=self.approval,\n            incident_store=self.incident_store,\n            action_execution_service=self.action_execution_service,\n            production_action_guard=(\n                self.production_action_guard\n            ),\n            kubernetes_production_executor=(\n                self.kubernetes_production_executor\n            ),\n            preflight_artifact_service=(\n                self.preflight_artifact_service\n                if self.kubernetes_production_executor is not None\n                else None\n            ),\n            production_pilot_control=(\n                self.production_pilot_control\n            ),\n            production_pilot_budget_service=(\n                self.production_pilot_budget_service\n            ),\n            production_pilot_ceremony_service=(\n                self.production_pilot_ceremony\n                if self.kubernetes_production_executor is not None\n                else None\n            ),\n        )\n\n        self.verification_store = VerificationStore()\n\n        self.verification = VerificationService(\n            store=self.verification_store\n        )\n\n        self.verification_runtime = VerificationRuntime(\n            verification_service=self.verification,\n            incident_store=self.incident_store,\n        )\n\n        self.verification_profile_factory = VerificationProfileFactory()\n\n        self.verification_collector = VerificationEvidenceCollector(\n            tools=self.tools,\n            require_cluster_verified_evidence=(\n                self.cluster_verified_evidence_required\n            ),\n        )\n\n        self.verification_coordinator = VerificationCoordinator(\n            profile_factory=self.verification_profile_factory,\n            collector=self.verification_collector,\n            verification_runtime=self.verification_runtime,\n        )\n\n        # Final pre-enable evidence is assembled only when every production\n        # preparation component is available. The service is read-only and\n        # deliberately owns no executor or mutable workflow operation.\n        self.production_pilot_pre_enable_evidence = None\n        if all(\n            component is not None\n            for component in (\n                self.production_pilot_ceremony,\n                self.production_pilot_budget_service,\n                self.preflight_artifact_service,\n            )\n        ):\n            self.production_pilot_pre_enable_evidence = (\n                ProductionPilotPreEnableEvidenceService(\n                    readiness_service=(\n                        self.production_pilot_readiness\n                    ),\n                    rehearsal_service=(\n                        self.production_pilot_rehearsal\n                    ),\n                    crash_rehearsal_service=(\n                        self.production_pilot_crash_recovery_rehearsal\n                    ),\n                    ceremony_service=(\n                        self.production_pilot_ceremony\n                    ),\n                    budget_service=(\n                        self.production_pilot_budget_service\n                    ),\n                    artifact_service=(\n                        self.preflight_artifact_service\n                    ),\n                    approval_service=self.approval,\n                    incident_store=self.incident_store,\n                    action_execution_service=(\n                        self.action_execution_service\n                    ),\n                    verification_service=self.verification,\n                )\n            )\n\n        # The final handoff rehearsal is also strictly read-only. It is\n        # available only with the full prepared Pilot chain and explicitly\n        # records whether production executors remain absent while the gate\n        # is disabled.\n        self.production_pilot_final_handoff_rehearsal = None\n        if self.production_pilot_pre_enable_evidence is not None:\n            self.production_pilot_final_handoff_rehearsal = (\n                ProductionPilotFinalHandoffRehearsalService(\n                    pilot_control=self.production_pilot_control,\n                    pre_enable_evidence_service=(\n                        self.production_pilot_pre_enable_evidence\n                    ),\n                    preflight_resolver=self.kubernetes_preflight,\n                    production_executor_configured=(\n                        self.kubernetes_production_executor is not None\n                    ),\n                    action_runtime_production_executor_configured=(\n                        getattr(\n                            self.action_runtime,\n                            "kubernetes_production_executor",\n                            None,\n                        )\n                        is not None\n                    ),\n                )\n            )\n\n        # A dedicated database is created only when the separately gated live\n        # probe exists and the full zero-write handoff chain is available.\n        self.production_pilot_go_no_go_store = None\n        self.production_pilot_go_no_go = None\n        if (\n            self.production_pilot_live_probe is not None\n            and self.production_pilot_final_handoff_rehearsal is not None\n            and self.preflight_artifact_service is not None\n        ):\n            self.production_pilot_go_no_go_store = (\n                ProductionPilotGoNoGoStore()\n            )\n            self.production_pilot_go_no_go = (\n                ProductionPilotGoNoGoService(\n                    store=self.production_pilot_go_no_go_store,\n                    live_probe=self.production_pilot_live_probe,\n                    final_handoff_service=(\n                        self.production_pilot_final_handoff_rehearsal\n                    ),\n                    artifact_service=self.preflight_artifact_service,\n                    pilot_control=self.production_pilot_control,\n                )\n            )\n\n        self.sandbox = LocalSandboxExecutor()\n\n        self.sandbox_policy = SandboxPolicyValidator()\n\n        if self.llm_gateway is None:\n            self.llm_gateway = create_llm_gateway()\n\n        self.registry = create_agent_registry(\n            llm_gateway=self.llm_gateway,\n        )\n\n        self.planner = AgentPlanner()\n\n        self.pipeline = PlannerPipeline(\n            self.registry,\n            self.planner,\n            self.tracer,\n            self.evaluators,\n            incident_store=self.incident_store,\n            incident_service=self.incident_service,\n            workflow_service=self.workflow_service,\n        )\n\n    async def execute(\n        self,\n        context: AgentContext,\n    ):\n        """\n        Execute the primary PlannerPipeline and, when explicitly enabled,\n        run Investigation automatically as a best-effort Shadow.\n\n        Ordering is deliberate:\n\n        1. PlannerPipeline completes first.\n        2. Investigation receives an isolated AgentContext.\n        3. Only the bounded investigation_shadow snapshot is copied back.\n\n        Investigation can never change the Pipeline result, Incident,\n        variables, results, trace, Approval, executions or evaluations.\n\n        Investigation orchestration failure is sanitized and recorded in\n        metadata without failing an otherwise successful Pipeline execution.\n        """\n\n        if not isinstance(\n            context,\n            AgentContext,\n        ):\n            raise TypeError(\n                "AgentRuntime execution context is invalid"\n            )\n\n        # Reserved Shadow metadata from a previous execution must never be\n        # visible to the primary Pipeline, even when this Runtime currently\n        # has Investigation disabled.\n        for reserved_key in (\n            "investigation_shadow",\n            "investigation_shadow_orchestration",\n            "investigation_rca_comparison",\n            "production_multi_cluster_readiness",\n        ):\n            context.metadata.pop(\n                reserved_key,\n                None,\n            )\n\n        # Primary workflow semantics remain authoritative. Pipeline failure\n        # propagates normally and Investigation is not attempted afterward.\n        context.metadata.pop(\n            "incident_evidence_recorder",\n            None,\n        )\n\n        results = await self.pipeline.execute(\n            context\n        )\n\n        # Evidence Recorder is evaluation-only and best-effort.\n        await self._record_incident_evidence_shadow(\n            context\n        )\n\n        if self.investigation_coordinator is None:\n            return results\n\n        shadow_context = (\n            self._create_investigation_shadow_context(\n                context\n            )\n        )\n\n        try:\n            await self.run_investigation_shadow(\n                shadow_context\n            )\n\n            readiness_snapshot = (\n                shadow_context.metadata.get(\n                    "production_multi_cluster_readiness"\n                )\n            )\n\n            if isinstance(\n                readiness_snapshot,\n                dict,\n            ):\n                context.metadata[\n                    "production_multi_cluster_readiness"\n                ] = deepcopy(\n                    readiness_snapshot\n                )\n\n            snapshot = shadow_context.metadata.get(\n                "investigation_shadow"\n            )\n\n            if (\n                not isinstance(\n                    snapshot,\n                    dict,\n                )\n                or snapshot.get(\n                    "shadow_mode"\n                )\n                is not True\n                or snapshot.get(\n                    "read_only"\n                )\n                is not True\n            ):\n                raise RuntimeError(\n                    "Investigation Shadow snapshot is invalid"\n                )\n\n            context.metadata[\n                "investigation_shadow"\n            ] = deepcopy(\n                snapshot\n            )\n\n        except Exception as exc:\n            # Shadow means Shadow: an Investigation orchestration fault must\n            # never convert a successful PlannerPipeline execution to failed.\n            #\n            # Raw exception text is deliberately excluded because provider,\n            # URL, credential or tool details may be present in it.\n            readiness_snapshot = (\n                shadow_context.metadata.get(\n                    "production_multi_cluster_readiness"\n                )\n            )\n\n            if isinstance(\n                readiness_snapshot,\n                dict,\n            ):\n                context.metadata[\n                    "production_multi_cluster_readiness"\n                ] = deepcopy(\n                    readiness_snapshot\n                )\n\n            context.metadata[\n                "investigation_shadow_orchestration"\n            ] = {\n                "shadow_mode": True,\n                "read_only": True,\n                "automatic": True,\n                "status": "failed",\n                "failure_code": (\n                    type(exc).__name__[:256]\n                ),\n            }\n\n        # Comparison is evaluation-only. It cannot change the authoritative\n        # RCA stored in context.variables["rca"] and has no Healing authority.\n        try:\n            context.metadata[\n                "investigation_rca_comparison"\n            ] = build_rca_investigation_comparison(\n                rca=context.variables.get(\n                    "rca"\n                ),\n                investigation_snapshot=(\n                    context.metadata.get(\n                        "investigation_shadow"\n                    )\n                ),\n                orchestration_snapshot=(\n                    context.metadata.get(\n                        "investigation_shadow_orchestration"\n                    )\n                ),\n            )\n        except Exception as exc:\n            # A comparison bug must remain weaker than Shadow itself and must\n            # never fail a successful primary Pipeline.\n            context.metadata[\n                "investigation_rca_comparison"\n            ] = {\n                "schema_version": "v1",\n                "shadow_mode": True,\n                "read_only": True,\n                "decision_influence": False,\n                "available": False,\n                "comparison_status": (\n                    "comparison_failed"\n                ),\n                "failure_code": (\n                    type(exc).__name__[:256]\n                ),\n            }\n\n        return results\n\n    def _create_investigation_shadow_context(\n        self,\n        context: AgentContext,\n    ) -> AgentContext:\n        """\n        Build the minimum-privilege context for automatic Investigation.\n\n        Copied:\n        - event input\n        - request correlation ID\n\n        Shared:\n        - exact Runtime-owned ToolManager\n\n        Deliberately not shared:\n        - Incident\n        - variables\n        - results\n        - metadata\n        - trace\n        - memory\n        - skills\n        - MCP\n        - sandbox\n        - Approval\n        - executions\n        - evaluations\n        """\n\n        return AgentContext(\n            request_id=context.request_id,\n            event=deepcopy(\n                context.event\n            ),\n            tools=self.tools,\n            metadata={},\n        )\n\n    async def run_investigation_shadow(\n        self,\n        context: AgentContext,\n    ) -> InvestigationState:\n        """\n        Explicitly execute the enabled read-only Investigation Shadow.\n\n        This method is intentionally separate from PlannerPipeline.\n\n        PlannerPipeline itself never invokes Investigation. AgentRuntime\n        may call this lower-level entry point after a successful Pipeline\n        execution when automatic Shadow Investigation is enabled.\n\n        The supplied AgentContext must use the exact Runtime ToolManager so\n        Investigation probes cannot bypass Runtime-owned tool boundaries.\n        """\n\n        if not isinstance(\n            context,\n            AgentContext,\n        ):\n            raise TypeError(\n                "AgentRuntime Investigation Shadow context is invalid"\n            )\n\n        if self.investigation_coordinator is None:\n            raise RuntimeError(\n                "AgentRuntime Investigation Shadow is disabled"\n            )\n\n        if context.tools is not self.tools:\n            raise TypeError(\n                "AgentRuntime Investigation Shadow requires shared Runtime tools"\n            )\n\n        if getattr(\n            self,\n            "cluster_verified_evidence_required",\n            False,\n        ):\n            if (\n                self.production_multi_cluster_readiness\n                is None\n            ):\n                raise ProductionMultiClusterReadinessError(\n                    "AgentRuntime Production Shadow readiness proof is unavailable"\n                )\n\n            readiness = (\n                self.production_multi_cluster_readiness\n                .evaluate_event(\n                    context.event\n                )\n            )\n\n            context.metadata[\n                "production_multi_cluster_readiness"\n            ] = readiness.snapshot()\n\n            if not readiness.ready:\n                raise ProductionMultiClusterReadinessError(\n                    "AgentRuntime Production Shadow read coverage is not ready"\n                )\n\n        return await (\n            self.investigation_coordinator.investigate(\n                context\n            )\n        )\n\n    async def _record_incident_evidence_shadow(\n        self,\n        context: AgentContext,\n    ) -> None:\n        """\n        Best-effort, decision-isolated production evidence preservation.\n\n        Runs only after the authoritative PlannerPipeline succeeds.\n        Disabled mode constructs no Recorder and issues no production Probe.\n        """\n\n        try:\n            settings = (\n                IncidentEvidenceRecorderSettings\n                .from_environment()\n            )\n        except Exception as exc:\n            context.metadata[\n                "incident_evidence_recorder"\n            ] = {\n                "schema_version": "v1",\n                "shadow_mode": True,\n                "read_only": True,\n                "decision_influence": False,\n                "automatic": True,\n                "status": "failed",\n                "failure_code": (\n                    type(exc).__name__[:256]\n                ),\n            }\n            return\n\n        if not settings.enabled:\n            return\n\n        recorder_context = AgentContext(\n            request_id=context.request_id,\n            event=deepcopy(\n                context.event\n            ),\n            tools=self.tools,\n            metadata={},\n        )\n\n        try:\n            recorder = ProductionIncidentEvidenceRecorder(\n                settings.resolve_output_dir()\n            )\n\n            result = await recorder.record(\n                recorder_context\n            )\n\n            context.metadata[\n                "incident_evidence_recorder"\n            ] = {\n                "schema_version": "v1",\n                "shadow_mode": True,\n                "read_only": True,\n                "decision_influence": False,\n                "automatic": True,\n                "status": "captured",\n                "created": result.created,\n                "incident_id": result.incident_id,\n                "observation_count": (\n                    result.observation_count\n                ),\n                "capture_file": result.path.name,\n            }\n\n        except Exception as exc:\n            context.metadata[\n                "incident_evidence_recorder"\n            ] = {\n                "schema_version": "v1",\n                "shadow_mode": True,\n                "read_only": True,\n                "decision_influence": False,\n                "automatic": True,\n                "status": "failed",\n                "failure_code": (\n                    type(exc).__name__[:256]\n                ),\n            }\n'
TEST_SOURCE = 'from __future__ import annotations\n\nfrom types import SimpleNamespace\n\nimport pytest\n\nfrom services.agent_runtime.app.investigation.models import (\n    default_investigation_probes,\n)\nfrom services.agent_runtime.app.investigation.multi_cluster_readiness import (\n    ProductionMultiClusterReadinessError,\n    ProductionMultiClusterReadinessGate,\n)\nfrom services.agent_runtime.app.tools.factory import (\n    create_tool_manager,\n)\nfrom services.agent_runtime.app.tools.kubernetes.router import (\n    KubernetesClusterRegistry,\n)\nfrom services.agent_runtime.app.tools.kubernetes.tool import (\n    KubernetesTool,\n)\nfrom services.agent_runtime.app.tools.prometheus.router import (\n    PrometheusClusterRegistry,\n)\nfrom services.agent_runtime.app.tools.prometheus.tool import (\n    PrometheusTool,\n)\n\n\nCLUSTER = "prod-us-03"\nSECOND_CLUSTER = "prod-sg-17"\n\n\ndef kubernetes_tool(\n    cluster=CLUSTER,\n    *,\n    fallback=False,\n):\n    return KubernetesTool(\n        api_url=(\n            f"https://{cluster}.kubernetes.test"\n        ),\n        cluster_name=cluster,\n        bearer_token=(\n            f"{cluster}-read-token-1234567890"\n        ),\n        verify_tls=True,\n        allow_dry_run_fallback=fallback,\n    )\n\n\ndef prometheus_tool(\n    endpoint="central",\n):\n    return PrometheusTool(\n        base_url=(\n            f"https://{endpoint}.prometheus.test"\n        ),\n        bearer_token="",\n        verify_tls=True,\n        allow_mock_fallback=False,\n    )\n\n\ndef complete_gate(\n    *,\n    clusters=(CLUSTER,),\n):\n    kubernetes_registry = (\n        KubernetesClusterRegistry(\n            [\n                kubernetes_tool(\n                    cluster\n                )\n                for cluster in clusters\n            ]\n        )\n    )\n\n    central = prometheus_tool()\n\n    prometheus_registry = (\n        PrometheusClusterRegistry(\n            {\n                cluster: central\n                for cluster in clusters\n            }\n        )\n    )\n\n    manager = create_tool_manager(\n        kubernetes_cluster_registry=(\n            kubernetes_registry\n        ),\n        prometheus_cluster_registry=(\n            prometheus_registry\n        ),\n    )\n\n    return (\n        ProductionMultiClusterReadinessGate(\n            kubernetes_cluster_registry=(\n                kubernetes_registry\n            ),\n            prometheus_cluster_registry=(\n                prometheus_registry\n            ),\n            tools=manager,\n            strict_evidence_required=True,\n        ),\n        kubernetes_registry,\n        prometheus_registry,\n    )\n\n\ndef event(\n    *clusters,\n):\n    return SimpleNamespace(\n        resources=[\n            SimpleNamespace(\n                cluster=cluster\n            )\n            for cluster in clusters\n        ]\n    )\n\n\ndef test_complete_cluster_coverage_is_ready_without_network():\n    gate, _, _ = complete_gate()\n\n    report = gate.evaluate(\n        CLUSTER\n    )\n\n    assert report.ready is True\n\n    assert (\n        report.kubernetes_route_ready\n        is True\n    )\n\n    assert (\n        report.kubernetes_change_route_ready\n        is True\n    )\n\n    assert (\n        report.prometheus_route_ready\n        is True\n    )\n\n    assert set(\n        report.covered_investigation_probes\n    ) == {\n        probe.value\n        for probe\n        in default_investigation_probes()\n    }\n\n    assert (\n        report.covered_verification_tools\n        == (\n            "kubernetes",\n            "prometheus",\n        )\n    )\n\n    assert report.issues == ()\n\n\ndef test_complete_central_prometheus_binding_can_cover_multiple_clusters():\n    gate, _, prometheus_registry = (\n        complete_gate(\n            clusters=(\n                CLUSTER,\n                SECOND_CLUSTER,\n            )\n        )\n    )\n\n    first = prometheus_registry.resolve(\n        CLUSTER\n    )[\n        1\n    ]\n\n    second = prometheus_registry.resolve(\n        SECOND_CLUSTER\n    )[\n        1\n    ]\n\n    assert first is second\n\n    coverage = gate.evaluate_all()\n\n    assert coverage.ready is True\n\n    assert {\n        item.cluster\n        for item in coverage.clusters\n    } == {\n        CLUSTER,\n        SECOND_CLUSTER,\n    }\n\n\ndef test_partial_registry_coverage_is_not_ready():\n    kubernetes_registry = (\n        KubernetesClusterRegistry(\n            [\n                kubernetes_tool()\n            ]\n        )\n    )\n\n    manager = create_tool_manager(\n        kubernetes_cluster_registry=(\n            kubernetes_registry\n        )\n    )\n\n    gate = ProductionMultiClusterReadinessGate(\n        kubernetes_cluster_registry=(\n            kubernetes_registry\n        ),\n        prometheus_cluster_registry=None,\n        tools=manager,\n        strict_evidence_required=True,\n    )\n\n    report = gate.evaluate(\n        CLUSTER\n    )\n\n    assert report.ready is False\n\n    assert (\n        "prometheus_registry_missing"\n        in report.issues\n    )\n\n    assert (\n        "required_investigation_probe_coverage_incomplete"\n        in report.issues\n    )\n\n    assert (\n        "required_verification_tool_coverage_incomplete"\n        in report.issues\n    )\n\n\ndef test_registry_cluster_sets_must_cover_the_same_incident_clusters():\n    kubernetes_registry = (\n        KubernetesClusterRegistry(\n            [\n                kubernetes_tool(\n                    CLUSTER\n                ),\n                kubernetes_tool(\n                    SECOND_CLUSTER\n                ),\n            ]\n        )\n    )\n\n    prometheus_registry = (\n        PrometheusClusterRegistry(\n            {\n                CLUSTER: prometheus_tool(),\n            }\n        )\n    )\n\n    manager = create_tool_manager(\n        kubernetes_cluster_registry=(\n            kubernetes_registry\n        ),\n        prometheus_cluster_registry=(\n            prometheus_registry\n        ),\n    )\n\n    gate = ProductionMultiClusterReadinessGate(\n        kubernetes_cluster_registry=(\n            kubernetes_registry\n        ),\n        prometheus_cluster_registry=(\n            prometheus_registry\n        ),\n        tools=manager,\n        strict_evidence_required=True,\n    )\n\n    coverage = gate.evaluate_all()\n\n    assert coverage.ready is False\n\n    assert (\n        "cluster_registry_coverage_sets_differ"\n        in coverage.issues\n    )\n\n    second = [\n        item\n        for item in coverage.clusters\n        if item.cluster\n        == SECOND_CLUSTER\n    ][\n        0\n    ]\n\n    assert second.ready is False\n\n    assert (\n        "prometheus_cluster_binding_missing"\n        in second.issues\n    )\n\n\ndef test_runtime_tool_manager_must_use_the_exact_registry_routers():\n    gate, kubernetes_registry, prometheus_registry = (\n        complete_gate()\n    )\n\n    legacy_manager = create_tool_manager()\n\n    broken = ProductionMultiClusterReadinessGate(\n        kubernetes_cluster_registry=(\n            kubernetes_registry\n        ),\n        prometheus_cluster_registry=(\n            prometheus_registry\n        ),\n        tools=legacy_manager,\n        strict_evidence_required=True,\n    )\n\n    report = broken.evaluate(\n        CLUSTER\n    )\n\n    assert report.ready is False\n\n    assert (\n        "kubernetes_runtime_router_mismatch"\n        in report.issues\n    )\n\n    assert (\n        "kubernetes_change_runtime_router_mismatch"\n        in report.issues\n    )\n\n    assert (\n        "prometheus_runtime_router_mismatch"\n        in report.issues\n    )\n\n\ndef test_kubernetes_dry_run_fallback_blocks_production_readiness():\n    kubernetes_registry = (\n        KubernetesClusterRegistry(\n            [\n                kubernetes_tool(\n                    fallback=True\n                )\n            ]\n        )\n    )\n\n    prometheus_registry = (\n        PrometheusClusterRegistry(\n            {\n                CLUSTER: prometheus_tool(),\n            }\n        )\n    )\n\n    manager = create_tool_manager(\n        kubernetes_cluster_registry=(\n            kubernetes_registry\n        ),\n        prometheus_cluster_registry=(\n            prometheus_registry\n        ),\n    )\n\n    gate = ProductionMultiClusterReadinessGate(\n        kubernetes_cluster_registry=(\n            kubernetes_registry\n        ),\n        prometheus_cluster_registry=(\n            prometheus_registry\n        ),\n        tools=manager,\n        strict_evidence_required=True,\n    )\n\n    report = gate.evaluate(\n        CLUSTER\n    )\n\n    assert report.ready is False\n\n    assert (\n        "kubernetes_dry_run_fallback_must_be_disabled"\n        in report.issues\n    )\n\n\ndef test_strict_evidence_policy_is_required_for_ready_state():\n    gate, kubernetes_registry, prometheus_registry = (\n        complete_gate()\n    )\n\n    not_strict = ProductionMultiClusterReadinessGate(\n        kubernetes_cluster_registry=(\n            kubernetes_registry\n        ),\n        prometheus_cluster_registry=(\n            prometheus_registry\n        ),\n        tools=gate.tools,\n        strict_evidence_required=False,\n    )\n\n    report = not_strict.evaluate(\n        CLUSTER\n    )\n\n    assert report.ready is False\n\n    assert (\n        "cluster_verified_evidence_policy_inactive"\n        in report.issues\n    )\n\n\n@pytest.mark.parametrize(\n    "clusters",\n    [\n        (),\n        (\n            CLUSTER,\n            SECOND_CLUSTER,\n        ),\n    ],\n)\ndef test_event_scope_requires_one_exact_cluster(\n    clusters,\n):\n    gate, _, _ = complete_gate(\n        clusters=(\n            CLUSTER,\n            SECOND_CLUSTER,\n        )\n    )\n\n    report = gate.evaluate_event(\n        event(\n            *clusters\n        )\n    )\n\n    assert report.ready is False\n\n    if not clusters:\n        assert any(\n            item\n            in report.issues\n            for item in (\n                "incident_resource_missing",\n                "incident_cluster_missing",\n            )\n        )\n    else:\n        assert (\n            "incident_cluster_ambiguous"\n            in report.issues\n        )\n\n\ndef test_assert_event_ready_raises_sanitized_error_only():\n    kubernetes_registry = (\n        KubernetesClusterRegistry(\n            [\n                kubernetes_tool()\n            ]\n        )\n    )\n\n    manager = create_tool_manager(\n        kubernetes_cluster_registry=(\n            kubernetes_registry\n        )\n    )\n\n    gate = ProductionMultiClusterReadinessGate(\n        kubernetes_cluster_registry=(\n            kubernetes_registry\n        ),\n        prometheus_cluster_registry=None,\n        tools=manager,\n        strict_evidence_required=True,\n    )\n\n    with pytest.raises(\n        ProductionMultiClusterReadinessError,\n        match="read coverage is not ready",\n    ) as captured:\n        gate.assert_event_ready(\n            event(\n                CLUSTER\n            )\n        )\n\n    text = str(\n        captured.value\n    )\n\n    assert "https://" not in text\n    assert "token" not in text.lower()\n\n\ndef test_readiness_snapshot_contains_no_endpoint_or_credential_values():\n    gate, _, _ = complete_gate()\n\n    snapshot = gate.evaluate(\n        CLUSTER\n    ).snapshot()\n\n    text = str(\n        snapshot\n    )\n\n    assert (\n        "kubernetes.test"\n        not in text\n    )\n\n    assert (\n        "prometheus.test"\n        not in text\n    )\n\n    assert (\n        "read-token"\n        not in text\n    )\n\n    assert snapshot[\n        "read_only"\n    ] is True\n\n    assert snapshot[\n        "decision_influence"\n    ] is False\n'
RUNTIME_TEST_SOURCE = 'from __future__ import annotations\n\nfrom types import SimpleNamespace\n\nimport pytest\n\nimport services.agent_runtime.app.runtime.runtime as runtime_module\n\nfrom common.config.settings import (\n    AuthenticationConfig,\n)\n\nfrom services.agent_runtime.app.investigation.multi_cluster_readiness import (\n    ProductionMultiClusterReadinessError,\n)\nfrom services.agent_runtime.app.investigation.settings import (\n    InvestigationSettings,\n)\nfrom services.agent_runtime.app.model.context import (\n    AgentContext,\n)\nfrom services.agent_runtime.app.security.factory import (\n    create_authentication_service,\n)\nfrom services.agent_runtime.app.tools.manager import (\n    ToolManager,\n)\nfrom services.agent_runtime.app.tools.registry import (\n    ToolRegistry,\n)\n\n\nclass NeverCalledCoordinator:\n    def __init__(\n        self,\n    ) -> None:\n        self.calls = 0\n        self.require_cluster_verified_evidence = (\n            False\n        )\n\n    async def investigate(\n        self,\n        context,\n    ):\n        self.calls += 1\n        raise AssertionError(\n            "Coordinator must not run when readiness fails"\n        )\n\n\nclass ReadyCoordinator:\n    def __init__(\n        self,\n    ) -> None:\n        self.calls = 0\n        self.require_cluster_verified_evidence = (\n            False\n        )\n\n    async def investigate(\n        self,\n        context,\n    ):\n        self.calls += 1\n        return SimpleNamespace(\n            status="ok"\n        )\n\n\nclass FakeReadiness:\n    def __init__(\n        self,\n        *,\n        ready,\n    ) -> None:\n        self.ready = ready\n        self.calls = 0\n\n    def evaluate_event(\n        self,\n        event,\n    ):\n        self.calls += 1\n\n        return SimpleNamespace(\n            ready=self.ready,\n            snapshot=lambda: {\n                "schema_version": "v1",\n                "read_only": True,\n                "decision_influence": False,\n                "applicable": True,\n                "ready": self.ready,\n                "cluster": "prod-us-03",\n                "issues": (\n                    []\n                    if self.ready\n                    else [\n                        "prometheus_registry_missing"\n                    ]\n                ),\n            },\n        )\n\n\ndef _base_runtime(\n    monkeypatch,\n    tmp_path,\n    coordinator,\n):\n    monkeypatch.chdir(\n        tmp_path\n    )\n\n    monkeypatch.setattr(\n        runtime_module,\n        "create_investigation_coordinator",\n        lambda **_: coordinator,\n    )\n\n    monkeypatch.setattr(\n        runtime_module,\n        "create_kubernetes_cluster_registry",\n        lambda: None,\n    )\n\n    monkeypatch.setattr(\n        runtime_module,\n        "create_prometheus_cluster_registry",\n        lambda: None,\n    )\n\n    monkeypatch.setattr(\n        runtime_module,\n        "create_tool_manager",\n        lambda **_: ToolManager(\n            ToolRegistry()\n        ),\n    )\n\n    monkeypatch.setattr(\n        runtime_module,\n        "create_kubernetes_preflight_resolver",\n        lambda: None,\n    )\n\n    monkeypatch.setattr(\n        runtime_module,\n        "create_kubernetes_production_executor",\n        lambda **_: None,\n    )\n\n    monkeypatch.setattr(\n        runtime_module,\n        "create_production_pilot_live_readiness_probe",\n        lambda: None,\n    )\n\n    return runtime_module.AgentRuntime(\n        authentication_service=(\n            create_authentication_service(\n                AuthenticationConfig()\n            )\n        ),\n        investigation_settings=(\n            InvestigationSettings(\n                enabled=False\n            )\n        ),\n    )\n\n\ndef shadow_context(\n    runtime,\n):\n    return AgentContext.model_construct(\n        event=SimpleNamespace(\n            resources=[\n                SimpleNamespace(\n                    cluster="prod-us-03"\n                )\n            ]\n        ),\n        tools=runtime.tools,\n        metadata={},\n    )\n\n\n@pytest.mark.asyncio\nasync def test_runtime_strict_shadow_fails_before_coordinator_when_readiness_not_ready(\n    monkeypatch,\n    tmp_path,\n):\n    coordinator = NeverCalledCoordinator()\n\n    runtime = _base_runtime(\n        monkeypatch,\n        tmp_path,\n        coordinator,\n    )\n\n    runtime.investigation_coordinator = (\n        coordinator\n    )\n\n    runtime.cluster_verified_evidence_required = (\n        True\n    )\n\n    runtime.production_multi_cluster_readiness = (\n        FakeReadiness(\n            ready=False\n        )\n    )\n\n    context = shadow_context(\n        runtime\n    )\n\n    with pytest.raises(\n        ProductionMultiClusterReadinessError,\n    ):\n        await runtime.run_investigation_shadow(\n            context\n        )\n\n    assert coordinator.calls == 0\n\n    assert (\n        context.metadata[\n            "production_multi_cluster_readiness"\n        ][\n            "ready"\n        ]\n        is False\n    )\n\n\n@pytest.mark.asyncio\nasync def test_runtime_strict_shadow_runs_coordinator_after_readiness_passes(\n    monkeypatch,\n    tmp_path,\n):\n    coordinator = ReadyCoordinator()\n\n    runtime = _base_runtime(\n        monkeypatch,\n        tmp_path,\n        coordinator,\n    )\n\n    runtime.investigation_coordinator = (\n        coordinator\n    )\n\n    runtime.cluster_verified_evidence_required = (\n        True\n    )\n\n    runtime.production_multi_cluster_readiness = (\n        FakeReadiness(\n            ready=True\n        )\n    )\n\n    context = shadow_context(\n        runtime\n    )\n\n    result = await runtime.run_investigation_shadow(\n        context\n    )\n\n    assert result.status == "ok"\n    assert coordinator.calls == 1\n\n    assert (\n        context.metadata[\n            "production_multi_cluster_readiness"\n        ][\n            "ready"\n        ]\n        is True\n    )\n\n\n@pytest.mark.asyncio\nasync def test_runtime_legacy_shadow_does_not_require_readiness_gate(\n    monkeypatch,\n    tmp_path,\n):\n    coordinator = ReadyCoordinator()\n\n    runtime = _base_runtime(\n        monkeypatch,\n        tmp_path,\n        coordinator,\n    )\n\n    runtime.investigation_coordinator = (\n        coordinator\n    )\n\n    runtime.cluster_verified_evidence_required = (\n        False\n    )\n\n    runtime.production_multi_cluster_readiness = (\n        FakeReadiness(\n            ready=False\n        )\n    )\n\n    context = shadow_context(\n        runtime\n    )\n\n    result = await runtime.run_investigation_shadow(\n        context\n    )\n\n    assert result.status == "ok"\n    assert coordinator.calls == 1\n\n    assert (\n        "production_multi_cluster_readiness"\n        not in context.metadata\n    )\n'


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


def write_text(
    path: Path,
    value: str,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    path.write_text(
        value.replace(
            "\r\n",
            "\n",
        ).replace(
            "\r",
            "\n",
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
                f"{relative} changed after the installed strict-evidence baseline. "
                f"expected_raw_sha256={expected} actual_raw_sha256={actual}. "
                "Refusing stale Production Multi-Cluster Readiness installation."
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

    readiness_file = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "investigation"
        / "multi_cluster_readiness.py"
    )

    runtime_file = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "runtime"
        / "runtime.py"
    )

    test_file = (
        root
        / "services"
        / "agent_runtime"
        / "tests"
        / "test_production_multi_cluster_readiness.py"
    )

    runtime_test_file = (
        root
        / "services"
        / "agent_runtime"
        / "tests"
        / "test_runtime_production_multi_cluster_readiness.py"
    )

    sources = {
        readiness_file: READINESS_SOURCE,
        runtime_file: RUNTIME_SOURCE,
        test_file: TEST_SOURCE,
        runtime_test_file: RUNTIME_TEST_SOURCE,
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
        "Production Multi-Cluster Readiness / Coverage Gate v1.2",
        f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
        "",
        "Purpose:",
        "- prove complete static production-read coverage before Multi-Cluster Investigation Shadow executes",
        "- perform no Kubernetes/Prometheus network request",
        "",
        "Cluster readiness requires:",
        "- explicit Incident cluster",
        "- KubernetesClusterRegistry binding",
        "- PrometheusClusterRegistry binding",
        "- Runtime kubernetes Router uses the exact Kubernetes registry",
        "- Runtime kubernetes_change Router uses the exact Kubernetes registry",
        "- Runtime prometheus Router uses the exact Prometheus registry",
        "- Kubernetes clean HTTPS endpoint + TLS verification + resolved credential + dry-run fallback disabled",
        "- Prometheus clean HTTPS endpoint + TLS verification + mock fallback disabled",
        "- current default Investigation probe families are all covered",
        "- current required Verification provider families (kubernetes + prometheus) are covered",
        "- cluster-verified evidence strict policy is active",
        "",
        "Coverage inventory:",
        "- evaluate(cluster) returns one sanitized cluster readiness report",
        "- evaluate_all() checks the union of configured cluster bindings",
        "- differing Kubernetes/Prometheus cluster sets fail global readiness",
        "",
        "Runtime enforcement:",
        "- legacy/default mode remains unchanged",
        "- when multi-cluster read plane is active, run_investigation_shadow checks readiness before Coordinator execution",
        "- failure is sanitized and contains no endpoint or credential value",
        "- automatic Shadow remains best-effort: readiness failure cannot fail the authoritative PlannerPipeline",
        "",
        "Authority:",
        "- read-only static inspection only",
        "- no Action / Approval / remediation authority",
        "- no network call",
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

        for path in (
            readiness_file,
            test_file,
            runtime_test_file,
        ):
            if path.exists():
                raise RuntimeError(
                    "Production readiness new file already exists; refusing to overwrite an unreviewed file: "
                    + str(
                        path.relative_to(
                            root
                        )
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
                "Production Multi-Cluster Readiness syntax failed"
            )

        focused_paths = require_tests(
            root=root,
            label="readiness focused",
            relative_paths=[
                (
                    "services/agent_runtime/tests/"
                    "test_production_multi_cluster_readiness.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_runtime_production_multi_cluster_readiness.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_production_cluster_verified_evidence_policy.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_cross_source_cluster_evidence_consistency.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_production_scope_integrity.py"
                ),
            ],
        )

        focused = run_command(
            root=root,
            name="Production readiness focused suite",
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
                "Production readiness focused tests failed"
            )

        routing_paths = require_tests(
            root=root,
            label="multi-cluster routing/config",
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

        routing = run_command(
            root=root,
            name="Multi-Cluster routing/config compatibility suite",
            command=[
                "uv",
                "run",
                "pytest",
                *routing_paths,
                "-q",
            ],
        )

        add_command(
            report,
            routing,
        )

        if routing.returncode != 0:
            raise RuntimeError(
                "Production readiness routing compatibility failed"
            )

        runtime_paths = require_tests(
            root=root,
            label="Runtime/Investigation",
            relative_paths=[
                (
                    "services/agent_runtime/tests/"
                    "test_runtime_investigation_wiring.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_auto_shadow_orchestration.py"
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
                    "test_historical_evidence_replay.py"
                ),
            ],
        )

        runtime_compat = run_command(
            root=root,
            name="Runtime / Investigation compatibility suite",
            command=[
                "uv",
                "run",
                "pytest",
                *runtime_paths,
                "-q",
            ],
        )

        add_command(
            report,
            runtime_compat,
        )

        if runtime_compat.returncode != 0:
            raise RuntimeError(
                "Production readiness Runtime compatibility failed"
            )

        verification_paths = require_tests(
            root=root,
            label="Verification",
            relative_paths=[
                (
                    "services/agent_runtime/tests/"
                    "test_verification_profiles.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_verification_collector.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_verification_coordinator.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_verification_fail_closed_e2e.py"
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
                "Production readiness Verification compatibility failed"
            )

        preflight = run_command(
            root=root,
            name="Production readiness architecture preflight",
            command=[
                "uv",
                "run",
                "python",
                "-c",
                (
                    "from pathlib import Path; "
                    "g=Path(r'services/agent_runtime/app/investigation/multi_cluster_readiness.py').read_text(encoding='utf-8'); "
                    "r=Path(r'services/agent_runtime/app/runtime/runtime.py').read_text(encoding='utf-8'); "
                    "print('readiness_gate='+str('class ProductionMultiClusterReadinessGate' in g)); "
                    "print('evaluate_all='+str('def evaluate_all' in g)); "
                    "print('https_checks='+str('kubernetes_https_endpoint_required' in g and 'prometheus_https_endpoint_required' in g)); "
                    "print('fallback_checks='+str('kubernetes_dry_run_fallback_must_be_disabled' in g and 'prometheus_mock_fallback_must_be_disabled' in g)); "
                    "print('runtime_gate='+str('production_multi_cluster_readiness' in r and 'ProductionMultiClusterReadinessError' in r)); "
                    "print('coverage_inventory='+str('production_multi_cluster_coverage' in r)); "
                    "assert 'class ProductionMultiClusterReadinessGate' in g; "
                    "assert 'def evaluate_all' in g; "
                    "assert 'kubernetes_https_endpoint_required' in g; "
                    "assert 'prometheus_https_endpoint_required' in g; "
                    "assert 'kubernetes_dry_run_fallback_must_be_disabled' in g; "
                    "assert 'prometheus_mock_fallback_must_be_disabled' in g; "
                    "assert 'production_multi_cluster_readiness' in r; "
                    "assert 'production_multi_cluster_coverage' in r; "
                    "assert 'ProductionMultiClusterReadinessError' in r"
                ),
            ],
        )

        add_command(
            report,
            preflight,
        )

        if preflight.returncode != 0:
            raise RuntimeError(
                "Production readiness architecture preflight failed"
            )

        authority = run_command(
            root=root,
            name="Production readiness authority boundary",
            command=[
                "uv",
                "run",
                "python",
                "-c",
                (
                    "from pathlib import Path; "
                    "g=Path(r'services/agent_runtime/app/investigation/multi_cluster_readiness.py').read_text(encoding='utf-8'); "
                    "bad=[x for x in ['ActionRuntime','ApprovalService','KubernetesProductionExecutor','.post(','.patch(','.put(','.delete(','httpx.'] if x in g]; "
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
                "Production readiness authority boundary failed"
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
                "Production Multi-Cluster Readiness / Coverage Gate v1.2 is installed.",
                "",
                "Guarantee:",
                "- Production Shadow cannot start in active multi-cluster mode unless the Incident cluster has both Kubernetes and Prometheus coverage",
                "- Runtime ToolManager must be wired to the exact registries used by the readiness proof",
                "- Kubernetes production-read route must disable dry-run fallback and retain TLS + resolved credential",
                "- Prometheus production-read route must disable mock fallback and retain TLS",
                "- all current default Investigation probes are statically covered",
                "- current required Verification provider families are statically covered",
                "- cluster-verified evidence strict policy must be active",
                "- readiness snapshots contain no endpoint or credential values",
                "- evaluate_all() provides a no-network fleet coverage inventory",
                "",
                "Legacy/default mode remains unchanged.",
                "",
                "Next recommended step:",
                "- Production Readiness Live Probe v1: optional bounded GET/query health proof for each READY cluster, still read-only, to distinguish static configuration readiness from live backend reachability.",
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
            "PRODUCTION MULTI-CLUSTER READINESS / COVERAGE GATE V1.2 PASSED"
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
                    "Production Multi-Cluster Readiness / Coverage Gate v1.2 FAILED",
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
            "PRODUCTION MULTI-CLUSTER READINESS / COVERAGE GATE V1.2 FAILED"
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
