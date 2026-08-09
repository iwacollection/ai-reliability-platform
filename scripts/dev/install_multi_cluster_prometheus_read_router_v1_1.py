from __future__ import annotations

import hashlib
import shutil
import subprocess
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


VERSION = "multi-cluster-prometheus-read-router-v1.1"

AFTER_NAME = (
    "multi_cluster_prometheus_read_router_v1_1_after.txt"
)

ERROR_NAME = (
    "multi_cluster_prometheus_read_router_v1_1_error.txt"
)

EXPECTED_HASHES = {'services/agent_runtime/app/tools/factory.py': '2325a43aa1a947e2541a61a2550353dc87eb6a43d46e193f4b6959cdfd75ef3a', 'services/agent_runtime/app/runtime/runtime.py': '2f85ae03d68bad88334af10ba93377eaab5f9eafff19a73bff87c435b8d849ca', 'services/agent_runtime/app/investigation/probes.py': 'd298fba98172f6f9fe66689a3488b3cdfeadd3a4c72db23deea8a4f44d2c04bf', 'services/agent_runtime/tests/test_investigation_probes.py': '20884e98abd829d210c58d0008818be838fa61cff2b0465ad1f1c630328249af'}

ROUTER_SOURCE = 'from __future__ import annotations\n\nfrom collections.abc import Mapping\nfrom re import fullmatch\nfrom types import MappingProxyType\nfrom typing import Any\n\nfrom services.agent_runtime.app.tools.base import (\n    BaseTool,\n)\nfrom services.agent_runtime.app.tools.prometheus.tool import (\n    PrometheusTool,\n)\n\n\nclass PrometheusClusterRoutingError(\n    RuntimeError\n):\n    """\n    The read-only Prometheus cluster route cannot be resolved safely.\n    """\n\n\nclass PrometheusClusterRegistry:\n    """\n    Immutable startup mapping from Incident cluster identity to PrometheusTool.\n\n    Unlike Kubernetes, more than one cluster may intentionally map to the same\n    PrometheusTool. This supports central Thanos/Mimir/Prometheus deployments\n    while keeping cluster routing explicit and fail-closed.\n\n    The registry does not parse credentials, mutate PromQL, or perform network\n    calls.\n    """\n\n    _MAX_CLUSTERS = 64\n    _MAX_CLUSTER_NAME_LENGTH = 128\n    _CLUSTER_PATTERN = (\n        r"[A-Za-z0-9]"\n        r"(?:[A-Za-z0-9_.:-]{0,126}[A-Za-z0-9])?"\n    )\n\n    def __init__(\n        self,\n        bindings: Mapping[\n            str,\n            PrometheusTool,\n        ],\n    ) -> None:\n        if not isinstance(\n            bindings,\n            Mapping,\n        ):\n            raise TypeError(\n                "Prometheus cluster registry requires a mapping"\n            )\n\n        items = tuple(\n            bindings.items()\n        )\n\n        if len(\n            items\n        ) > self._MAX_CLUSTERS:\n            raise PrometheusClusterRoutingError(\n                "Prometheus cluster registry exceeds the bounded cluster limit"\n            )\n\n        mapping: dict[\n            str,\n            PrometheusTool,\n        ] = {}\n\n        for cluster, tool in items:\n            normalized_cluster = (\n                self._cluster_name(\n                    cluster\n                )\n            )\n\n            if not isinstance(\n                tool,\n                PrometheusTool,\n            ):\n                raise TypeError(\n                    "Prometheus cluster registry accepts PrometheusTool values only"\n                )\n\n            if tool.base_url is None:\n                raise PrometheusClusterRoutingError(\n                    "Registered Prometheus cluster has no live endpoint"\n                )\n\n            if (\n                tool.allow_mock_fallback\n                is not False\n            ):\n                raise PrometheusClusterRoutingError(\n                    "Registered Prometheus cluster must disable mock fallback"\n                )\n\n            if tool.verify_tls is not True:\n                raise PrometheusClusterRoutingError(\n                    "Registered Prometheus cluster must verify TLS"\n                )\n\n            mapping[\n                normalized_cluster\n            ] = tool\n\n        self._tools = MappingProxyType(\n            mapping\n        )\n\n    @property\n    def count(\n        self,\n    ) -> int:\n        return len(\n            self._tools\n        )\n\n    @property\n    def cluster_names(\n        self,\n    ) -> tuple[str, ...]:\n        return tuple(\n            sorted(\n                self._tools\n            )\n        )\n\n    def resolve(\n        self,\n        cluster: str | None,\n    ) -> tuple[\n        str,\n        PrometheusTool,\n    ]:\n        requested = (\n            self._requested_cluster(\n                cluster\n            )\n        )\n\n        try:\n            return (\n                requested,\n                self._tools[\n                    requested\n                ],\n            )\n\n        except KeyError:\n            raise PrometheusClusterRoutingError(\n                "Requested Prometheus cluster is not registered"\n            ) from None\n\n    def _requested_cluster(\n        self,\n        cluster: str | None,\n    ) -> str:\n        if cluster is None:\n            if self.count == 1:\n                return next(\n                    iter(\n                        self._tools\n                    )\n                )\n\n            if self.count == 0:\n                raise PrometheusClusterRoutingError(\n                    "No Prometheus clusters are registered"\n                )\n\n            raise PrometheusClusterRoutingError(\n                "Prometheus cluster is required when multiple clusters are registered"\n            )\n\n        return self._cluster_name(\n            cluster\n        )\n\n    @classmethod\n    def _cluster_name(\n        cls,\n        value: Any,\n    ) -> str:\n        if (\n            not isinstance(\n                value,\n                str,\n            )\n            or not value\n            or value != value.strip()\n            or len(\n                value\n            )\n            > cls._MAX_CLUSTER_NAME_LENGTH\n            or "\\x00" in value\n            or fullmatch(\n                cls._CLUSTER_PATTERN,\n                value,\n            )\n            is None\n        ):\n            raise PrometheusClusterRoutingError(\n                "Prometheus cluster identifier is invalid"\n            )\n\n        return value\n\n\nclass MultiClusterPrometheusToolRouter(\n    BaseTool\n):\n    """\n    Route the existing read-only `prometheus` Tool contract by exact cluster.\n\n    Cluster identity selects only the endpoint binding. The existing bounded\n    PromQL remains owned by Investigation ProbeExecutor.\n    """\n\n    def __init__(\n        self,\n        clusters: PrometheusClusterRegistry,\n    ) -> None:\n        if not isinstance(\n            clusters,\n            PrometheusClusterRegistry,\n        ):\n            raise TypeError(\n                "Multi-cluster Prometheus router requires PrometheusClusterRegistry"\n            )\n\n        if clusters.count == 0:\n            raise PrometheusClusterRoutingError(\n                "Multi-cluster Prometheus router requires at least one cluster"\n            )\n\n        self.clusters = clusters\n\n    @property\n    def name(\n        self,\n    ) -> str:\n        return "prometheus"\n\n    @property\n    def is_available(\n        self,\n    ) -> bool:\n        return self.clusters.count > 0\n\n    async def execute(\n        self,\n        *,\n        cluster: str | None = None,\n        **kwargs: Any,\n    ) -> dict[str, Any]:\n        selected_cluster, tool = (\n            self.clusters.resolve(\n                cluster\n            )\n        )\n\n        result = await tool.execute(\n            **kwargs,\n        )\n\n        if not isinstance(\n            result,\n            Mapping,\n        ):\n            raise PrometheusClusterRoutingError(\n                "Prometheus routed Tool returned an invalid result"\n            )\n\n        existing_cluster = result.get(\n            "cluster"\n        )\n\n        if (\n            existing_cluster is not None\n            and existing_cluster\n            != selected_cluster\n        ):\n            raise PrometheusClusterRoutingError(\n                "Prometheus routed Tool returned a mismatched cluster identity"\n            )\n\n        routed = dict(\n            result\n        )\n\n        routed[\n            "cluster"\n        ] = selected_cluster\n\n        return routed\n\n\n__all__ = [\n    "MultiClusterPrometheusToolRouter",\n    "PrometheusClusterRegistry",\n    "PrometheusClusterRoutingError",\n]\n'
FACTORY_SOURCE = 'from services.agent_runtime.app.tools.registry import (\n    ToolRegistry,\n)\n\nfrom services.agent_runtime.app.tools.manager import (\n    ToolManager,\n)\n\nfrom services.agent_runtime.app.tools.mock.echo import (\n    EchoTool,\n)\n\nfrom services.agent_runtime.app.tools.prometheus.tool import (\n    PrometheusTool,\n)\nfrom services.agent_runtime.app.tools.prometheus.router import (\n    MultiClusterPrometheusToolRouter,\n    PrometheusClusterRegistry,\n    PrometheusClusterRoutingError,\n)\n\nfrom services.agent_runtime.app.tools.kubernetes.tool import (\n    KubernetesTool,\n)\n\nfrom services.agent_runtime.app.tools.kubernetes.change_tool import (\n    KubernetesChangeTool,\n)\n\nfrom services.agent_runtime.app.tools.kubernetes.router import (\n    KubernetesClusterRegistry,\n    KubernetesClusterRoutingError,\n    MultiClusterKubernetesChangeToolRouter,\n    MultiClusterKubernetesToolRouter,\n)\n\n\ndef create_tool_manager(\n    kubernetes_cluster_registry: (\n        KubernetesClusterRegistry | None\n    ) = None,\n    prometheus_cluster_registry: (\n        PrometheusClusterRegistry | None\n    ) = None,\n) -> ToolManager:\n\n\n    registry = ToolRegistry()\n\n\n    registry.register(\n        EchoTool()\n    )\n\n\n    if prometheus_cluster_registry is None:\n\n        registry.register(\n            PrometheusTool()\n        )\n\n    else:\n\n        if not isinstance(\n            prometheus_cluster_registry,\n            PrometheusClusterRegistry,\n        ):\n            raise TypeError(\n                "Tool factory Prometheus cluster registry is invalid"\n            )\n\n        if (\n            prometheus_cluster_registry.count\n            == 0\n        ):\n            raise PrometheusClusterRoutingError(\n                "Tool factory multi-cluster Prometheus mode requires at least one cluster"\n            )\n\n        registry.register(\n            MultiClusterPrometheusToolRouter(\n                prometheus_cluster_registry\n            )\n        )\n\n\n    if kubernetes_cluster_registry is None:\n\n        kubernetes = KubernetesTool()\n\n\n        registry.register(\n            kubernetes\n        )\n\n\n        registry.register(\n            KubernetesChangeTool(\n                kubernetes\n            )\n        )\n\n    else:\n\n        if not isinstance(\n            kubernetes_cluster_registry,\n            KubernetesClusterRegistry,\n        ):\n            raise TypeError(\n                "Tool factory Kubernetes cluster registry is invalid"\n            )\n\n        if (\n            kubernetes_cluster_registry.count\n            == 0\n        ):\n            raise KubernetesClusterRoutingError(\n                "Tool factory multi-cluster mode requires at least one cluster"\n            )\n\n\n        registry.register(\n            MultiClusterKubernetesToolRouter(\n                kubernetes_cluster_registry\n            )\n        )\n\n\n        registry.register(\n            MultiClusterKubernetesChangeToolRouter(\n                kubernetes_cluster_registry\n            )\n        )\n\n\n    return ToolManager(\n        registry\n    )\n'
RUNTIME_SOURCE = 'from copy import deepcopy\n\nfrom services.agent_runtime.app.registry.factory import (\n    create_agent_registry,\n)\nfrom services.agent_runtime.app.llm.gateway.factory import (\n    create_llm_gateway,\n)\nfrom services.agent_runtime.app.llm.gateway.gateway import (\n    LLMGateway,\n)\nfrom services.agent_runtime.app.planner.agent_planner import (\n    AgentPlanner,\n)\nfrom services.agent_runtime.app.pipeline.planner_pipeline import (\n    PlannerPipeline,\n)\nfrom services.agent_runtime.app.memory.store import (\n    MemoryStore,\n)\nfrom services.agent_runtime.app.tools.factory import (\n    create_tool_manager,\n)\nfrom services.agent_runtime.app.tools.kubernetes.router import (\n    KubernetesClusterRegistry,\n)\nfrom services.agent_runtime.app.tools.kubernetes.connection_factory import (\n    create_kubernetes_cluster_registry,\n)\nfrom services.agent_runtime.app.tools.prometheus.router import (\n    PrometheusClusterRegistry,\n)\nfrom services.agent_runtime.app.skills.factory import (\n    create_skill_registry,\n)\nfrom services.agent_runtime.app.mcp.factory import (\n    create_mcp_registry,\n)\nfrom services.agent_runtime.app.observability.collector import (\n    TraceCollector,\n)\nfrom services.agent_runtime.app.evaluation.factory import (\n    create_evaluation_registry,\n)\nfrom services.agent_runtime.app.policy.factory import (\n    create_policy_engine,\n)\nfrom services.agent_runtime.app.approval.service import (\n    ApprovalService,\n)\nfrom services.agent_runtime.app.incident.store import (\n    IncidentStore,\n)\nfrom services.agent_runtime.app.incident.service import (\n    IncidentService,\n)\nfrom services.agent_runtime.app.investigation.comparison import (\n    build_rca_investigation_comparison,\n)\nfrom services.agent_runtime.app.investigation.factory import (\n    create_investigation_coordinator,\n)\nfrom services.agent_runtime.app.investigation.llm_gateway_adapter import (\n    InvestigationLLMGatewayAdapter,\n)\nfrom services.agent_runtime.app.investigation.reasoner import (\n    BaseInvestigationReasoner,\n    LLMInvestigationReasoner,\n)\nfrom services.agent_runtime.app.investigation.settings import (\n    InvestigationSettings,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    InvestigationState,\n)\nfrom services.agent_runtime.app.model.context import (\n    AgentContext,\n)\nfrom services.agent_runtime.app.workflow.service import (\n    WorkflowService,\n)\nfrom services.agent_runtime.app.action.execution_service import (\n    ActionExecutionService,\n)\nfrom services.agent_runtime.app.action.execution_store import (\n    ActionExecutionStore,\n)\nfrom services.agent_runtime.app.action.kubernetes_preflight import (\n    KubernetesPreflightResolver,\n)\nfrom services.agent_runtime.app.action.kubernetes_preflight_factory import (\n    create_kubernetes_preflight_resolver,\n)\nfrom services.agent_runtime.app.action.kubernetes_production_executor import (\n    KubernetesProductionExecutor,\n)\nfrom services.agent_runtime.app.action.kubernetes_production_factory import (\n    create_kubernetes_production_executor,\n)\nfrom services.agent_runtime.app.action.preflight_artifact_service import (\n    PreflightArtifactService,\n)\nfrom services.agent_runtime.app.action.preflight_artifact_store import (\n    PreflightArtifactStore,\n)\nfrom services.agent_runtime.app.action.production_action_preparation import (\n    ProductionActionPreparationService,\n)\nfrom services.agent_runtime.app.action.production_action_query import (\n    ProductionActionQueryService,\n)\nfrom services.agent_runtime.app.action.production_action_guard import (\n    ProductionActionExpiryGuard,\n)\nfrom services.agent_runtime.app.action.production_pilot import (\n    KubernetesProductionPilotControl,\n    ProductionPilotReadinessService,\n)\nfrom services.agent_runtime.app.action.production_pilot_factory import (\n    create_kubernetes_production_pilot_control,\n)\nfrom services.agent_runtime.app.action.production_pilot_budget_service import (\n    ProductionPilotBudgetService,\n)\nfrom services.agent_runtime.app.action.production_pilot_budget_store import (\n    ProductionPilotBudgetStore,\n)\nfrom services.agent_runtime.app.action.production_pilot_rehearsal import (\n    ProductionPilotRehearsalService,\n)\nfrom services.agent_runtime.app.action.production_pilot_crash_rehearsal import (\n    ProductionPilotCrashRecoveryRehearsalService,\n)\nfrom services.agent_runtime.app.action.production_pilot_pre_enable_evidence import (\n    ProductionPilotPreEnableEvidenceService,\n)\nfrom services.agent_runtime.app.action.production_pilot_final_handoff import (\n    ProductionPilotFinalHandoffRehearsalService,\n)\nfrom services.agent_runtime.app.action.production_pilot_live_probe import (\n    ProductionPilotLiveReadinessProbe,\n    create_production_pilot_live_readiness_probe,\n)\nfrom services.agent_runtime.app.action.production_pilot_go_no_go_service import (\n    ProductionPilotGoNoGoService,\n)\nfrom services.agent_runtime.app.action.production_pilot_go_no_go_store import (\n    ProductionPilotGoNoGoStore,\n)\nfrom services.agent_runtime.app.action.production_pilot_ceremony_service import (\n    ProductionPilotCeremonyService,\n)\nfrom services.agent_runtime.app.action.production_pilot_ceremony_store import (\n    ProductionPilotCeremonyStore,\n)\nfrom services.agent_runtime.app.verification.collector import (\n    VerificationEvidenceCollector,\n)\nfrom services.agent_runtime.app.verification.coordinator import (\n    VerificationCoordinator,\n)\nfrom services.agent_runtime.app.verification.profiles import (\n    VerificationProfileFactory,\n)\nfrom services.agent_runtime.app.verification.service import (\n    VerificationService,\n)\nfrom services.agent_runtime.app.verification.store import (\n    VerificationStore,\n)\nfrom services.agent_runtime.app.runtime.action_runtime import (\n    ActionRuntime,\n)\nfrom services.agent_runtime.app.runtime.verification_runtime import (\n    VerificationRuntime,\n)\nfrom services.agent_runtime.app.security.factory import (\n    create_authentication_service,\n)\nfrom services.agent_runtime.app.security.policy import (\n    SecurityPolicyEngine,\n)\nfrom services.agent_runtime.app.security.service import (\n    AuthenticationService,\n)\nfrom services.sandbox.executor.local import (\n    LocalSandboxExecutor,\n)\nfrom services.sandbox.policy.validator import (\n    SandboxPolicyValidator,\n)\n\n\nfrom services.agent_runtime.app.incident_evidence.recorder import (\n    ProductionIncidentEvidenceRecorder,\n)\nfrom services.agent_runtime.app.incident_evidence.settings import (\n    IncidentEvidenceRecorderSettings,\n)\n\nclass AgentRuntime:\n    """\n    Runtime container.\n\n    Owns and shares security and runtime infrastructure\n    across Pipeline, Action and Verification.\n\n    security_policy is the RBAC authorization policy. The existing policy\n    attribute remains the remediation business policy engine.\n    """\n\n    def __init__(\n        self,\n        authentication_service: (\n            AuthenticationService | None\n        ) = None,\n        security_policy: (\n            SecurityPolicyEngine | None\n        ) = None,\n        kubernetes_preflight: (\n            KubernetesPreflightResolver | None\n        ) = None,\n        kubernetes_production_executor: (\n            KubernetesProductionExecutor | None\n        ) = None,\n        production_pilot_control: (\n            KubernetesProductionPilotControl | None\n        ) = None,\n        production_pilot_budget_service: (\n            ProductionPilotBudgetService | None\n        ) = None,\n        production_pilot_live_probe: (\n            ProductionPilotLiveReadinessProbe | None\n        ) = None,\n        kubernetes_cluster_registry: (\n            KubernetesClusterRegistry | None\n        ) = None,\n        prometheus_cluster_registry: (\n            PrometheusClusterRegistry | None\n        ) = None,\n        llm_gateway: (\n            LLMGateway | None\n        ) = None,\n        investigation_reasoner: (\n            BaseInvestigationReasoner | None\n        ) = None,\n        investigation_settings: (\n            InvestigationSettings | None\n        ) = None,\n    ) -> None:\n        # Validate every injected security component before factories, stores\n        # or other runtime components can produce side effects.\n        if (\n            authentication_service is not None\n            and not isinstance(\n                authentication_service,\n                AuthenticationService,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime authentication service is invalid"\n            )\n\n        if (\n            security_policy is not None\n            and not isinstance(\n                security_policy,\n                SecurityPolicyEngine,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime security policy is invalid"\n            )\n\n        if (\n            kubernetes_preflight is not None\n            and not isinstance(\n                kubernetes_preflight,\n                KubernetesPreflightResolver,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Kubernetes preflight resolver is invalid"\n            )\n\n        if (\n            kubernetes_production_executor is not None\n            and not isinstance(\n                kubernetes_production_executor,\n                KubernetesProductionExecutor,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Kubernetes production executor is invalid"\n            )\n\n        if (\n            production_pilot_control is not None\n            and not isinstance(\n                production_pilot_control,\n                KubernetesProductionPilotControl,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Kubernetes production pilot control is invalid"\n            )\n\n        if (\n            production_pilot_budget_service is not None\n            and not isinstance(\n                production_pilot_budget_service,\n                ProductionPilotBudgetService,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Kubernetes production pilot budget service is invalid"\n            )\n\n        if (\n            production_pilot_live_probe is not None\n            and not isinstance(\n                production_pilot_live_probe,\n                ProductionPilotLiveReadinessProbe,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Production Pilot live probe is invalid"\n            )\n\n        if (\n            kubernetes_cluster_registry is not None\n            and not isinstance(\n                kubernetes_cluster_registry,\n                KubernetesClusterRegistry,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Kubernetes cluster registry is invalid"\n            )\n\n        if (\n            prometheus_cluster_registry is not None\n            and not isinstance(\n                prometheus_cluster_registry,\n                PrometheusClusterRegistry,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Prometheus cluster registry is invalid"\n            )\n\n        if (\n            llm_gateway is not None\n            and not isinstance(\n                llm_gateway,\n                LLMGateway,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime shared LLM gateway is invalid"\n            )\n\n        if (\n            investigation_reasoner is not None\n            and not isinstance(\n                investigation_reasoner,\n                BaseInvestigationReasoner,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Investigation reasoner is invalid"\n            )\n\n        if (\n            investigation_settings is not None\n            and not isinstance(\n                investigation_settings,\n                InvestigationSettings,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Investigation settings are invalid"\n            )\n\n        # Resolve disabled-default Investigation configuration before any\n        # Runtime store, tool, credential, network or LLM component is created.\n        self.investigation_settings = (\n            investigation_settings\n            if investigation_settings is not None\n            else InvestigationSettings.from_environment()\n        )\n\n        investigation_shared_gateway = None\n\n        # An enabled LLM-backed Investigation must use the exact shared\n        # LLMGateway instance that AgentRuntime will provide to its Agents.\n        #\n        # Disabled Investigation deliberately does not inspect or touch the\n        # supplied reasoner\'s LLM adapter.\n        if (\n            self.investigation_settings.enabled\n            and isinstance(\n                investigation_reasoner,\n                LLMInvestigationReasoner,\n            )\n        ):\n            investigation_llm = (\n                investigation_reasoner.investigation_llm\n            )\n\n            if not isinstance(\n                investigation_llm,\n                InvestigationLLMGatewayAdapter,\n            ):\n                raise TypeError(\n                    "AgentRuntime LLM Investigation requires "\n                    "InvestigationLLMGatewayAdapter"\n                )\n\n            investigation_shared_gateway = (\n                investigation_llm.llm_gateway\n            )\n\n            if not isinstance(\n                investigation_shared_gateway,\n                LLMGateway,\n            ):\n                raise TypeError(\n                    "AgentRuntime Investigation shared LLM gateway is invalid"\n                )\n\n            if (\n                llm_gateway is not None\n                and investigation_shared_gateway\n                is not llm_gateway\n            ):\n                raise TypeError(\n                    "AgentRuntime Investigation LLM gateway must be shared"\n                )\n\n        # Preserve the existing fail-closed Investigation assembly boundary.\n        # Enabled mode without an explicit reasoner still fails here before\n        # any Runtime or LLM infrastructure is constructed.\n        self.investigation_coordinator = (\n            create_investigation_coordinator(\n                reasoner=investigation_reasoner,\n                settings=self.investigation_settings,\n            )\n        )\n\n        # Do not construct a default Gateway yet. Keeping this unresolved\n        # preserves the previous initialization order. If Investigation\n        # already carries the approved Gateway Adapter, Runtime adopts that\n        # exact Gateway object as its shared instance.\n        self.llm_gateway = (\n            llm_gateway\n            if llm_gateway is not None\n            else investigation_shared_gateway\n        )\n\n        self.authentication = (\n            authentication_service\n            if authentication_service is not None\n            else create_authentication_service()\n        )\n\n        self.security_policy = (\n            security_policy\n            if security_policy is not None\n            else SecurityPolicyEngine()\n        )\n\n        self.kubernetes_preflight = (\n            kubernetes_preflight\n            if kubernetes_preflight is not None\n            else create_kubernetes_preflight_resolver()\n        )\n\n        self.production_pilot_control = (\n            production_pilot_control\n            if production_pilot_control is not None\n            else create_kubernetes_production_pilot_control()\n        )\n\n        # This independent gate may read both credential values at startup,\n        # but can construct only a two-GET probe. Disabled mode returns before\n        # any credential or CA access.\n        self.production_pilot_live_probe = (\n            production_pilot_live_probe\n            if production_pilot_live_probe is not None\n            else create_production_pilot_live_readiness_probe()\n        )\n\n        self.production_pilot_budget_store = None\n        self.production_pilot_budget_service = (\n            production_pilot_budget_service\n        )\n        if (\n            self.production_pilot_budget_service is None\n            and self.production_pilot_control.config.enabled\n        ):\n            self.production_pilot_budget_store = (\n                ProductionPilotBudgetStore()\n            )\n            self.production_pilot_budget_service = (\n                ProductionPilotBudgetService(\n                    store=(\n                        self.production_pilot_budget_store\n                    )\n                )\n            )\n\n        self.kubernetes_production_executor = (\n            kubernetes_production_executor\n            if kubernetes_production_executor is not None\n            else create_kubernetes_production_executor(\n                pilot_control=(\n                    self.production_pilot_control\n                ),\n                pilot_budget_service=(\n                    self.production_pilot_budget_service\n                ),\n            )\n        )\n\n        if self.kubernetes_production_executor is not None:\n            executor_control = getattr(\n                self.kubernetes_production_executor,\n                "pilot_control",\n                None,\n            )\n            if executor_control is None:\n                self.kubernetes_production_executor.pilot_control = (\n                    self.production_pilot_control\n                )\n            elif executor_control is not self.production_pilot_control:\n                raise TypeError(\n                    "AgentRuntime Kubernetes production pilot control must be shared"\n                )\n            executor_budget = getattr(\n                self.kubernetes_production_executor,\n                "pilot_budget_service",\n                None,\n            )\n            if executor_budget is None:\n                if self.production_pilot_budget_service is None:\n                    raise TypeError(\n                        "AgentRuntime Kubernetes production pilot budget is unavailable"\n                    )\n                self.kubernetes_production_executor.pilot_budget_service = (\n                    self.production_pilot_budget_service\n                )\n            elif executor_budget is not self.production_pilot_budget_service:\n                raise TypeError(\n                    "AgentRuntime Kubernetes production pilot budget must be shared"\n                )\n\n        if (\n            self.kubernetes_production_executor is not None\n            and self.kubernetes_preflight is None\n        ):\n            raise TypeError(\n                "AgentRuntime Kubernetes production executor requires "\n                "trusted preflight"\n            )\n\n        self.production_pilot_readiness = (\n            ProductionPilotReadinessService(\n                control=(\n                    self.production_pilot_control\n                ),\n                production_executor_configured=(\n                    self.kubernetes_production_executor\n                    is not None\n                ),\n            )\n        )\n        self.production_pilot_rehearsal = (\n            ProductionPilotRehearsalService(\n                control=(\n                    self.production_pilot_control\n                ),\n                budget_service=(\n                    self.production_pilot_budget_service\n                ),\n                production_executor_configured=(\n                    self.kubernetes_production_executor\n                    is not None\n                ),\n            )\n        )\n        # Pure recovery-policy proof. It owns no store, credential, network\n        # client or executor and is available while the production gate is\n        # disabled so operators can rehearse recovery before enablement.\n        self.production_pilot_crash_recovery_rehearsal = (\n            ProductionPilotCrashRecoveryRehearsalService()\n        )\n\n        self.memory = MemoryStore()\n\n        if (\n            kubernetes_cluster_registry\n            is None\n        ):\n            self.kubernetes_cluster_registry = (\n                create_kubernetes_cluster_registry()\n            )\n        else:\n            self.kubernetes_cluster_registry = (\n                kubernetes_cluster_registry\n            )\n\n        self.prometheus_cluster_registry = (\n            prometheus_cluster_registry\n        )\n\n        tool_manager_kwargs = {}\n\n        if (\n            self.kubernetes_cluster_registry\n            is not None\n        ):\n            tool_manager_kwargs[\n                "kubernetes_cluster_registry"\n            ] = self.kubernetes_cluster_registry\n\n        if (\n            self.prometheus_cluster_registry\n            is not None\n        ):\n            tool_manager_kwargs[\n                "prometheus_cluster_registry"\n            ] = self.prometheus_cluster_registry\n\n        if tool_manager_kwargs:\n            self.tools = create_tool_manager(\n                **tool_manager_kwargs\n            )\n        else:\n            self.tools = create_tool_manager()\n\n        self.skills = create_skill_registry()\n        self.mcp = create_mcp_registry()\n        self.tracer = TraceCollector()\n        self.evaluators = create_evaluation_registry()\n\n        # Remediation business policy. This is intentionally separate from\n        # security_policy, which authorizes operator-facing operations.\n        self.policy = create_policy_engine()\n\n        self.preflight_artifact_store = None\n        self.preflight_artifact_service = None\n        self.production_action_guard = None\n        self.production_action_preparation = None\n        self.production_action_query = None\n\n        if self.kubernetes_preflight is not None:\n            self.preflight_artifact_store = PreflightArtifactStore()\n            self.preflight_artifact_service = PreflightArtifactService(\n                store=self.preflight_artifact_store\n            )\n            self.production_action_guard = (\n                ProductionActionExpiryGuard(\n                    artifact_service=(\n                        self.preflight_artifact_service\n                    )\n                )\n            )\n\n        self.approval = ApprovalService()\n\n        if self.production_action_guard is not None:\n            self.approval.manager.set_transition_guard(\n                self.production_action_guard\n            )\n\n        if self.preflight_artifact_service is not None:\n            self.production_action_preparation = (\n                ProductionActionPreparationService(\n                    resolver=self.kubernetes_preflight,\n                    artifact_service=self.preflight_artifact_service,\n                    approval_service=self.approval,\n                )\n            )\n\n        self.production_pilot_ceremony_store = None\n        self.production_pilot_ceremony = None\n        if (\n            self.production_pilot_control.config.enabled\n            and self.production_pilot_budget_service is not None\n            and self.preflight_artifact_service is not None\n        ):\n            self.production_pilot_ceremony_store = (\n                ProductionPilotCeremonyStore()\n            )\n            self.production_pilot_ceremony = (\n                ProductionPilotCeremonyService(\n                    store=(\n                        self.production_pilot_ceremony_store\n                    ),\n                    control=(\n                        self.production_pilot_control\n                    ),\n                    rehearsal=(\n                        self.production_pilot_rehearsal\n                    ),\n                    budget_service=(\n                        self.production_pilot_budget_service\n                    ),\n                    approval_service=self.approval,\n                    artifact_service=(\n                        self.preflight_artifact_service\n                    ),\n                )\n            )\n\n        self.incident_store = IncidentStore()\n\n        if self.preflight_artifact_service is not None:\n            self.production_action_query = (\n                ProductionActionQueryService(\n                    artifact_service=(\n                        self.preflight_artifact_service\n                    ),\n                    approval_service=self.approval,\n                    incident_store=self.incident_store,\n                )\n            )\n\n        self.incident_service = IncidentService(\n            store=self.incident_store\n        )\n\n        self.workflow_service = WorkflowService(\n            incident_service=self.incident_service\n        )\n\n        self.action_execution_store = ActionExecutionStore()\n\n        self.action_execution_service = ActionExecutionService(\n            store=self.action_execution_store\n        )\n\n        self.action_runtime = ActionRuntime(\n            approval_service=self.approval,\n            incident_store=self.incident_store,\n            action_execution_service=self.action_execution_service,\n            production_action_guard=(\n                self.production_action_guard\n            ),\n            kubernetes_production_executor=(\n                self.kubernetes_production_executor\n            ),\n            preflight_artifact_service=(\n                self.preflight_artifact_service\n                if self.kubernetes_production_executor is not None\n                else None\n            ),\n            production_pilot_control=(\n                self.production_pilot_control\n            ),\n            production_pilot_budget_service=(\n                self.production_pilot_budget_service\n            ),\n            production_pilot_ceremony_service=(\n                self.production_pilot_ceremony\n                if self.kubernetes_production_executor is not None\n                else None\n            ),\n        )\n\n        self.verification_store = VerificationStore()\n\n        self.verification = VerificationService(\n            store=self.verification_store\n        )\n\n        self.verification_runtime = VerificationRuntime(\n            verification_service=self.verification,\n            incident_store=self.incident_store,\n        )\n\n        self.verification_profile_factory = VerificationProfileFactory()\n\n        self.verification_collector = VerificationEvidenceCollector(\n            tools=self.tools\n        )\n\n        self.verification_coordinator = VerificationCoordinator(\n            profile_factory=self.verification_profile_factory,\n            collector=self.verification_collector,\n            verification_runtime=self.verification_runtime,\n        )\n\n        # Final pre-enable evidence is assembled only when every production\n        # preparation component is available. The service is read-only and\n        # deliberately owns no executor or mutable workflow operation.\n        self.production_pilot_pre_enable_evidence = None\n        if all(\n            component is not None\n            for component in (\n                self.production_pilot_ceremony,\n                self.production_pilot_budget_service,\n                self.preflight_artifact_service,\n            )\n        ):\n            self.production_pilot_pre_enable_evidence = (\n                ProductionPilotPreEnableEvidenceService(\n                    readiness_service=(\n                        self.production_pilot_readiness\n                    ),\n                    rehearsal_service=(\n                        self.production_pilot_rehearsal\n                    ),\n                    crash_rehearsal_service=(\n                        self.production_pilot_crash_recovery_rehearsal\n                    ),\n                    ceremony_service=(\n                        self.production_pilot_ceremony\n                    ),\n                    budget_service=(\n                        self.production_pilot_budget_service\n                    ),\n                    artifact_service=(\n                        self.preflight_artifact_service\n                    ),\n                    approval_service=self.approval,\n                    incident_store=self.incident_store,\n                    action_execution_service=(\n                        self.action_execution_service\n                    ),\n                    verification_service=self.verification,\n                )\n            )\n\n        # The final handoff rehearsal is also strictly read-only. It is\n        # available only with the full prepared Pilot chain and explicitly\n        # records whether production executors remain absent while the gate\n        # is disabled.\n        self.production_pilot_final_handoff_rehearsal = None\n        if self.production_pilot_pre_enable_evidence is not None:\n            self.production_pilot_final_handoff_rehearsal = (\n                ProductionPilotFinalHandoffRehearsalService(\n                    pilot_control=self.production_pilot_control,\n                    pre_enable_evidence_service=(\n                        self.production_pilot_pre_enable_evidence\n                    ),\n                    preflight_resolver=self.kubernetes_preflight,\n                    production_executor_configured=(\n                        self.kubernetes_production_executor is not None\n                    ),\n                    action_runtime_production_executor_configured=(\n                        getattr(\n                            self.action_runtime,\n                            "kubernetes_production_executor",\n                            None,\n                        )\n                        is not None\n                    ),\n                )\n            )\n\n        # A dedicated database is created only when the separately gated live\n        # probe exists and the full zero-write handoff chain is available.\n        self.production_pilot_go_no_go_store = None\n        self.production_pilot_go_no_go = None\n        if (\n            self.production_pilot_live_probe is not None\n            and self.production_pilot_final_handoff_rehearsal is not None\n            and self.preflight_artifact_service is not None\n        ):\n            self.production_pilot_go_no_go_store = (\n                ProductionPilotGoNoGoStore()\n            )\n            self.production_pilot_go_no_go = (\n                ProductionPilotGoNoGoService(\n                    store=self.production_pilot_go_no_go_store,\n                    live_probe=self.production_pilot_live_probe,\n                    final_handoff_service=(\n                        self.production_pilot_final_handoff_rehearsal\n                    ),\n                    artifact_service=self.preflight_artifact_service,\n                    pilot_control=self.production_pilot_control,\n                )\n            )\n\n        self.sandbox = LocalSandboxExecutor()\n\n        self.sandbox_policy = SandboxPolicyValidator()\n\n        if self.llm_gateway is None:\n            self.llm_gateway = create_llm_gateway()\n\n        self.registry = create_agent_registry(\n            llm_gateway=self.llm_gateway,\n        )\n\n        self.planner = AgentPlanner()\n\n        self.pipeline = PlannerPipeline(\n            self.registry,\n            self.planner,\n            self.tracer,\n            self.evaluators,\n            incident_store=self.incident_store,\n            incident_service=self.incident_service,\n            workflow_service=self.workflow_service,\n        )\n\n    async def execute(\n        self,\n        context: AgentContext,\n    ):\n        """\n        Execute the primary PlannerPipeline and, when explicitly enabled,\n        run Investigation automatically as a best-effort Shadow.\n\n        Ordering is deliberate:\n\n        1. PlannerPipeline completes first.\n        2. Investigation receives an isolated AgentContext.\n        3. Only the bounded investigation_shadow snapshot is copied back.\n\n        Investigation can never change the Pipeline result, Incident,\n        variables, results, trace, Approval, executions or evaluations.\n\n        Investigation orchestration failure is sanitized and recorded in\n        metadata without failing an otherwise successful Pipeline execution.\n        """\n\n        if not isinstance(\n            context,\n            AgentContext,\n        ):\n            raise TypeError(\n                "AgentRuntime execution context is invalid"\n            )\n\n        # Reserved Shadow metadata from a previous execution must never be\n        # visible to the primary Pipeline, even when this Runtime currently\n        # has Investigation disabled.\n        for reserved_key in (\n            "investigation_shadow",\n            "investigation_shadow_orchestration",\n            "investigation_rca_comparison",\n        ):\n            context.metadata.pop(\n                reserved_key,\n                None,\n            )\n\n        # Primary workflow semantics remain authoritative. Pipeline failure\n        # propagates normally and Investigation is not attempted afterward.\n        context.metadata.pop(\n            "incident_evidence_recorder",\n            None,\n        )\n\n        results = await self.pipeline.execute(\n            context\n        )\n\n        # Evidence Recorder is evaluation-only and best-effort.\n        await self._record_incident_evidence_shadow(\n            context\n        )\n\n        if self.investigation_coordinator is None:\n            return results\n\n        shadow_context = (\n            self._create_investigation_shadow_context(\n                context\n            )\n        )\n\n        try:\n            await self.run_investigation_shadow(\n                shadow_context\n            )\n\n            snapshot = shadow_context.metadata.get(\n                "investigation_shadow"\n            )\n\n            if (\n                not isinstance(\n                    snapshot,\n                    dict,\n                )\n                or snapshot.get(\n                    "shadow_mode"\n                )\n                is not True\n                or snapshot.get(\n                    "read_only"\n                )\n                is not True\n            ):\n                raise RuntimeError(\n                    "Investigation Shadow snapshot is invalid"\n                )\n\n            context.metadata[\n                "investigation_shadow"\n            ] = deepcopy(\n                snapshot\n            )\n\n        except Exception as exc:\n            # Shadow means Shadow: an Investigation orchestration fault must\n            # never convert a successful PlannerPipeline execution to failed.\n            #\n            # Raw exception text is deliberately excluded because provider,\n            # URL, credential or tool details may be present in it.\n            context.metadata[\n                "investigation_shadow_orchestration"\n            ] = {\n                "shadow_mode": True,\n                "read_only": True,\n                "automatic": True,\n                "status": "failed",\n                "failure_code": (\n                    type(exc).__name__[:256]\n                ),\n            }\n\n        # Comparison is evaluation-only. It cannot change the authoritative\n        # RCA stored in context.variables["rca"] and has no Healing authority.\n        try:\n            context.metadata[\n                "investigation_rca_comparison"\n            ] = build_rca_investigation_comparison(\n                rca=context.variables.get(\n                    "rca"\n                ),\n                investigation_snapshot=(\n                    context.metadata.get(\n                        "investigation_shadow"\n                    )\n                ),\n                orchestration_snapshot=(\n                    context.metadata.get(\n                        "investigation_shadow_orchestration"\n                    )\n                ),\n            )\n        except Exception as exc:\n            # A comparison bug must remain weaker than Shadow itself and must\n            # never fail a successful primary Pipeline.\n            context.metadata[\n                "investigation_rca_comparison"\n            ] = {\n                "schema_version": "v1",\n                "shadow_mode": True,\n                "read_only": True,\n                "decision_influence": False,\n                "available": False,\n                "comparison_status": (\n                    "comparison_failed"\n                ),\n                "failure_code": (\n                    type(exc).__name__[:256]\n                ),\n            }\n\n        return results\n\n    def _create_investigation_shadow_context(\n        self,\n        context: AgentContext,\n    ) -> AgentContext:\n        """\n        Build the minimum-privilege context for automatic Investigation.\n\n        Copied:\n        - event input\n        - request correlation ID\n\n        Shared:\n        - exact Runtime-owned ToolManager\n\n        Deliberately not shared:\n        - Incident\n        - variables\n        - results\n        - metadata\n        - trace\n        - memory\n        - skills\n        - MCP\n        - sandbox\n        - Approval\n        - executions\n        - evaluations\n        """\n\n        return AgentContext(\n            request_id=context.request_id,\n            event=deepcopy(\n                context.event\n            ),\n            tools=self.tools,\n            metadata={},\n        )\n\n    async def run_investigation_shadow(\n        self,\n        context: AgentContext,\n    ) -> InvestigationState:\n        """\n        Explicitly execute the enabled read-only Investigation Shadow.\n\n        This method is intentionally separate from PlannerPipeline.\n\n        PlannerPipeline itself never invokes Investigation. AgentRuntime\n        may call this lower-level entry point after a successful Pipeline\n        execution when automatic Shadow Investigation is enabled.\n\n        The supplied AgentContext must use the exact Runtime ToolManager so\n        Investigation probes cannot bypass Runtime-owned tool boundaries.\n        """\n\n        if not isinstance(\n            context,\n            AgentContext,\n        ):\n            raise TypeError(\n                "AgentRuntime Investigation Shadow context is invalid"\n            )\n\n        if self.investigation_coordinator is None:\n            raise RuntimeError(\n                "AgentRuntime Investigation Shadow is disabled"\n            )\n\n        if context.tools is not self.tools:\n            raise TypeError(\n                "AgentRuntime Investigation Shadow requires shared Runtime tools"\n            )\n\n        return await (\n            self.investigation_coordinator.investigate(\n                context\n            )\n        )\n\n    async def _record_incident_evidence_shadow(\n        self,\n        context: AgentContext,\n    ) -> None:\n        """\n        Best-effort, decision-isolated production evidence preservation.\n\n        Runs only after the authoritative PlannerPipeline succeeds.\n        Disabled mode constructs no Recorder and issues no production Probe.\n        """\n\n        try:\n            settings = (\n                IncidentEvidenceRecorderSettings\n                .from_environment()\n            )\n        except Exception as exc:\n            context.metadata[\n                "incident_evidence_recorder"\n            ] = {\n                "schema_version": "v1",\n                "shadow_mode": True,\n                "read_only": True,\n                "decision_influence": False,\n                "automatic": True,\n                "status": "failed",\n                "failure_code": (\n                    type(exc).__name__[:256]\n                ),\n            }\n            return\n\n        if not settings.enabled:\n            return\n\n        recorder_context = AgentContext(\n            request_id=context.request_id,\n            event=deepcopy(\n                context.event\n            ),\n            tools=self.tools,\n            metadata={},\n        )\n\n        try:\n            recorder = ProductionIncidentEvidenceRecorder(\n                settings.resolve_output_dir()\n            )\n\n            result = await recorder.record(\n                recorder_context\n            )\n\n            context.metadata[\n                "incident_evidence_recorder"\n            ] = {\n                "schema_version": "v1",\n                "shadow_mode": True,\n                "read_only": True,\n                "decision_influence": False,\n                "automatic": True,\n                "status": "captured",\n                "created": result.created,\n                "incident_id": result.incident_id,\n                "observation_count": (\n                    result.observation_count\n                ),\n                "capture_file": result.path.name,\n            }\n\n        except Exception as exc:\n            context.metadata[\n                "incident_evidence_recorder"\n            ] = {\n                "schema_version": "v1",\n                "shadow_mode": True,\n                "read_only": True,\n                "decision_influence": False,\n                "automatic": True,\n                "status": "failed",\n                "failure_code": (\n                    type(exc).__name__[:256]\n                ),\n            }\n'
PROBES_SOURCE = 'import re\nfrom collections.abc import Mapping\nfrom datetime import UTC, datetime\nfrom math import isfinite\nfrom typing import Any\n\nfrom services.agent_runtime.app.investigation.evidence_time import (\n    InvestigationEvidenceTimeError,\n    InvestigationEvidenceTimePolicy,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    EvidenceItem,\n    InvestigationProbe,\n    InvestigationScope,\n    default_investigation_probes,\n)\n\n\nclass InvestigationProbeError(RuntimeError):\n    """\n    Base error for the bounded read-only probe adapter.\n    """\n\n\nclass InvestigationToolUnavailableError(\n    InvestigationProbeError\n):\n    """\n    Runtime ToolManager is unavailable.\n    """\n\n\nclass InvestigationProbeResponseError(\n    InvestigationProbeError\n):\n    """\n    A read-only tool returned evidence that cannot cross the\n    Investigation trust boundary.\n    """\n\n\nclass ReadOnlyInvestigationProbeExecutor:\n    """\n    Translate symbolic Investigation probes into exact read-only tool calls.\n\n    The reasoner selects only an InvestigationProbe enum value.\n\n    This adapter owns:\n\n    - fixed Kubernetes read-only actions;\n    - fixed bounded previous-container log collection;\n    - fixed Prometheus query templates;\n    - provider/source validation;\n    - read-only mode validation;\n    - production-signal validation;\n    - observed-at validation;\n    - bounded evidence normalization.\n\n    The reasoner cannot provide Kubernetes verbs, resource kinds, PromQL,\n    URLs, credentials or raw tool arguments.\n    """\n\n    _TRUSTED_MODE = "read_only"\n    _MAX_LOG_TOOL_CHARS = 4000\n    _MAX_LOG_EVIDENCE_CHARS = 1800\n    _MAX_LOG_LINES = 80\n\n    def __init__(\n        self,\n        time_policy: (\n            InvestigationEvidenceTimePolicy\n            | None\n        ) = None,\n    ) -> None:\n        self.time_policy = (\n            time_policy\n            if time_policy is not None\n            else InvestigationEvidenceTimePolicy()\n        )\n\n    @staticmethod\n    def available_probes(\n        context,\n    ) -> list[InvestigationProbe]:\n        probes = default_investigation_probes()\n\n        tools = getattr(\n            context,\n            "tools",\n            None,\n        )\n\n        registry = getattr(\n            tools,\n            "registry",\n            None,\n        )\n\n        getter = getattr(\n            registry,\n            "get",\n            None,\n        )\n\n        if not callable(\n            getter\n        ):\n            return probes\n\n        try:\n            change_tool = getter(\n                "kubernetes_change"\n            )\n        except KeyError:\n            return probes\n\n        if (\n            getattr(\n                change_tool,\n                "is_available",\n                True,\n            )\n            is not True\n        ):\n            return probes\n\n        probes.append(\n            InvestigationProbe.KUBERNETES_WORKLOAD_CHANGE\n        )\n\n        probes.append(\n            InvestigationProbe.KUBERNETES_CONFIG_CHANGE\n        )\n\n        return probes\n\n    async def collect(\n        self,\n        context,\n        scope: InvestigationScope,\n        probe: InvestigationProbe,\n    ) -> EvidenceItem:\n        tools = getattr(\n            context,\n            "tools",\n            None,\n        )\n\n        if tools is None:\n            raise InvestigationToolUnavailableError(\n                "Runtime tools are unavailable"\n            )\n\n        if (\n            probe\n            == InvestigationProbe.KUBERNETES_POD_STATE\n        ):\n            result = await tools.call(\n                "kubernetes",\n                context=context,\n                action="describe",\n                resource="pod",\n                target=scope.resource,\n                namespace=scope.namespace,\n                cluster=scope.cluster,\n            )\n\n            return self._normalize_kubernetes(\n                scope=scope,\n                probe=probe,\n                result=result,\n            )\n\n        if (\n            probe\n            == InvestigationProbe.KUBERNETES_PREVIOUS_CONTAINER_LOGS\n        ):\n            result = await tools.call(\n                "kubernetes",\n                context=context,\n                action="previous_logs",\n                resource="pod",\n                target=scope.resource,\n                namespace=scope.namespace,\n                cluster=scope.cluster,\n            )\n\n            return self._normalize_kubernetes_logs(\n                scope=scope,\n                probe=probe,\n                result=result,\n            )\n\n        if (\n            probe\n            == InvestigationProbe.KUBERNETES_WORKLOAD_CHANGE\n        ):\n            result = await tools.call(\n                "kubernetes_change",\n                context=context,\n                target=scope.resource,\n                namespace=scope.namespace,\n                cluster=scope.cluster,\n                incident_time=(\n                    scope.event_occurred_at.isoformat()\n                    if scope.event_occurred_at\n                    is not None\n                    else None\n                ),\n                view="workload",\n            )\n\n            return self._normalize_kubernetes_change(\n                scope=scope,\n                probe=probe,\n                result=result,\n            )\n\n        if (\n            probe\n            == InvestigationProbe.KUBERNETES_CONFIG_CHANGE\n        ):\n            result = await tools.call(\n                "kubernetes_change",\n                context=context,\n                target=scope.resource,\n                namespace=scope.namespace,\n                cluster=scope.cluster,\n                incident_time=(\n                    scope.event_occurred_at.isoformat()\n                    if scope.event_occurred_at\n                    is not None\n                    else None\n                ),\n                view="config",\n            )\n\n            return self._normalize_kubernetes_config_change(\n                scope=scope,\n                probe=probe,\n                result=result,\n            )\n\n        query = self._prometheus_query(\n            scope=scope,\n            probe=probe,\n        )\n\n        query_time = self.time_policy.query_time(\n            scope=scope,\n            probe=probe,\n        )\n\n        call_arguments = {\n            "query": query,\n        }\n\n        if scope.cluster is not None:\n            call_arguments[\n                "cluster"\n            ] = scope.cluster\n\n        if query_time is not None:\n            call_arguments["time"] = (\n                query_time\n            )\n\n        result = await tools.call(\n            "prometheus",\n            context=context,\n            **call_arguments,\n        )\n\n        return self._normalize_prometheus(\n            scope=scope,\n            probe=probe,\n            result=result,\n        )\n\n    @classmethod\n    def _prometheus_query(\n        cls,\n        scope: InvestigationScope,\n        probe: InvestigationProbe,\n    ) -> str:\n        labels = [\n            (\n                \'pod="\'\n                f\'{cls._escape_label(scope.resource)}\'\n                \'"\'\n            ),\n            (\n                \'namespace="\'\n                f\'{cls._escape_label(scope.namespace)}\'\n                \'"\'\n            ),\n        ]\n\n        if scope.cluster:\n            labels.append(\n                \'cluster="\'\n                f\'{cls._escape_label(scope.cluster)}\'\n                \'"\'\n            )\n\n        selector = ",".join(\n            labels\n        )\n\n        if (\n            probe\n            == InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET\n        ):\n            return (\n                "sum(container_memory_working_set_bytes{"\n                f\'{selector},container!="POD",container!="",image!=""\'\n                "})"\n            )\n\n        if (\n            probe\n            == InvestigationProbe.PROMETHEUS_MEMORY_LIMIT\n        ):\n            return (\n                "sum(kube_pod_container_resource_limits{"\n                f\'{selector},resource="memory",unit="byte"\'\n                "})"\n            )\n\n        if (\n            probe\n            == InvestigationProbe.PROMETHEUS_RESTART_COUNT\n        ):\n            return (\n                "sum(kube_pod_container_status_restarts_total{"\n                f"{selector}"\n                "})"\n            )\n\n        raise InvestigationProbeError(\n            "Unsupported investigation probe"\n        )\n\n    def _normalize_kubernetes(\n        self,\n        scope: InvestigationScope,\n        probe: InvestigationProbe,\n        result: Any,\n    ) -> EvidenceItem:\n        data, observed_at = (\n            self._validate_tool_evidence(\n                result=result,\n                expected_source="kubernetes",\n            )\n        )\n\n        if "phase" not in data:\n            raise InvestigationProbeResponseError(\n                "Kubernetes evidence phase is missing"\n            )\n\n        containers = data.get(\n            "containers"\n        )\n\n        if not isinstance(\n            containers,\n            list,\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes evidence containers are invalid"\n            )\n\n        restart_counts: list[int] = []\n        state_reasons: set[str] = set()\n        termination_reasons: set[str] = set()\n\n        for container in containers[:32]:\n            if not isinstance(\n                container,\n                Mapping,\n            ):\n                continue\n\n            restart_count = container.get(\n                "restart_count"\n            )\n\n            if isinstance(\n                restart_count,\n                int,\n            ):\n                restart_counts.append(\n                    restart_count\n                )\n\n            state_reason = container.get(\n                "state_reason"\n            )\n\n            if (\n                isinstance(\n                    state_reason,\n                    str,\n                )\n                and state_reason\n            ):\n                state_reasons.add(\n                    state_reason[:128]\n                )\n\n            termination_reason = container.get(\n                "last_termination_reason"\n            )\n\n            if (\n                isinstance(\n                    termination_reason,\n                    str,\n                )\n                and termination_reason\n            ):\n                termination_reasons.add(\n                    termination_reason[:128]\n                )\n\n        facts = {\n            "temporal_basis": (\n                self.time_policy.temporal_basis(\n                    scope=scope,\n                    probe=probe,\n                )\n            ),\n            "phase": cls_scalar(\n                data.get("phase")\n            ),\n            "ready": cls_scalar(\n                data.get("ready")\n            ),\n            "scheduled": cls_scalar(\n                data.get("scheduled")\n            ),\n            "oom_killed": cls_scalar(\n                data.get("oom_killed")\n            ),\n            "max_restart_count": (\n                max(restart_counts)\n                if restart_counts\n                else None\n            ),\n            "state_reasons": (\n                ",".join(\n                    sorted(\n                        state_reasons\n                    )\n                )\n                if state_reasons\n                else None\n            ),\n            "last_termination_reasons": (\n                ",".join(\n                    sorted(\n                        termination_reasons\n                    )\n                )\n                if termination_reasons\n                else None\n            ),\n        }\n\n        return EvidenceItem(\n            probe=probe,\n            source="kubernetes",\n            success=True,\n            trusted=True,\n            production_signal=True,\n            reliability=1.0,\n            observed_at=observed_at,\n            facts=facts,\n        )\n\n    def _normalize_kubernetes_logs(\n        self,\n        scope: InvestigationScope,\n        probe: InvestigationProbe,\n        result: Any,\n    ) -> EvidenceItem:\n        data, observed_at = (\n            self._validate_tool_evidence(\n                result=result,\n                expected_source="kubernetes",\n            )\n        )\n\n        if (\n            data.get(\n                "previous"\n            )\n            is not True\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes log evidence is not previous-container output"\n            )\n\n        container_value = data.get(\n            "container_name"\n        )\n\n        if not isinstance(\n            container_value,\n            str,\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes log evidence container is invalid"\n            )\n\n        container_name = (\n            container_value\n            .strip()\n        )\n\n        if (\n            not container_name\n            or len(\n                container_name\n            )\n            > 128\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes log evidence container is invalid"\n            )\n\n        line_count = data.get(\n            "line_count"\n        )\n\n        if (\n            not isinstance(\n                line_count,\n                int,\n            )\n            or isinstance(\n                line_count,\n                bool,\n            )\n            or line_count < 0\n            or line_count > self._MAX_LOG_LINES\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes log evidence line count is invalid"\n            )\n\n        truncated = data.get(\n            "truncated"\n        )\n\n        if not isinstance(\n            truncated,\n            bool,\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes log evidence truncation flag is invalid"\n            )\n\n        redaction_count = data.get(\n            "redaction_count"\n        )\n\n        if (\n            not isinstance(\n                redaction_count,\n                int,\n            )\n            or isinstance(\n                redaction_count,\n                bool,\n            )\n            or redaction_count < 0\n            or redaction_count > 10000\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes log evidence redaction count is invalid"\n            )\n\n        excerpt_value = data.get(\n            "excerpt"\n        )\n\n        if not isinstance(\n            excerpt_value,\n            str,\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes log evidence excerpt is invalid"\n            )\n\n        if len(\n            excerpt_value\n        ) > self._MAX_LOG_TOOL_CHARS:\n            raise InvestigationProbeResponseError(\n                "Kubernetes log evidence excerpt is too large"\n            )\n\n        excerpt, local_redactions = (\n            redact_log_excerpt(\n                excerpt_value\n            )\n        )\n\n        redaction_count = (\n            redaction_count\n            + local_redactions\n        )\n\n        evidence_truncated = (\n            len(\n                excerpt\n            )\n            > self._MAX_LOG_EVIDENCE_CHARS\n        )\n\n        if evidence_truncated:\n            excerpt = excerpt[\n                -self._MAX_LOG_EVIDENCE_CHARS:\n            ]\n\n        facts = {\n            "temporal_basis": (\n                self.time_policy.temporal_basis(\n                    scope=scope,\n                    probe=probe,\n                )\n            ),\n            "container_name": container_name,\n            "previous": True,\n            "log_line_count": line_count,\n            "tool_truncated": truncated,\n            "evidence_truncated": (\n                evidence_truncated\n            ),\n            "redaction_count": (\n                redaction_count\n            ),\n            "log_excerpt": (\n                excerpt\n                if excerpt\n                else None\n            ),\n        }\n\n        return EvidenceItem(\n            probe=probe,\n            source="kubernetes",\n            success=True,\n            trusted=True,\n            production_signal=True,\n            reliability=1.0,\n            observed_at=observed_at,\n            facts=facts,\n        )\n\n    def _normalize_kubernetes_config_change(\n        self,\n        scope: InvestigationScope,\n        probe: InvestigationProbe,\n        result: Any,\n    ) -> EvidenceItem:\n        data, observed_at = (\n            self._validate_tool_evidence(\n                result=result,\n                expected_source="kubernetes_change",\n            )\n        )\n\n        if (\n            data.get(\n                "owner_chain_verified"\n            )\n            is not True\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes config change owner chain is untrusted"\n            )\n\n        if (\n            data.get(\n                "workload_kind"\n            )\n            != "Deployment"\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes config change workload kind is unsupported"\n            )\n\n        if (\n            data.get(\n                "secret_content_queried"\n            )\n            is not False\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes config change must not query Secret content"\n            )\n\n        if (\n            data.get(\n                "configmap_content_exposed"\n            )\n            is not False\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes config change must not expose ConfigMap content"\n            )\n\n        metadata_status = data.get(\n            "current_configmap_metadata_status"\n        )\n\n        if metadata_status not in {\n            "complete",\n            "partial",\n            "unavailable",\n            "not_applicable",\n        }:\n            raise InvestigationProbeResponseError(\n                "Kubernetes config metadata status is invalid"\n            )\n\n        facts = {\n            "temporal_basis": (\n                "workload_template_config_change"\n            ),\n            "owner_chain_verified": True,\n            "deployment_name": bounded_change_text(\n                data.get(\n                    "deployment_name"\n                ),\n                required=True,\n            ),\n            "revision_before": bounded_change_int(\n                data.get(\n                    "revision_before"\n                )\n            ),\n            "revision_after": bounded_change_int(\n                data.get(\n                    "revision_after"\n                )\n            ),\n            "configmap_refs_before": bounded_change_text(\n                data.get(\n                    "configmap_refs_before"\n                ),\n                required=False,\n                max_length=1024,\n            ),\n            "configmap_refs_after": bounded_change_text(\n                data.get(\n                    "configmap_refs_after"\n                ),\n                required=False,\n                max_length=1024,\n            ),\n            "configmap_refs_changed": bounded_change_bool(\n                data.get(\n                    "configmap_refs_changed"\n                )\n            ),\n            "configmap_refs_added": bounded_change_text(\n                data.get(\n                    "configmap_refs_added"\n                ),\n                required=False,\n                max_length=1024,\n            ),\n            "configmap_refs_removed": bounded_change_text(\n                data.get(\n                    "configmap_refs_removed"\n                ),\n                required=False,\n                max_length=1024,\n            ),\n            "secret_refs_before": bounded_change_text(\n                data.get(\n                    "secret_refs_before"\n                ),\n                required=False,\n                max_length=1024,\n            ),\n            "secret_refs_after": bounded_change_text(\n                data.get(\n                    "secret_refs_after"\n                ),\n                required=False,\n                max_length=1024,\n            ),\n            "secret_refs_changed": bounded_change_bool(\n                data.get(\n                    "secret_refs_changed"\n                )\n            ),\n            "secret_refs_added": bounded_change_text(\n                data.get(\n                    "secret_refs_added"\n                ),\n                required=False,\n                max_length=1024,\n            ),\n            "secret_refs_removed": bounded_change_text(\n                data.get(\n                    "secret_refs_removed"\n                ),\n                required=False,\n                max_length=1024,\n            ),\n            "config_annotation_keys_before": bounded_change_text(\n                data.get(\n                    "config_annotation_keys_before"\n                ),\n                required=False,\n                max_length=1024,\n            ),\n            "config_annotation_keys_after": bounded_change_text(\n                data.get(\n                    "config_annotation_keys_after"\n                ),\n                required=False,\n                max_length=1024,\n            ),\n            "config_annotation_fingerprint_before": bounded_change_text(\n                data.get(\n                    "config_annotation_fingerprint_before"\n                ),\n                required=False,\n                max_length=128,\n            ),\n            "config_annotation_fingerprint_after": bounded_change_text(\n                data.get(\n                    "config_annotation_fingerprint_after"\n                ),\n                required=False,\n                max_length=128,\n            ),\n            "config_annotation_changed": bounded_change_bool(\n                data.get(\n                    "config_annotation_changed"\n                )\n            ),\n            "current_configmap_metadata_status": (\n                metadata_status\n            ),\n            "current_configmap_metadata_summary": bounded_change_text(\n                data.get(\n                    "current_configmap_metadata_summary"\n                ),\n                required=False,\n                max_length=1536,\n            ),\n            "current_configmap_metadata_error": bounded_change_text(\n                data.get(\n                    "current_configmap_metadata_error"\n                ),\n                required=False,\n                max_length=512,\n            ),\n            "secret_content_queried": False,\n            "configmap_content_exposed": False,\n        }\n\n        return EvidenceItem(\n            probe=probe,\n            source="kubernetes_change",\n            success=True,\n            trusted=True,\n            production_signal=True,\n            reliability=1.0,\n            observed_at=observed_at,\n            facts=facts,\n        )\n\n    def _normalize_kubernetes_change(\n        self,\n        scope: InvestigationScope,\n        probe: InvestigationProbe,\n        result: Any,\n    ) -> EvidenceItem:\n        data, observed_at = (\n            self._validate_tool_evidence(\n                result=result,\n                expected_source="kubernetes_change",\n            )\n        )\n\n        if (\n            data.get(\n                "owner_chain_verified"\n            )\n            is not True\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes change owner chain is untrusted"\n            )\n\n        if (\n            data.get(\n                "workload_kind"\n            )\n            != "Deployment"\n        ):\n            raise InvestigationProbeResponseError(\n                "Kubernetes change workload kind is unsupported"\n            )\n\n        deployment_name = bounded_change_text(\n            data.get(\n                "deployment_name"\n            ),\n            required=True,\n        )\n\n        rollout_started_at = bounded_change_text(\n            data.get(\n                "rollout_started_at"\n            ),\n            required=False,\n        )\n\n        rollout_offset_seconds = None\n        recent_rollout_before_incident = None\n\n        if (\n            rollout_started_at is not None\n            and scope.event_occurred_at\n            is not None\n        ):\n            rollout_time = parse_observed_at(\n                rollout_started_at\n            )\n\n            rollout_offset_seconds = (\n                scope.event_occurred_at\n                .astimezone(\n                    UTC\n                )\n                - rollout_time\n            ).total_seconds()\n\n            recent_rollout_before_incident = (\n                0.0\n                <= rollout_offset_seconds\n                <= 1800.0\n            )\n\n        facts = {\n            "temporal_basis": (\n                "workload_change_history"\n            ),\n            "owner_chain_verified": True,\n            "deployment_name": (\n                deployment_name\n            ),\n            "revision_before": bounded_change_int(\n                data.get(\n                    "revision_before"\n                )\n            ),\n            "revision_after": bounded_change_int(\n                data.get(\n                    "revision_after"\n                )\n            ),\n            "revision_changed": bounded_change_bool(\n                data.get(\n                    "revision_changed"\n                )\n            ),\n            "image_before": bounded_change_text(\n                data.get(\n                    "image_before"\n                ),\n                required=False,\n            ),\n            "image_after": bounded_change_text(\n                data.get(\n                    "image_after"\n                ),\n                required=False,\n            ),\n            "image_changed": bounded_change_bool(\n                data.get(\n                    "image_changed"\n                )\n            ),\n            "rollout_started_at": (\n                rollout_started_at\n            ),\n            "rollout_offset_seconds": (\n                rollout_offset_seconds\n            ),\n            "recent_rollout_before_incident": (\n                recent_rollout_before_incident\n            ),\n            "generation": bounded_change_int(\n                data.get(\n                    "generation"\n                )\n            ),\n            "observed_generation": bounded_change_int(\n                data.get(\n                    "observed_generation"\n                )\n            ),\n            "replicas_desired": bounded_change_int(\n                data.get(\n                    "replicas_desired"\n                )\n            ),\n            "replicas_updated": bounded_change_int(\n                data.get(\n                    "replicas_updated"\n                )\n            ),\n            "replicas_ready": bounded_change_int(\n                data.get(\n                    "replicas_ready"\n                )\n            ),\n            "replicas_available": bounded_change_int(\n                data.get(\n                    "replicas_available"\n                )\n            ),\n            "replicas_unavailable": bounded_change_int(\n                data.get(\n                    "replicas_unavailable"\n                )\n            ),\n            "history_complete": bounded_change_bool(\n                data.get(\n                    "history_complete"\n                )\n            ),\n            "rollout_condition_summary": bounded_change_text(\n                data.get(\n                    "rollout_condition_summary"\n                ),\n                required=False,\n                max_length=512,\n            ),\n            "generation_observed": bounded_change_bool(\n                data.get(\n                    "generation_observed"\n                )\n            ),\n            "rollout_complete": bounded_change_bool(\n                data.get(\n                    "rollout_complete"\n                )\n            ),\n            "rollout_failure_signal": bounded_change_bool(\n                data.get(\n                    "rollout_failure_signal"\n                )\n            ),\n            "rollout_failure_reason": bounded_change_text(\n                data.get(\n                    "rollout_failure_reason"\n                ),\n                required=False,\n            ),\n            "events_status": bounded_change_events_status(\n                data.get(\n                    "events_status"\n                )\n            ),\n            "events_error_code": bounded_change_text(\n                data.get(\n                    "events_error_code"\n                ),\n                required=False,\n            ),\n            "recent_event_count": bounded_change_int(\n                data.get(\n                    "recent_event_count"\n                )\n            ),\n            "recent_warning_count": bounded_change_int(\n                data.get(\n                    "recent_warning_count"\n                )\n            ),\n            "recent_event_reasons": bounded_change_text(\n                data.get(\n                    "recent_event_reasons"\n                ),\n                required=False,\n                max_length=512,\n            ),\n            "recent_event_summary": bounded_change_text(\n                data.get(\n                    "recent_event_summary"\n                ),\n                required=False,\n                max_length=1536,\n            ),\n        }\n\n        return EvidenceItem(\n            probe=probe,\n            source="kubernetes_change",\n            success=True,\n            trusted=True,\n            production_signal=True,\n            reliability=1.0,\n            observed_at=observed_at,\n            facts=facts,\n        )\n\n    def _normalize_prometheus(\n        self,\n        scope: InvestigationScope,\n        probe: InvestigationProbe,\n        result: Any,\n    ) -> EvidenceItem:\n        data, observed_at = (\n            self._validate_tool_evidence(\n                result=result,\n                expected_source="prometheus",\n            )\n        )\n\n        result_type_value = data.get(\n            "resultType"\n        )\n\n        if (\n            not isinstance(\n                result_type_value,\n                str,\n            )\n            or result_type_value\n            not in {\n                "vector",\n                "matrix",\n                "scalar",\n                "string",\n            }\n        ):\n            raise InvestigationProbeResponseError(\n                "Prometheus evidence result type is invalid"\n            )\n\n        result_type = (\n            result_type_value[:64]\n        )\n\n        samples = extract_numeric_samples(\n            result_type=result_type,\n            value=data.get(\n                "result"\n            ),\n        )\n\n        if not samples:\n            raise InvestigationProbeResponseError(\n                "Prometheus evidence contains no numeric samples"\n            )\n\n        try:\n            event_offset_seconds = (\n                self.time_policy.validate_observed_at(\n                    scope=scope,\n                    probe=probe,\n                    observed_at=observed_at,\n                )\n            )\n        except InvestigationEvidenceTimeError as exc:\n            raise InvestigationProbeResponseError(\n                "Prometheus evidence is not "\n                "temporally relevant"\n            ) from exc\n\n        facts = {\n            "temporal_basis": (\n                self.time_policy.temporal_basis(\n                    scope=scope,\n                    probe=probe,\n                )\n            ),\n            "event_offset_seconds": (\n                event_offset_seconds\n            ),\n            "result_type": result_type,\n            "sample_count": len(\n                samples\n            ),\n            "value_sum": sum(\n                samples\n            ),\n            "value_min": min(\n                samples\n            ),\n            "value_max": max(\n                samples\n            ),\n        }\n\n        return EvidenceItem(\n            probe=probe,\n            source="prometheus",\n            success=True,\n            trusted=True,\n            production_signal=True,\n            reliability=1.0,\n            observed_at=observed_at,\n            facts=facts,\n        )\n\n    @classmethod\n    def _validate_tool_evidence(\n        cls,\n        *,\n        result: Any,\n        expected_source: str,\n    ) -> tuple[\n        Mapping[str, Any],\n        datetime,\n    ]:\n        if not isinstance(\n            result,\n            Mapping,\n        ):\n            raise InvestigationProbeResponseError(\n                "Investigation tool result is invalid"\n            )\n\n        if (\n            result.get(\n                "success"\n            )\n            is not True\n        ):\n            raise InvestigationProbeResponseError(\n                "Investigation tool result was unsuccessful"\n            )\n\n        source_value = result.get(\n            "source"\n        )\n\n        if not isinstance(\n            source_value,\n            str,\n        ):\n            raise InvestigationProbeResponseError(\n                "Investigation evidence source is invalid"\n            )\n\n        source = (\n            source_value\n            .strip()\n            .lower()\n        )\n\n        if source != expected_source:\n            raise InvestigationProbeResponseError(\n                "Investigation evidence source is untrusted"\n            )\n\n        mode_value = result.get(\n            "mode"\n        )\n\n        if not isinstance(\n            mode_value,\n            str,\n        ):\n            raise InvestigationProbeResponseError(\n                "Investigation evidence mode is invalid"\n            )\n\n        mode = (\n            mode_value\n            .strip()\n            .lower()\n        )\n\n        if mode != cls._TRUSTED_MODE:\n            raise InvestigationProbeResponseError(\n                "Investigation evidence mode is not read-only"\n            )\n\n        if (\n            result.get(\n                "production_signal"\n            )\n            is not True\n        ):\n            raise InvestigationProbeResponseError(\n                "Investigation evidence is not a production signal"\n            )\n\n        observed_at = parse_observed_at(\n            result.get(\n                "observed_at"\n            )\n        )\n\n        data = result.get(\n            "data"\n        )\n\n        if not isinstance(\n            data,\n            Mapping,\n        ):\n            raise InvestigationProbeResponseError(\n                "Investigation evidence data is invalid"\n            )\n\n        return (\n            data,\n            observed_at,\n        )\n\n    @staticmethod\n    def _escape_label(\n        value: str,\n    ) -> str:\n        return (\n            value\n            .replace(\n                "\\\\",\n                "\\\\\\\\",\n            )\n            .replace(\n                "\\n",\n                "\\\\n",\n            )\n            .replace(\n                "\\r",\n                "\\\\r",\n            )\n            .replace(\n                \'"\',\n                \'\\\\"\',\n            )\n        )\n\n\ndef redact_log_excerpt(\n    value: str,\n) -> tuple[str, int]:\n    """\n    Defense-in-depth redaction at the Investigation trust boundary.\n\n    KubernetesTool redacts before ToolManager tracing. This second pass keeps\n    injected or forged ToolManager responses from placing obvious credentials\n    into bounded InvestigationState.\n    """\n\n    text = value\n    total = 0\n\n    patterns = [\n        (\n            re.compile(\n                (\n                    r"\\beyJ[A-Za-z0-9_-]{10,}"\n                    r"\\.[A-Za-z0-9_-]{10,}"\n                    r"\\.[A-Za-z0-9_-]{10,}\\b"\n                )\n            ),\n            "[REDACTED_JWT]",\n        ),\n        (\n            re.compile(\n                (\n                    r"(?i)\\b("\n                    r"bearer|basic"\n                    r")\\s+"\n                    r"[A-Za-z0-9._~+/=-]{8,}"\n                )\n            ),\n            None,\n        ),\n        (\n            re.compile(\n                (\n                    r"(?i)\\b("\n                    r"password|passwd|pwd|secret|token|"\n                    r"api[_-]?key|access[_-]?key|"\n                    r"client[_-]?secret"\n                    r")\\b"\n                    r"(\\s*[:=]\\s*)"\n                    r"([\\"\']?)"\n                    r"([^\\s,;\\"\']{4,})"\n                    r"([\\"\']?)"\n                )\n            ),\n            None,\n        ),\n    ]\n\n    text, count = patterns[0][0].subn(\n        patterns[0][1],\n        text,\n    )\n\n    total += count\n\n    text, count = patterns[1][0].subn(\n        lambda match: (\n            match.group(1)\n            + " [REDACTED]"\n        ),\n        text,\n    )\n\n    total += count\n\n    text, count = patterns[2][0].subn(\n        lambda match: (\n            match.group(1)\n            + match.group(2)\n            + "[REDACTED]"\n        ),\n        text,\n    )\n\n    total += count\n\n    return (\n        text,\n        total,\n    )\n\n\ndef bounded_change_text(\n    value: Any,\n    *,\n    required: bool,\n    max_length: int = 512,\n) -> str | None:\n    if value is None:\n        if required:\n            raise InvestigationProbeResponseError(\n                "Kubernetes change text fact is missing"\n            )\n        return None\n\n    if not isinstance(\n        value,\n        str,\n    ):\n        raise InvestigationProbeResponseError(\n            "Kubernetes change text fact is invalid"\n        )\n\n    normalized = value.strip()\n\n    if not normalized:\n        if required:\n            raise InvestigationProbeResponseError(\n                "Kubernetes change text fact is missing"\n            )\n        return None\n\n    if len(\n        normalized\n    ) > max_length:\n        raise InvestigationProbeResponseError(\n            "Kubernetes change text fact is too large"\n        )\n\n    return normalized\n\n\ndef bounded_change_int(\n    value: Any,\n) -> int | None:\n    if value is None:\n        return None\n\n    if (\n        isinstance(\n            value,\n            bool,\n        )\n        or not isinstance(\n            value,\n            int,\n        )\n        or value < 0\n        or value > 1_000_000_000\n    ):\n        raise InvestigationProbeResponseError(\n            "Kubernetes change integer fact is invalid"\n        )\n\n    return value\n\n\ndef bounded_change_events_status(\n    value: Any,\n) -> str | None:\n    if value is None:\n        return None\n\n    if value not in {\n        "complete",\n        "partial",\n        "unavailable",\n    }:\n        raise InvestigationProbeResponseError(\n            "Kubernetes event evidence status is invalid"\n        )\n\n    return value\n\n\ndef bounded_change_bool(\n    value: Any,\n) -> bool | None:\n    if value is None:\n        return None\n\n    if not isinstance(\n        value,\n        bool,\n    ):\n        raise InvestigationProbeResponseError(\n            "Kubernetes change boolean fact is invalid"\n        )\n\n    return value\n\n\ndef cls_scalar(\n    value: Any,\n):\n    if (\n        value is None\n        or isinstance(\n            value,\n            (\n                bool,\n                int,\n                float,\n                str,\n            ),\n        )\n    ):\n        return value\n\n    return str(\n        value\n    )[:256]\n\n\ndef parse_observed_at(\n    value: Any,\n) -> datetime:\n    if isinstance(\n        value,\n        datetime,\n    ):\n        parsed = value\n\n    elif isinstance(\n        value,\n        str,\n    ):\n        text = value.strip()\n\n        if not text:\n            raise InvestigationProbeResponseError(\n                "Investigation evidence observed_at is invalid"\n            )\n\n        if text.endswith(\n            "Z"\n        ):\n            text = (\n                f"{text[:-1]}+00:00"\n            )\n\n        try:\n            parsed = datetime.fromisoformat(\n                text\n            )\n        except ValueError as exc:\n            raise InvestigationProbeResponseError(\n                "Investigation evidence observed_at is invalid"\n            ) from exc\n\n    else:\n        raise InvestigationProbeResponseError(\n            "Investigation evidence observed_at is invalid"\n        )\n\n    if parsed.tzinfo is None:\n        raise InvestigationProbeResponseError(\n            "Investigation evidence observed_at must be timezone-aware"\n        )\n\n    return parsed.astimezone(\n        UTC\n    )\n\n\ndef extract_numeric_samples(\n    result_type: str | None,\n    value: Any,\n) -> list[float]:\n    samples: list[float] = []\n\n    def add_sample(\n        sample: Any,\n    ) -> None:\n        if (\n            not isinstance(\n                sample,\n                list,\n            )\n            or len(sample) < 2\n            or len(samples) >= 32\n        ):\n            return\n\n        try:\n            numeric_value = float(\n                sample[1]\n            )\n        except (\n            TypeError,\n            ValueError,\n        ):\n            return\n\n        if not isfinite(\n            numeric_value\n        ):\n            return\n\n        samples.append(\n            numeric_value\n        )\n\n    if result_type in {\n        "scalar",\n        "string",\n    }:\n        add_sample(\n            value\n        )\n\n    elif (\n        result_type == "vector"\n        and isinstance(\n            value,\n            list,\n        )\n    ):\n        for item in value[:32]:\n            if isinstance(\n                item,\n                Mapping,\n            ):\n                add_sample(\n                    item.get(\n                        "value"\n                    )\n                )\n\n    elif (\n        result_type == "matrix"\n        and isinstance(\n            value,\n            list,\n        )\n    ):\n        for item in value[:32]:\n            if not isinstance(\n                item,\n                Mapping,\n            ):\n                continue\n\n            values = item.get(\n                "values"\n            )\n\n            if (\n                isinstance(\n                    values,\n                    list,\n                )\n                and values\n            ):\n                add_sample(\n                    values[-1]\n                )\n\n    return samples\n\n\n__all__ = [\n    "InvestigationProbeError",\n    "InvestigationProbeResponseError",\n    "InvestigationToolUnavailableError",\n    "ReadOnlyInvestigationProbeExecutor",\n    "extract_numeric_samples",\n    "parse_observed_at",\n]\n'
PROBE_TEST_SOURCE = 'from datetime import UTC, datetime\nfrom types import SimpleNamespace\n\nimport pytest\n\nfrom services.agent_runtime.app.investigation.models import (\n    InvestigationProbe,\n    InvestigationScope,\n)\nfrom services.agent_runtime.app.investigation.probes import (\n    ReadOnlyInvestigationProbeExecutor,\n)\n\n\nNOW = datetime(\n    2026,\n    8,\n    9,\n    15,\n    0,\n    tzinfo=UTC,\n)\n\n\nclass FakeToolManager:\n    def __init__(self):\n        self.calls = []\n\n    async def call(\n        self,\n        name,\n        context=None,\n        **kwargs,\n    ):\n        self.calls.append(\n            {\n                "name": name,\n                "context": context,\n                "kwargs": kwargs,\n            }\n        )\n\n        if name == "kubernetes":\n            return {\n                "success": True,\n                "source": "kubernetes",\n                "mode": "read_only",\n                "production_signal": True,\n                "observed_at": NOW.isoformat(),\n                "data": {\n                    "uid": "must-not-be-retained",\n                    "resource_version": "secret-version",\n                    "phase": "Running",\n                    "ready": False,\n                    "scheduled": True,\n                    "oom_killed": True,\n                    "containers": [\n                        {\n                            "restart_count": 7,\n                            "state_reason": (\n                                "CrashLoopBackOff"\n                            ),\n                            "last_termination_reason": (\n                                "OOMKilled"\n                            ),\n                        }\n                    ],\n                },\n            }\n\n        return {\n            "success": True,\n            "source": "prometheus",\n            "mode": "read_only",\n            "production_signal": True,\n            "observed_at": NOW.isoformat(),\n            "query": "must-not-be-retained",\n            "data": {\n                "resultType": "vector",\n                "result": [\n                    {\n                        "metric": {\n                            "pod": "payment-api"\n                        },\n                        "value": [\n                            1786300000,\n                            "123.5",\n                        ],\n                    }\n                ],\n            },\n        }\n\n\ndef scope() -> InvestigationScope:\n    return InvestigationScope(\n        alert_name="PodOOMKilled",\n        alert_message="Pod restarted",\n        resource=\'payment"api\',\n        namespace="team\\\\blue",\n        cluster="prod\\nwest",\n    )\n\n\n@pytest.mark.asyncio\nasync def test_kubernetes_probe_has_fixed_read_only_call():\n    tools = FakeToolManager()\n    context = SimpleNamespace(\n        tools=tools\n    )\n    executor = ReadOnlyInvestigationProbeExecutor()\n\n    evidence = await executor.collect(\n        context,\n        scope(),\n        InvestigationProbe.KUBERNETES_POD_STATE,\n    )\n\n    assert tools.calls == [\n        {\n            "name": "kubernetes",\n            "context": context,\n            "kwargs": {\n                "action": "describe",\n                "resource": "pod",\n                "target": \'payment"api\',\n                "namespace": "team\\\\blue",\n                "cluster": "prod\\nwest",\n            },\n        }\n    ]\n    assert evidence.trusted is True\n    assert evidence.facts["oom_killed"] is True\n    assert evidence.facts["max_restart_count"] == 7\n\n    payload = evidence.model_dump(\n        mode="json"\n    )\n    serialized = str(payload)\n\n    assert "must-not-be-retained" not in serialized\n    assert "secret-version" not in serialized\n\n\n@pytest.mark.asyncio\n@pytest.mark.parametrize(\n    ("probe", "metric"),\n    [\n        (\n            InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,\n            "container_memory_working_set_bytes",\n        ),\n        (\n            InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,\n            "kube_pod_container_resource_limits",\n        ),\n        (\n            InvestigationProbe.PROMETHEUS_RESTART_COUNT,\n            "kube_pod_container_status_restarts_total",\n        ),\n    ],\n)\nasync def test_prometheus_probe_uses_bounded_template(\n    probe,\n    metric,\n):\n    tools = FakeToolManager()\n    context = SimpleNamespace(\n        tools=tools\n    )\n    executor = ReadOnlyInvestigationProbeExecutor()\n\n    evidence = await executor.collect(\n        context,\n        scope(),\n        probe,\n    )\n\n    assert len(tools.calls) == 1\n    call = tools.calls[0]\n    assert call["name"] == "prometheus"\n    assert set(call["kwargs"]) == {\n        "query",\n        "cluster",\n    }\n    assert (\n        call["kwargs"][\n            "cluster"\n        ]\n        == "prod\\nwest"\n    )\n\n    query = call["kwargs"]["query"]\n    assert metric in query\n    assert \'pod="payment\\\\"api"\' in query\n    assert \'namespace="team\\\\\\\\blue"\' in query\n    assert \'cluster="prod\\\\nwest"\' in query\n    assert "\\n" not in query\n\n    assert evidence.source == "prometheus"\n    assert evidence.facts["sample_count"] == 1\n    assert evidence.facts["value_sum"] == 123.5\n    assert "query" not in evidence.model_dump()\n\n\n@pytest.mark.asyncio\nasync def test_probe_requires_tool_manager():\n    executor = ReadOnlyInvestigationProbeExecutor()\n\n    with pytest.raises(\n        RuntimeError,\n        match="tools are unavailable",\n    ):\n        await executor.collect(\n            SimpleNamespace(tools=None),\n            scope(),\n            InvestigationProbe.KUBERNETES_POD_STATE,\n        )\n'
TEST_SOURCE = 'from __future__ import annotations\n\nfrom datetime import UTC, datetime\nfrom pathlib import Path\nfrom types import SimpleNamespace\nfrom typing import Any\n\nimport pytest\n\nimport services.agent_runtime.app.runtime.runtime as runtime_module\nimport services.agent_runtime.app.tools.prometheus.router as router_module\n\nfrom common.config.settings import (\n    AuthenticationConfig,\n)\n\nfrom services.agent_runtime.app.investigation.models import (\n    InvestigationProbe,\n    InvestigationScope,\n)\nfrom services.agent_runtime.app.investigation.probes import (\n    ReadOnlyInvestigationProbeExecutor,\n)\nfrom services.agent_runtime.app.investigation.settings import (\n    InvestigationSettings,\n)\nfrom services.agent_runtime.app.security.factory import (\n    create_authentication_service,\n)\nfrom services.agent_runtime.app.tools.factory import (\n    create_tool_manager,\n)\nfrom services.agent_runtime.app.tools.manager import (\n    ToolManager,\n)\nfrom services.agent_runtime.app.tools.prometheus.router import (\n    MultiClusterPrometheusToolRouter,\n    PrometheusClusterRegistry,\n    PrometheusClusterRoutingError,\n)\nfrom services.agent_runtime.app.tools.prometheus.tool import (\n    PrometheusTool,\n)\nfrom services.agent_runtime.app.tools.registry import (\n    ToolRegistry,\n)\n\n\nNOW = datetime(\n    2026,\n    8,\n    11,\n    4,\n    30,\n    tzinfo=UTC,\n)\n\n\nclass RecordingPrometheusTool(\n    PrometheusTool\n):\n    def __init__(\n        self,\n        endpoint_name: str,\n    ) -> None:\n        super().__init__(\n            base_url=(\n                f"https://{endpoint_name}.prometheus.test"\n            ),\n            verify_tls=True,\n            allow_mock_fallback=False,\n        )\n\n        self.endpoint_name = (\n            endpoint_name\n        )\n\n        self.calls: list[\n            dict[str, Any]\n        ] = []\n\n    async def execute(\n        self,\n        query: str,\n        time=None,\n        **kwargs: Any,\n    ) -> dict[str, Any]:\n        call = {\n            "query": query,\n        }\n\n        if time is not None:\n            call[\n                "time"\n            ] = time\n\n        call.update(\n            kwargs\n        )\n\n        self.calls.append(\n            call\n        )\n\n        return {\n            "success": True,\n            "source": "prometheus",\n            "mode": "read_only",\n            "production_signal": True,\n            "observed_at": (\n                NOW.isoformat()\n            ),\n            "query": query,\n            "data": {\n                "resultType": "vector",\n                "result": [\n                    {\n                        "metric": {\n                            "endpoint": (\n                                self.endpoint_name\n                            ),\n                        },\n                        "value": [\n                            NOW.timestamp(),\n                            "7",\n                        ],\n                    }\n                ],\n            },\n            "warnings": [],\n        }\n\n\ndef endpoints():\n    sg = RecordingPrometheusTool(\n        "sg"\n    )\n\n    us = RecordingPrometheusTool(\n        "us"\n    )\n\n    return sg, us\n\n\ndef test_registry_is_immutable_exact_cluster_mapping():\n    sg, us = endpoints()\n\n    registry = PrometheusClusterRegistry(\n        {\n            "prod-sg-17": sg,\n            "prod-us-03": us,\n        }\n    )\n\n    assert registry.count == 2\n\n    assert registry.cluster_names == (\n        "prod-sg-17",\n        "prod-us-03",\n    )\n\n    assert (\n        registry.resolve(\n            "prod-sg-17"\n        )\n        == (\n            "prod-sg-17",\n            sg,\n        )\n    )\n\n    assert not hasattr(\n        registry,\n        "register",\n    )\n\n\ndef test_registry_allows_shared_central_endpoint_for_multiple_clusters():\n    central = RecordingPrometheusTool(\n        "central"\n    )\n\n    registry = PrometheusClusterRegistry(\n        {\n            "prod-sg-17": central,\n            "prod-us-03": central,\n        }\n    )\n\n    assert (\n        registry.resolve(\n            "prod-sg-17"\n        )[\n            1\n        ]\n        is central\n    )\n\n    assert (\n        registry.resolve(\n            "prod-us-03"\n        )[\n            1\n        ]\n        is central\n    )\n\n\n@pytest.mark.parametrize(\n    "tool",\n    [\n        PrometheusTool(\n            base_url=None,\n            allow_mock_fallback=False,\n        ),\n        PrometheusTool(\n            base_url=(\n                "https://mock-fallback.prometheus.test"\n            ),\n            allow_mock_fallback=True,\n        ),\n        PrometheusTool(\n            base_url=(\n                "https://insecure.prometheus.test"\n            ),\n            verify_tls=False,\n            allow_mock_fallback=False,\n        ),\n    ],\n)\ndef test_registry_rejects_unsafe_live_endpoint_bindings(\n    tool,\n):\n    with pytest.raises(\n        PrometheusClusterRoutingError,\n    ):\n        PrometheusClusterRegistry(\n            {\n                "prod-sg-17": tool,\n            }\n        )\n\n\n@pytest.mark.asyncio\nasync def test_router_selects_exact_requested_metrics_endpoint():\n    sg, us = endpoints()\n\n    router = (\n        MultiClusterPrometheusToolRouter(\n            PrometheusClusterRegistry(\n                {\n                    "prod-sg-17": sg,\n                    "prod-us-03": us,\n                }\n            )\n        )\n    )\n\n    result = await router.execute(\n        cluster="prod-us-03",\n        query=(\n            \'up{cluster="prod-us-03"}\'\n        ),\n    )\n\n    assert result[\n        "cluster"\n    ] == "prod-us-03"\n\n    assert sg.calls == []\n\n    assert us.calls == [\n        {\n            "query": (\n                \'up{cluster="prod-us-03"}\'\n            ),\n        }\n    ]\n\n\n@pytest.mark.asyncio\nasync def test_unknown_cluster_fails_before_any_prometheus_call():\n    sg, us = endpoints()\n\n    router = (\n        MultiClusterPrometheusToolRouter(\n            PrometheusClusterRegistry(\n                {\n                    "prod-sg-17": sg,\n                    "prod-us-03": us,\n                }\n            )\n        )\n    )\n\n    with pytest.raises(\n        PrometheusClusterRoutingError,\n        match="not registered",\n    ):\n        await router.execute(\n            cluster="prod-eu-05",\n            query="up",\n        )\n\n    assert sg.calls == []\n    assert us.calls == []\n\n\n@pytest.mark.asyncio\nasync def test_multiple_metrics_clusters_require_explicit_cluster():\n    sg, us = endpoints()\n\n    router = (\n        MultiClusterPrometheusToolRouter(\n            PrometheusClusterRegistry(\n                {\n                    "prod-sg-17": sg,\n                    "prod-us-03": us,\n                }\n            )\n        )\n    )\n\n    with pytest.raises(\n        PrometheusClusterRoutingError,\n        match="cluster is required",\n    ):\n        await router.execute(\n            query="up",\n        )\n\n    assert sg.calls == []\n    assert us.calls == []\n\n\n@pytest.mark.asyncio\nasync def test_single_metrics_cluster_keeps_missing_cluster_compatibility():\n    sg, _ = endpoints()\n\n    router = (\n        MultiClusterPrometheusToolRouter(\n            PrometheusClusterRegistry(\n                {\n                    "prod-sg-17": sg,\n                }\n            )\n        )\n    )\n\n    result = await router.execute(\n        query="up",\n    )\n\n    assert result[\n        "cluster"\n    ] == "prod-sg-17"\n\n    assert len(\n        sg.calls\n    ) == 1\n\n\n@pytest.mark.asyncio\nasync def test_probe_cluster_routes_to_matching_prometheus_endpoint():\n    sg, us = endpoints()\n\n    registry = ToolRegistry()\n\n    registry.register(\n        MultiClusterPrometheusToolRouter(\n            PrometheusClusterRegistry(\n                {\n                    "prod-sg-17": sg,\n                    "prod-us-03": us,\n                }\n            )\n        )\n    )\n\n    context = SimpleNamespace(\n        tools=ToolManager(\n            registry\n        ),\n        trace=None,\n    )\n\n    scope = InvestigationScope(\n        alert_name="PodRestartHigh",\n        alert_message=(\n            "device gateway restart rate is elevated"\n        ),\n        event_occurred_at=NOW,\n        resource=(\n            "device-gateway-xyz789"\n        ),\n        namespace="fleet-edge",\n        cluster="prod-us-03",\n    )\n\n    evidence = await (\n        ReadOnlyInvestigationProbeExecutor()\n        .collect(\n            context,\n            scope,\n            (\n                InvestigationProbe\n                .PROMETHEUS_RESTART_COUNT\n            ),\n        )\n    )\n\n    assert sg.calls == []\n\n    assert len(\n        us.calls\n    ) == 1\n\n    assert (\n        \'cluster="prod-us-03"\'\n        in us.calls[\n            0\n        ][\n            "query"\n        ]\n    )\n\n    assert evidence.source == (\n        "prometheus"\n    )\n\n    assert evidence.trusted is True\n\n\ndef test_default_tool_factory_preserves_legacy_prometheus_singleton():\n    manager = create_tool_manager()\n\n    prometheus = (\n        manager.registry.get(\n            "prometheus"\n        )\n    )\n\n    assert isinstance(\n        prometheus,\n        PrometheusTool,\n    )\n\n    assert not isinstance(\n        prometheus,\n        MultiClusterPrometheusToolRouter,\n    )\n\n\ndef test_explicit_prometheus_registry_switches_factory_to_router():\n    sg, us = endpoints()\n\n    clusters = PrometheusClusterRegistry(\n        {\n            "prod-sg-17": sg,\n            "prod-us-03": us,\n        }\n    )\n\n    manager = create_tool_manager(\n        prometheus_cluster_registry=(\n            clusters\n        )\n    )\n\n    prometheus = (\n        manager.registry.get(\n            "prometheus"\n        )\n    )\n\n    assert isinstance(\n        prometheus,\n        MultiClusterPrometheusToolRouter,\n    )\n\n    assert (\n        prometheus.clusters\n        is clusters\n    )\n\n\ndef test_runtime_rejects_invalid_prometheus_registry_before_factories(\n    monkeypatch,\n):\n    authentication_calls = 0\n\n    def forbidden_authentication():\n        nonlocal authentication_calls\n        authentication_calls += 1\n        raise AssertionError(\n            "authentication factory must not run"\n        )\n\n    monkeypatch.setattr(\n        runtime_module,\n        "create_authentication_service",\n        forbidden_authentication,\n    )\n\n    with pytest.raises(\n        TypeError,\n        match="Prometheus cluster registry",\n    ):\n        runtime_module.AgentRuntime(\n            prometheus_cluster_registry=object(),\n        )\n\n    assert authentication_calls == 0\n\n\ndef test_runtime_passes_explicit_prometheus_registry_only_when_opted_in(\n    monkeypatch,\n    tmp_path,\n):\n    monkeypatch.chdir(\n        tmp_path\n    )\n\n    sg, us = endpoints()\n\n    clusters = PrometheusClusterRegistry(\n        {\n            "prod-sg-17": sg,\n            "prod-us-03": us,\n        }\n    )\n\n    captured = []\n\n    def routed_manager_factory(\n        *,\n        prometheus_cluster_registry,\n    ):\n        captured.append(\n            prometheus_cluster_registry\n        )\n\n        return ToolManager(\n            ToolRegistry()\n        )\n\n    monkeypatch.setattr(\n        runtime_module,\n        "create_tool_manager",\n        routed_manager_factory,\n    )\n\n    monkeypatch.setattr(\n        runtime_module,\n        "create_kubernetes_cluster_registry",\n        lambda: None,\n    )\n\n    monkeypatch.setattr(\n        runtime_module,\n        "create_kubernetes_preflight_resolver",\n        lambda: None,\n    )\n\n    monkeypatch.setattr(\n        runtime_module,\n        "create_kubernetes_production_executor",\n        lambda **_: None,\n    )\n\n    monkeypatch.setattr(\n        runtime_module,\n        "create_production_pilot_live_readiness_probe",\n        lambda: None,\n    )\n\n    runtime = runtime_module.AgentRuntime(\n        authentication_service=(\n            create_authentication_service(\n                AuthenticationConfig()\n            )\n        ),\n        prometheus_cluster_registry=(\n            clusters\n        ),\n        investigation_settings=(\n            InvestigationSettings()\n        ),\n    )\n\n    assert captured == [\n        clusters\n    ]\n\n    assert (\n        runtime.prometheus_cluster_registry\n        is clusters\n    )\n\n    assert isinstance(\n        runtime.tools,\n        ToolManager,\n    )\n\n\ndef test_prometheus_router_module_has_no_write_authority():\n    source = router_module.__file__\n\n    assert source is not None\n\n    text = Path(\n        source\n    ).read_text(\n        encoding="utf-8"\n    )\n\n    forbidden = [\n        "ActionRuntime",\n        "ApprovalService",\n        "VerificationRuntime",\n        "KubernetesProductionExecutor",\n        ".post(",\n        ".patch(",\n        ".put(",\n        ".delete(",\n    ]\n\n    assert [\n        item\n        for item in forbidden\n        if item in text\n    ] == []\n'


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
                f"{relative} changed after the reviewed Prometheus snapshot. "
                f"expected_sha256={expected} actual_sha256={actual}. "
                "Refusing stale Multi-Cluster Prometheus Router installation."
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

    router_file = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "tools"
        / "prometheus"
        / "router.py"
    )

    factory_file = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "tools"
        / "factory.py"
    )

    runtime_file = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "runtime"
        / "runtime.py"
    )

    probes_file = (
        root
        / "services"
        / "agent_runtime"
        / "app"
        / "investigation"
        / "probes.py"
    )

    probe_test_file = (
        root
        / "services"
        / "agent_runtime"
        / "tests"
        / "test_investigation_probes.py"
    )

    test_file = (
        root
        / "services"
        / "agent_runtime"
        / "tests"
        / "test_multi_cluster_prometheus_router.py"
    )

    sources = {
        router_file: ROUTER_SOURCE,
        factory_file: FACTORY_SOURCE,
        runtime_file: RUNTIME_SOURCE,
        probes_file: PROBES_SOURCE,
        probe_test_file: PROBE_TEST_SOURCE,
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
        "Multi-Cluster Prometheus Read Router v1.1",
        f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
        "",
        "Architecture:",
        "- ToolRegistry remains name -> singleton Tool",
        "- ToolManager call protocol remains unchanged",
        "- multi-cluster mode registers one prometheus routing Tool under the existing name",
        "- PrometheusClusterRegistry maps exact Incident cluster -> PrometheusTool",
        "- multiple clusters may intentionally share one Tool/endpoint for central Thanos/Mimir/Prometheus",
        "",
        "Scope contract:",
        "- Investigation Probe continues to include cluster in bounded PromQL labels",
        "- Probe now also passes scope.cluster as an explicit Tool routing argument",
        "- unknown cluster fails before a Prometheus child Tool call",
        "- multiple configured metric clusters require explicit cluster",
        "",
        "Compatibility:",
        "- default create_tool_manager() still registers the legacy singleton PrometheusTool",
        "- explicit PrometheusClusterRegistry opt-in enables the router",
        "- Runtime accepts explicit registry injection but has no new config loader in v1",
        "- existing Kubernetes multi-cluster Router/Connection Config remain unchanged",
        "",
        "Registry safety:",
        "- live base_url required",
        "- mock fallback must be disabled",
        "- TLS verification must be enabled",
        "- bounded maximum 64 explicit cluster mappings",
        "",
        "Authority:",
        "- read-only metrics routing only",
        "- no Action / Approval / Verification Runtime write authority",
        "- no Kubernetes production write path change",
        "",
        "Installer sends no real Kubernetes/Prometheus/LLM request.",
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

        if router_file.exists():
            raise RuntimeError(
                "Prometheus router.py already exists; refusing to overwrite an unreviewed router"
            )

        if test_file.exists():
            raise RuntimeError(
                "Prometheus router test already exists; refusing to overwrite an unreviewed test"
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
                "Multi-Cluster Prometheus Router syntax failed"
            )

        focused = run_command(
            root=root,
            name="Multi-Cluster Prometheus Router focused suite",
            command=[
                "uv",
                "run",
                "pytest",
                (
                    "services/agent_runtime/tests/"
                    "test_multi_cluster_prometheus_router.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_prometheus_tool.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_probes.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_production_scope_integrity.py"
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
                "Multi-Cluster Prometheus Router focused tests failed"
            )

        kubernetes_compat = run_command(
            root=root,
            name="Kubernetes multi-cluster compatibility suite",
            command=[
                "uv",
                "run",
                "pytest",
                (
                    "services/agent_runtime/tests/"
                    "test_multi_cluster_kubernetes_router.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_multi_cluster_connection_config.py"
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
            kubernetes_compat,
        )

        if kubernetes_compat.returncode != 0:
            raise RuntimeError(
                "Prometheus Router Kubernetes compatibility failed"
            )

        investigation_compat = run_command(
            root=root,
            name="Investigation / Runtime compatibility suite",
            command=[
                "uv",
                "run",
                "pytest",
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_production_tool_contract.py"
                ),
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
                    "test_verification_collector.py"
                ),
                "-q",
            ],
        )

        add_command(
            report,
            investigation_compat,
        )

        if investigation_compat.returncode != 0:
            raise RuntimeError(
                "Multi-Cluster Prometheus Router Investigation compatibility failed"
            )

        preflight = run_command(
            root=root,
            name="Prometheus routing architecture preflight",
            command=[
                "uv",
                "run",
                "python",
                "-c",
                (
                    "from pathlib import Path; "
                    "r=Path(r'services/agent_runtime/app/tools/prometheus/router.py').read_text(encoding='utf-8'); "
                    "f=Path(r'services/agent_runtime/app/tools/factory.py').read_text(encoding='utf-8'); "
                    "rt=Path(r'services/agent_runtime/app/runtime/runtime.py').read_text(encoding='utf-8'); "
                    "p=Path(r'services/agent_runtime/app/investigation/probes.py').read_text(encoding='utf-8'); "
                    "m=Path(r'services/agent_runtime/app/tools/manager.py').read_text(encoding='utf-8'); "
                    "g=Path(r'services/agent_runtime/app/tools/registry.py').read_text(encoding='utf-8'); "
                    "print('prom_registry='+str('class PrometheusClusterRegistry' in r)); "
                    "print('prom_router='+str('class MultiClusterPrometheusToolRouter' in r)); "
                    "print('factory_opt_in='+str('prometheus_cluster_registry' in f)); "
                    "print('runtime_opt_in='+str('prometheus_cluster_registry' in rt)); "
                    "print('probe_cluster_routing='+str('call_arguments[\\n                \"cluster\"\\n            ] = scope.cluster' in p)); "
                    "print('manager_unchanged='+str('registry.get' in m)); "
                    "print('registry_singleton_contract='+str('dict[str, BaseTool]' in g)); "
                    "assert 'class PrometheusClusterRegistry' in r; "
                    "assert 'class MultiClusterPrometheusToolRouter' in r; "
                    "assert 'prometheus_cluster_registry' in f; "
                    "assert 'prometheus_cluster_registry' in rt; "
                    "assert 'call_arguments[\\n                \"cluster\"\\n            ] = scope.cluster' in p; "
                    "assert 'registry.get' in m; "
                    "assert 'dict[str, BaseTool]' in g"
                ),
            ],
        )

        add_command(
            report,
            preflight,
        )

        if preflight.returncode != 0:
            raise RuntimeError(
                "Multi-Cluster Prometheus Router architecture preflight failed"
            )

        authority = run_command(
            root=root,
            name="Prometheus read-only routing authority boundary",
            command=[
                "uv",
                "run",
                "python",
                "-c",
                (
                    "from pathlib import Path; "
                    "r=Path(r'services/agent_runtime/app/tools/prometheus/router.py').read_text(encoding='utf-8'); "
                    "bad=[x for x in ['ActionRuntime','ApprovalService','VerificationRuntime','KubernetesProductionExecutor','.post(','.patch(','.put(','.delete('] if x in r]; "
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
                "Multi-Cluster Prometheus Router authority boundary failed"
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
                "Multi-Cluster Prometheus Read Router v1.1 is installed.",
                "",
                "Current metrics routing capability:",
                "- explicit PrometheusClusterRegistry can map Incident clusters to exact live PrometheusTool endpoints",
                "- multiple clusters can share a central metrics endpoint explicitly",
                "- Investigation passes scope.cluster to metrics routing in addition to the PromQL cluster label",
                "- unknown/ambiguous cluster routing fails closed before child query execution",
                "",
                "Still intentionally not implemented:",
                "- Settings.connections.prometheus_read",
                "- env/file credential-reference factory for Prometheus",
                "- CA-file connection factory support",
                "- dynamic endpoint discovery/reload",
                "",
                "Next recommended step:",
                "- Multi-Cluster Prometheus Connection Config / Registry Factory v1, disabled by default.",
            ]
        )

        write_text(
            after,
            "\n".join(
                report
            )
            + "\n",
        )

        print(
            "=" * 72
        )
        print(
            "MULTI-CLUSTER PROMETHEUS READ ROUTER V1.1 PASSED"
        )
        print(
            "=" * 72
        )
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
                    "Multi-Cluster Prometheus Read Router v1.1 FAILED",
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

        print(
            "=" * 72
        )
        print(
            "MULTI-CLUSTER PROMETHEUS READ ROUTER V1.1 FAILED"
        )
        print(
            "=" * 72
        )
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
