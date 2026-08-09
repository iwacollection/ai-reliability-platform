from __future__ import annotations

import hashlib
import shutil
import subprocess
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


VERSION = "multi-cluster-kubernetes-tool-router-v1"

AFTER_NAME = (
    "multi_cluster_kubernetes_tool_router_v1_after.txt"
)

ERROR_NAME = (
    "multi_cluster_kubernetes_tool_router_v1_error.txt"
)

EXPECTED_HASHES = {'services/agent_runtime/app/tools/factory.py': 'a7c7db1533331a693cd7021c5c9480f0b0660c472a8fe65861561d8ddbffae64', 'services/agent_runtime/app/runtime/runtime.py': '23ff341e043b73196911f0aa6ad3562bed43f87bcbcbf3fe95fcbe1b12c95d0c'}

ROUTER_SOURCE = 'from __future__ import annotations\n\nfrom collections.abc import Iterable\nfrom types import MappingProxyType\nfrom typing import Any\n\nfrom services.agent_runtime.app.tools.base import (\n    BaseTool,\n)\nfrom services.agent_runtime.app.tools.kubernetes.change_tool import (\n    KubernetesChangeTool,\n)\nfrom services.agent_runtime.app.tools.kubernetes.tool import (\n    KubernetesTool,\n)\n\n\nclass KubernetesClusterRoutingError(\n    RuntimeError\n):\n    """\n    The read-only Kubernetes cluster route cannot be resolved safely.\n    """\n\n\nclass KubernetesClusterRegistry:\n    """\n    Immutable startup registry of cluster-bound read-only Kubernetes tools.\n\n    This registry stores only already-constructed KubernetesTool objects.\n    It does not parse credentials, expose tokens, mutate Runtime scope, or\n    perform network calls.\n\n    Multi-cluster registration is intentionally startup-only. Dynamic cluster\n    discovery and credential rotation belong to a future configuration layer.\n    """\n\n    _MAX_CLUSTERS = 64\n    _MAX_CLUSTER_NAME_LENGTH = 253\n\n    def __init__(\n        self,\n        tools: Iterable[\n            KubernetesTool\n        ],\n    ) -> None:\n        if isinstance(\n            tools,\n            (\n                str,\n                bytes,\n                KubernetesTool,\n            ),\n        ):\n            raise TypeError(\n                "Kubernetes cluster registry requires a collection of KubernetesTool objects"\n            )\n\n        try:\n            items = tuple(\n                tools\n            )\n        except TypeError:\n            raise TypeError(\n                "Kubernetes cluster registry requires an iterable"\n            ) from None\n\n        if len(\n            items\n        ) > self._MAX_CLUSTERS:\n            raise KubernetesClusterRoutingError(\n                "Kubernetes cluster registry exceeds the bounded cluster limit"\n            )\n\n        mapping: dict[\n            str,\n            KubernetesTool,\n        ] = {}\n\n        for tool in items:\n            if not isinstance(\n                tool,\n                KubernetesTool,\n            ):\n                raise TypeError(\n                    "Kubernetes cluster registry accepts KubernetesTool only"\n                )\n\n            cluster = self._tool_cluster_name(\n                tool\n            )\n\n            if tool.api_url is None:\n                raise KubernetesClusterRoutingError(\n                    "Registered Kubernetes cluster is not configured with an API endpoint"\n                )\n\n            if cluster in mapping:\n                raise KubernetesClusterRoutingError(\n                    "Duplicate Kubernetes cluster registration is not allowed"\n                )\n\n            mapping[\n                cluster\n            ] = tool\n\n        self._tools = MappingProxyType(\n            mapping\n        )\n\n    @property\n    def count(\n        self,\n    ) -> int:\n        return len(\n            self._tools\n        )\n\n    @property\n    def cluster_names(\n        self,\n    ) -> tuple[str, ...]:\n        return tuple(\n            sorted(\n                self._tools\n            )\n        )\n\n    def resolve(\n        self,\n        cluster: str | None,\n    ) -> KubernetesTool:\n        requested = self._requested_cluster(\n            cluster\n        )\n\n        try:\n            return self._tools[\n                requested\n            ]\n        except KeyError:\n            raise KubernetesClusterRoutingError(\n                "Requested Kubernetes cluster is not registered"\n            ) from None\n\n    def _requested_cluster(\n        self,\n        cluster: str | None,\n    ) -> str:\n        if cluster is None:\n            if self.count == 1:\n                return next(\n                    iter(\n                        self._tools\n                    )\n                )\n\n            if self.count == 0:\n                raise KubernetesClusterRoutingError(\n                    "No Kubernetes clusters are registered"\n                )\n\n            raise KubernetesClusterRoutingError(\n                "Kubernetes cluster is required when multiple clusters are registered"\n            )\n\n        if (\n            not isinstance(\n                cluster,\n                str,\n            )\n            or not cluster\n            or cluster != cluster.strip()\n            or len(\n                cluster\n            )\n            > self._MAX_CLUSTER_NAME_LENGTH\n            or "\\x00" in cluster\n        ):\n            raise KubernetesClusterRoutingError(\n                "Requested Kubernetes cluster is invalid"\n            )\n\n        return cluster\n\n    @classmethod\n    def _tool_cluster_name(\n        cls,\n        tool: KubernetesTool,\n    ) -> str:\n        value = tool.cluster_name\n\n        if (\n            not isinstance(\n                value,\n                str,\n            )\n            or not value\n            or value != value.strip()\n            or len(\n                value\n            )\n            > cls._MAX_CLUSTER_NAME_LENGTH\n            or "\\x00" in value\n        ):\n            raise KubernetesClusterRoutingError(\n                "Registered KubernetesTool requires an exact cluster_name"\n            )\n\n        return value\n\n\nclass MultiClusterKubernetesToolRouter(\n    BaseTool\n):\n    """\n    Route the existing read-only `kubernetes` contract by exact cluster.\n\n    The caller still supplies the original Tool arguments. The only routing\n    authority added here is exact selection of an already-bound KubernetesTool.\n    """\n\n    def __init__(\n        self,\n        clusters: KubernetesClusterRegistry,\n    ) -> None:\n        if not isinstance(\n            clusters,\n            KubernetesClusterRegistry,\n        ):\n            raise TypeError(\n                "Multi-cluster Kubernetes router requires KubernetesClusterRegistry"\n            )\n\n        if clusters.count == 0:\n            raise KubernetesClusterRoutingError(\n                "Multi-cluster Kubernetes router requires at least one cluster"\n            )\n\n        self.clusters = clusters\n\n    @property\n    def name(\n        self,\n    ) -> str:\n        return "kubernetes"\n\n    @property\n    def is_available(\n        self,\n    ) -> bool:\n        return self.clusters.count > 0\n\n    async def execute(\n        self,\n        *,\n        cluster: str | None = None,\n        **kwargs: Any,\n    ) -> dict[str, Any]:\n        tool = self.clusters.resolve(\n            cluster\n        )\n\n        selected_cluster = (\n            tool.cluster_name\n        )\n\n        assert isinstance(\n            selected_cluster,\n            str,\n        )\n\n        return await tool.execute(\n            cluster=selected_cluster,\n            **kwargs,\n        )\n\n\nclass MultiClusterKubernetesChangeToolRouter(\n    BaseTool\n):\n    """\n    Route workload/config change evidence through the same selected cluster.\n\n    Each KubernetesChangeTool is constructed from the exact KubernetesTool\n    already registered for that cluster, so Pod/ReplicaSet/Deployment reads\n    cannot silently switch Kubernetes clients.\n    """\n\n    def __init__(\n        self,\n        clusters: KubernetesClusterRegistry,\n    ) -> None:\n        if not isinstance(\n            clusters,\n            KubernetesClusterRegistry,\n        ):\n            raise TypeError(\n                "Multi-cluster Kubernetes change router requires KubernetesClusterRegistry"\n            )\n\n        if clusters.count == 0:\n            raise KubernetesClusterRoutingError(\n                "Multi-cluster Kubernetes change router requires at least one cluster"\n            )\n\n        self.clusters = clusters\n\n        self._change_tools = {\n            name: KubernetesChangeTool(\n                clusters.resolve(\n                    name\n                )\n            )\n            for name in clusters.cluster_names\n        }\n\n    @property\n    def name(\n        self,\n    ) -> str:\n        return "kubernetes_change"\n\n    @property\n    def is_available(\n        self,\n    ) -> bool:\n        return self.clusters.count > 0\n\n    async def execute(\n        self,\n        *,\n        cluster: str | None = None,\n        **kwargs: Any,\n    ) -> dict[str, Any]:\n        kubernetes = (\n            self.clusters.resolve(\n                cluster\n            )\n        )\n\n        selected_cluster = (\n            kubernetes.cluster_name\n        )\n\n        assert isinstance(\n            selected_cluster,\n            str,\n        )\n\n        change_tool = (\n            self._change_tools[\n                selected_cluster\n            ]\n        )\n\n        if (\n            change_tool.kubernetes\n            is not kubernetes\n        ):\n            raise KubernetesClusterRoutingError(\n                "Kubernetes change router lost cluster-client identity"\n            )\n\n        return await change_tool.execute(\n            cluster=selected_cluster,\n            **kwargs,\n        )\n\n\n__all__ = [\n    "KubernetesClusterRegistry",\n    "KubernetesClusterRoutingError",\n    "MultiClusterKubernetesChangeToolRouter",\n    "MultiClusterKubernetesToolRouter",\n]\n'
FACTORY_SOURCE = 'from services.agent_runtime.app.tools.registry import (\n    ToolRegistry,\n)\n\nfrom services.agent_runtime.app.tools.manager import (\n    ToolManager,\n)\n\nfrom services.agent_runtime.app.tools.mock.echo import (\n    EchoTool,\n)\n\nfrom services.agent_runtime.app.tools.prometheus.tool import (\n    PrometheusTool,\n)\n\nfrom services.agent_runtime.app.tools.kubernetes.tool import (\n    KubernetesTool,\n)\n\nfrom services.agent_runtime.app.tools.kubernetes.change_tool import (\n    KubernetesChangeTool,\n)\n\nfrom services.agent_runtime.app.tools.kubernetes.router import (\n    KubernetesClusterRegistry,\n    KubernetesClusterRoutingError,\n    MultiClusterKubernetesChangeToolRouter,\n    MultiClusterKubernetesToolRouter,\n)\n\n\ndef create_tool_manager(\n    kubernetes_cluster_registry: (\n        KubernetesClusterRegistry | None\n    ) = None,\n) -> ToolManager:\n\n\n    registry = ToolRegistry()\n\n\n    registry.register(\n        EchoTool()\n    )\n\n\n    registry.register(\n        PrometheusTool()\n    )\n\n\n    if kubernetes_cluster_registry is None:\n\n        kubernetes = KubernetesTool()\n\n\n        registry.register(\n            kubernetes\n        )\n\n\n        registry.register(\n            KubernetesChangeTool(\n                kubernetes\n            )\n        )\n\n    else:\n\n        if not isinstance(\n            kubernetes_cluster_registry,\n            KubernetesClusterRegistry,\n        ):\n            raise TypeError(\n                "Tool factory Kubernetes cluster registry is invalid"\n            )\n\n        if (\n            kubernetes_cluster_registry.count\n            == 0\n        ):\n            raise KubernetesClusterRoutingError(\n                "Tool factory multi-cluster mode requires at least one cluster"\n            )\n\n\n        registry.register(\n            MultiClusterKubernetesToolRouter(\n                kubernetes_cluster_registry\n            )\n        )\n\n\n        registry.register(\n            MultiClusterKubernetesChangeToolRouter(\n                kubernetes_cluster_registry\n            )\n        )\n\n\n    return ToolManager(\n        registry\n    )\n'
RUNTIME_SOURCE = 'from copy import deepcopy\n\nfrom services.agent_runtime.app.registry.factory import (\n    create_agent_registry,\n)\nfrom services.agent_runtime.app.llm.gateway.factory import (\n    create_llm_gateway,\n)\nfrom services.agent_runtime.app.llm.gateway.gateway import (\n    LLMGateway,\n)\nfrom services.agent_runtime.app.planner.agent_planner import (\n    AgentPlanner,\n)\nfrom services.agent_runtime.app.pipeline.planner_pipeline import (\n    PlannerPipeline,\n)\nfrom services.agent_runtime.app.memory.store import (\n    MemoryStore,\n)\nfrom services.agent_runtime.app.tools.factory import (\n    create_tool_manager,\n)\nfrom services.agent_runtime.app.tools.kubernetes.router import (\n    KubernetesClusterRegistry,\n)\nfrom services.agent_runtime.app.skills.factory import (\n    create_skill_registry,\n)\nfrom services.agent_runtime.app.mcp.factory import (\n    create_mcp_registry,\n)\nfrom services.agent_runtime.app.observability.collector import (\n    TraceCollector,\n)\nfrom services.agent_runtime.app.evaluation.factory import (\n    create_evaluation_registry,\n)\nfrom services.agent_runtime.app.policy.factory import (\n    create_policy_engine,\n)\nfrom services.agent_runtime.app.approval.service import (\n    ApprovalService,\n)\nfrom services.agent_runtime.app.incident.store import (\n    IncidentStore,\n)\nfrom services.agent_runtime.app.incident.service import (\n    IncidentService,\n)\nfrom services.agent_runtime.app.investigation.comparison import (\n    build_rca_investigation_comparison,\n)\nfrom services.agent_runtime.app.investigation.factory import (\n    create_investigation_coordinator,\n)\nfrom services.agent_runtime.app.investigation.llm_gateway_adapter import (\n    InvestigationLLMGatewayAdapter,\n)\nfrom services.agent_runtime.app.investigation.reasoner import (\n    BaseInvestigationReasoner,\n    LLMInvestigationReasoner,\n)\nfrom services.agent_runtime.app.investigation.settings import (\n    InvestigationSettings,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    InvestigationState,\n)\nfrom services.agent_runtime.app.model.context import (\n    AgentContext,\n)\nfrom services.agent_runtime.app.workflow.service import (\n    WorkflowService,\n)\nfrom services.agent_runtime.app.action.execution_service import (\n    ActionExecutionService,\n)\nfrom services.agent_runtime.app.action.execution_store import (\n    ActionExecutionStore,\n)\nfrom services.agent_runtime.app.action.kubernetes_preflight import (\n    KubernetesPreflightResolver,\n)\nfrom services.agent_runtime.app.action.kubernetes_preflight_factory import (\n    create_kubernetes_preflight_resolver,\n)\nfrom services.agent_runtime.app.action.kubernetes_production_executor import (\n    KubernetesProductionExecutor,\n)\nfrom services.agent_runtime.app.action.kubernetes_production_factory import (\n    create_kubernetes_production_executor,\n)\nfrom services.agent_runtime.app.action.preflight_artifact_service import (\n    PreflightArtifactService,\n)\nfrom services.agent_runtime.app.action.preflight_artifact_store import (\n    PreflightArtifactStore,\n)\nfrom services.agent_runtime.app.action.production_action_preparation import (\n    ProductionActionPreparationService,\n)\nfrom services.agent_runtime.app.action.production_action_query import (\n    ProductionActionQueryService,\n)\nfrom services.agent_runtime.app.action.production_action_guard import (\n    ProductionActionExpiryGuard,\n)\nfrom services.agent_runtime.app.action.production_pilot import (\n    KubernetesProductionPilotControl,\n    ProductionPilotReadinessService,\n)\nfrom services.agent_runtime.app.action.production_pilot_factory import (\n    create_kubernetes_production_pilot_control,\n)\nfrom services.agent_runtime.app.action.production_pilot_budget_service import (\n    ProductionPilotBudgetService,\n)\nfrom services.agent_runtime.app.action.production_pilot_budget_store import (\n    ProductionPilotBudgetStore,\n)\nfrom services.agent_runtime.app.action.production_pilot_rehearsal import (\n    ProductionPilotRehearsalService,\n)\nfrom services.agent_runtime.app.action.production_pilot_crash_rehearsal import (\n    ProductionPilotCrashRecoveryRehearsalService,\n)\nfrom services.agent_runtime.app.action.production_pilot_pre_enable_evidence import (\n    ProductionPilotPreEnableEvidenceService,\n)\nfrom services.agent_runtime.app.action.production_pilot_final_handoff import (\n    ProductionPilotFinalHandoffRehearsalService,\n)\nfrom services.agent_runtime.app.action.production_pilot_live_probe import (\n    ProductionPilotLiveReadinessProbe,\n    create_production_pilot_live_readiness_probe,\n)\nfrom services.agent_runtime.app.action.production_pilot_go_no_go_service import (\n    ProductionPilotGoNoGoService,\n)\nfrom services.agent_runtime.app.action.production_pilot_go_no_go_store import (\n    ProductionPilotGoNoGoStore,\n)\nfrom services.agent_runtime.app.action.production_pilot_ceremony_service import (\n    ProductionPilotCeremonyService,\n)\nfrom services.agent_runtime.app.action.production_pilot_ceremony_store import (\n    ProductionPilotCeremonyStore,\n)\nfrom services.agent_runtime.app.verification.collector import (\n    VerificationEvidenceCollector,\n)\nfrom services.agent_runtime.app.verification.coordinator import (\n    VerificationCoordinator,\n)\nfrom services.agent_runtime.app.verification.profiles import (\n    VerificationProfileFactory,\n)\nfrom services.agent_runtime.app.verification.service import (\n    VerificationService,\n)\nfrom services.agent_runtime.app.verification.store import (\n    VerificationStore,\n)\nfrom services.agent_runtime.app.runtime.action_runtime import (\n    ActionRuntime,\n)\nfrom services.agent_runtime.app.runtime.verification_runtime import (\n    VerificationRuntime,\n)\nfrom services.agent_runtime.app.security.factory import (\n    create_authentication_service,\n)\nfrom services.agent_runtime.app.security.policy import (\n    SecurityPolicyEngine,\n)\nfrom services.agent_runtime.app.security.service import (\n    AuthenticationService,\n)\nfrom services.sandbox.executor.local import (\n    LocalSandboxExecutor,\n)\nfrom services.sandbox.policy.validator import (\n    SandboxPolicyValidator,\n)\n\n\nfrom services.agent_runtime.app.incident_evidence.recorder import (\n    ProductionIncidentEvidenceRecorder,\n)\nfrom services.agent_runtime.app.incident_evidence.settings import (\n    IncidentEvidenceRecorderSettings,\n)\n\nclass AgentRuntime:\n    """\n    Runtime container.\n\n    Owns and shares security and runtime infrastructure\n    across Pipeline, Action and Verification.\n\n    security_policy is the RBAC authorization policy. The existing policy\n    attribute remains the remediation business policy engine.\n    """\n\n    def __init__(\n        self,\n        authentication_service: (\n            AuthenticationService | None\n        ) = None,\n        security_policy: (\n            SecurityPolicyEngine | None\n        ) = None,\n        kubernetes_preflight: (\n            KubernetesPreflightResolver | None\n        ) = None,\n        kubernetes_production_executor: (\n            KubernetesProductionExecutor | None\n        ) = None,\n        production_pilot_control: (\n            KubernetesProductionPilotControl | None\n        ) = None,\n        production_pilot_budget_service: (\n            ProductionPilotBudgetService | None\n        ) = None,\n        production_pilot_live_probe: (\n            ProductionPilotLiveReadinessProbe | None\n        ) = None,\n        kubernetes_cluster_registry: (\n            KubernetesClusterRegistry | None\n        ) = None,\n        llm_gateway: (\n            LLMGateway | None\n        ) = None,\n        investigation_reasoner: (\n            BaseInvestigationReasoner | None\n        ) = None,\n        investigation_settings: (\n            InvestigationSettings | None\n        ) = None,\n    ) -> None:\n        # Validate every injected security component before factories, stores\n        # or other runtime components can produce side effects.\n        if (\n            authentication_service is not None\n            and not isinstance(\n                authentication_service,\n                AuthenticationService,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime authentication service is invalid"\n            )\n\n        if (\n            security_policy is not None\n            and not isinstance(\n                security_policy,\n                SecurityPolicyEngine,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime security policy is invalid"\n            )\n\n        if (\n            kubernetes_preflight is not None\n            and not isinstance(\n                kubernetes_preflight,\n                KubernetesPreflightResolver,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Kubernetes preflight resolver is invalid"\n            )\n\n        if (\n            kubernetes_production_executor is not None\n            and not isinstance(\n                kubernetes_production_executor,\n                KubernetesProductionExecutor,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Kubernetes production executor is invalid"\n            )\n\n        if (\n            production_pilot_control is not None\n            and not isinstance(\n                production_pilot_control,\n                KubernetesProductionPilotControl,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Kubernetes production pilot control is invalid"\n            )\n\n        if (\n            production_pilot_budget_service is not None\n            and not isinstance(\n                production_pilot_budget_service,\n                ProductionPilotBudgetService,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Kubernetes production pilot budget service is invalid"\n            )\n\n        if (\n            production_pilot_live_probe is not None\n            and not isinstance(\n                production_pilot_live_probe,\n                ProductionPilotLiveReadinessProbe,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Production Pilot live probe is invalid"\n            )\n\n        if (\n            kubernetes_cluster_registry is not None\n            and not isinstance(\n                kubernetes_cluster_registry,\n                KubernetesClusterRegistry,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Kubernetes cluster registry is invalid"\n            )\n\n        if (\n            llm_gateway is not None\n            and not isinstance(\n                llm_gateway,\n                LLMGateway,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime shared LLM gateway is invalid"\n            )\n\n        if (\n            investigation_reasoner is not None\n            and not isinstance(\n                investigation_reasoner,\n                BaseInvestigationReasoner,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Investigation reasoner is invalid"\n            )\n\n        if (\n            investigation_settings is not None\n            and not isinstance(\n                investigation_settings,\n                InvestigationSettings,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Investigation settings are invalid"\n            )\n\n        # Resolve disabled-default Investigation configuration before any\n        # Runtime store, tool, credential, network or LLM component is created.\n        self.investigation_settings = (\n            investigation_settings\n            if investigation_settings is not None\n            else InvestigationSettings.from_environment()\n        )\n\n        investigation_shared_gateway = None\n\n        # An enabled LLM-backed Investigation must use the exact shared\n        # LLMGateway instance that AgentRuntime will provide to its Agents.\n        #\n        # Disabled Investigation deliberately does not inspect or touch the\n        # supplied reasoner\'s LLM adapter.\n        if (\n            self.investigation_settings.enabled\n            and isinstance(\n                investigation_reasoner,\n                LLMInvestigationReasoner,\n            )\n        ):\n            investigation_llm = (\n                investigation_reasoner.investigation_llm\n            )\n\n            if not isinstance(\n                investigation_llm,\n                InvestigationLLMGatewayAdapter,\n            ):\n                raise TypeError(\n                    "AgentRuntime LLM Investigation requires "\n                    "InvestigationLLMGatewayAdapter"\n                )\n\n            investigation_shared_gateway = (\n                investigation_llm.llm_gateway\n            )\n\n            if not isinstance(\n                investigation_shared_gateway,\n                LLMGateway,\n            ):\n                raise TypeError(\n                    "AgentRuntime Investigation shared LLM gateway is invalid"\n                )\n\n            if (\n                llm_gateway is not None\n                and investigation_shared_gateway\n                is not llm_gateway\n            ):\n                raise TypeError(\n                    "AgentRuntime Investigation LLM gateway must be shared"\n                )\n\n        # Preserve the existing fail-closed Investigation assembly boundary.\n        # Enabled mode without an explicit reasoner still fails here before\n        # any Runtime or LLM infrastructure is constructed.\n        self.investigation_coordinator = (\n            create_investigation_coordinator(\n                reasoner=investigation_reasoner,\n                settings=self.investigation_settings,\n            )\n        )\n\n        # Do not construct a default Gateway yet. Keeping this unresolved\n        # preserves the previous initialization order. If Investigation\n        # already carries the approved Gateway Adapter, Runtime adopts that\n        # exact Gateway object as its shared instance.\n        self.llm_gateway = (\n            llm_gateway\n            if llm_gateway is not None\n            else investigation_shared_gateway\n        )\n\n        self.authentication = (\n            authentication_service\n            if authentication_service is not None\n            else create_authentication_service()\n        )\n\n        self.security_policy = (\n            security_policy\n            if security_policy is not None\n            else SecurityPolicyEngine()\n        )\n\n        self.kubernetes_preflight = (\n            kubernetes_preflight\n            if kubernetes_preflight is not None\n            else create_kubernetes_preflight_resolver()\n        )\n\n        self.production_pilot_control = (\n            production_pilot_control\n            if production_pilot_control is not None\n            else create_kubernetes_production_pilot_control()\n        )\n\n        # This independent gate may read both credential values at startup,\n        # but can construct only a two-GET probe. Disabled mode returns before\n        # any credential or CA access.\n        self.production_pilot_live_probe = (\n            production_pilot_live_probe\n            if production_pilot_live_probe is not None\n            else create_production_pilot_live_readiness_probe()\n        )\n\n        self.production_pilot_budget_store = None\n        self.production_pilot_budget_service = (\n            production_pilot_budget_service\n        )\n        if (\n            self.production_pilot_budget_service is None\n            and self.production_pilot_control.config.enabled\n        ):\n            self.production_pilot_budget_store = (\n                ProductionPilotBudgetStore()\n            )\n            self.production_pilot_budget_service = (\n                ProductionPilotBudgetService(\n                    store=(\n                        self.production_pilot_budget_store\n                    )\n                )\n            )\n\n        self.kubernetes_production_executor = (\n            kubernetes_production_executor\n            if kubernetes_production_executor is not None\n            else create_kubernetes_production_executor(\n                pilot_control=(\n                    self.production_pilot_control\n                ),\n                pilot_budget_service=(\n                    self.production_pilot_budget_service\n                ),\n            )\n        )\n\n        if self.kubernetes_production_executor is not None:\n            executor_control = getattr(\n                self.kubernetes_production_executor,\n                "pilot_control",\n                None,\n            )\n            if executor_control is None:\n                self.kubernetes_production_executor.pilot_control = (\n                    self.production_pilot_control\n                )\n            elif executor_control is not self.production_pilot_control:\n                raise TypeError(\n                    "AgentRuntime Kubernetes production pilot control must be shared"\n                )\n            executor_budget = getattr(\n                self.kubernetes_production_executor,\n                "pilot_budget_service",\n                None,\n            )\n            if executor_budget is None:\n                if self.production_pilot_budget_service is None:\n                    raise TypeError(\n                        "AgentRuntime Kubernetes production pilot budget is unavailable"\n                    )\n                self.kubernetes_production_executor.pilot_budget_service = (\n                    self.production_pilot_budget_service\n                )\n            elif executor_budget is not self.production_pilot_budget_service:\n                raise TypeError(\n                    "AgentRuntime Kubernetes production pilot budget must be shared"\n                )\n\n        if (\n            self.kubernetes_production_executor is not None\n            and self.kubernetes_preflight is None\n        ):\n            raise TypeError(\n                "AgentRuntime Kubernetes production executor requires "\n                "trusted preflight"\n            )\n\n        self.production_pilot_readiness = (\n            ProductionPilotReadinessService(\n                control=(\n                    self.production_pilot_control\n                ),\n                production_executor_configured=(\n                    self.kubernetes_production_executor\n                    is not None\n                ),\n            )\n        )\n        self.production_pilot_rehearsal = (\n            ProductionPilotRehearsalService(\n                control=(\n                    self.production_pilot_control\n                ),\n                budget_service=(\n                    self.production_pilot_budget_service\n                ),\n                production_executor_configured=(\n                    self.kubernetes_production_executor\n                    is not None\n                ),\n            )\n        )\n        # Pure recovery-policy proof. It owns no store, credential, network\n        # client or executor and is available while the production gate is\n        # disabled so operators can rehearse recovery before enablement.\n        self.production_pilot_crash_recovery_rehearsal = (\n            ProductionPilotCrashRecoveryRehearsalService()\n        )\n\n        self.memory = MemoryStore()\n\n        self.kubernetes_cluster_registry = (\n            kubernetes_cluster_registry\n        )\n\n        if (\n            self.kubernetes_cluster_registry\n            is None\n        ):\n            self.tools = create_tool_manager()\n        else:\n            self.tools = create_tool_manager(\n                kubernetes_cluster_registry=(\n                    self.kubernetes_cluster_registry\n                )\n            )\n\n        self.skills = create_skill_registry()\n        self.mcp = create_mcp_registry()\n        self.tracer = TraceCollector()\n        self.evaluators = create_evaluation_registry()\n\n        # Remediation business policy. This is intentionally separate from\n        # security_policy, which authorizes operator-facing operations.\n        self.policy = create_policy_engine()\n\n        self.preflight_artifact_store = None\n        self.preflight_artifact_service = None\n        self.production_action_guard = None\n        self.production_action_preparation = None\n        self.production_action_query = None\n\n        if self.kubernetes_preflight is not None:\n            self.preflight_artifact_store = PreflightArtifactStore()\n            self.preflight_artifact_service = PreflightArtifactService(\n                store=self.preflight_artifact_store\n            )\n            self.production_action_guard = (\n                ProductionActionExpiryGuard(\n                    artifact_service=(\n                        self.preflight_artifact_service\n                    )\n                )\n            )\n\n        self.approval = ApprovalService()\n\n        if self.production_action_guard is not None:\n            self.approval.manager.set_transition_guard(\n                self.production_action_guard\n            )\n\n        if self.preflight_artifact_service is not None:\n            self.production_action_preparation = (\n                ProductionActionPreparationService(\n                    resolver=self.kubernetes_preflight,\n                    artifact_service=self.preflight_artifact_service,\n                    approval_service=self.approval,\n                )\n            )\n\n        self.production_pilot_ceremony_store = None\n        self.production_pilot_ceremony = None\n        if (\n            self.production_pilot_control.config.enabled\n            and self.production_pilot_budget_service is not None\n            and self.preflight_artifact_service is not None\n        ):\n            self.production_pilot_ceremony_store = (\n                ProductionPilotCeremonyStore()\n            )\n            self.production_pilot_ceremony = (\n                ProductionPilotCeremonyService(\n                    store=(\n                        self.production_pilot_ceremony_store\n                    ),\n                    control=(\n                        self.production_pilot_control\n                    ),\n                    rehearsal=(\n                        self.production_pilot_rehearsal\n                    ),\n                    budget_service=(\n                        self.production_pilot_budget_service\n                    ),\n                    approval_service=self.approval,\n                    artifact_service=(\n                        self.preflight_artifact_service\n                    ),\n                )\n            )\n\n        self.incident_store = IncidentStore()\n\n        if self.preflight_artifact_service is not None:\n            self.production_action_query = (\n                ProductionActionQueryService(\n                    artifact_service=(\n                        self.preflight_artifact_service\n                    ),\n                    approval_service=self.approval,\n                    incident_store=self.incident_store,\n                )\n            )\n\n        self.incident_service = IncidentService(\n            store=self.incident_store\n        )\n\n        self.workflow_service = WorkflowService(\n            incident_service=self.incident_service\n        )\n\n        self.action_execution_store = ActionExecutionStore()\n\n        self.action_execution_service = ActionExecutionService(\n            store=self.action_execution_store\n        )\n\n        self.action_runtime = ActionRuntime(\n            approval_service=self.approval,\n            incident_store=self.incident_store,\n            action_execution_service=self.action_execution_service,\n            production_action_guard=(\n                self.production_action_guard\n            ),\n            kubernetes_production_executor=(\n                self.kubernetes_production_executor\n            ),\n            preflight_artifact_service=(\n                self.preflight_artifact_service\n                if self.kubernetes_production_executor is not None\n                else None\n            ),\n            production_pilot_control=(\n                self.production_pilot_control\n            ),\n            production_pilot_budget_service=(\n                self.production_pilot_budget_service\n            ),\n            production_pilot_ceremony_service=(\n                self.production_pilot_ceremony\n                if self.kubernetes_production_executor is not None\n                else None\n            ),\n        )\n\n        self.verification_store = VerificationStore()\n\n        self.verification = VerificationService(\n            store=self.verification_store\n        )\n\n        self.verification_runtime = VerificationRuntime(\n            verification_service=self.verification,\n            incident_store=self.incident_store,\n        )\n\n        self.verification_profile_factory = VerificationProfileFactory()\n\n        self.verification_collector = VerificationEvidenceCollector(\n            tools=self.tools\n        )\n\n        self.verification_coordinator = VerificationCoordinator(\n            profile_factory=self.verification_profile_factory,\n            collector=self.verification_collector,\n            verification_runtime=self.verification_runtime,\n        )\n\n        # Final pre-enable evidence is assembled only when every production\n        # preparation component is available. The service is read-only and\n        # deliberately owns no executor or mutable workflow operation.\n        self.production_pilot_pre_enable_evidence = None\n        if all(\n            component is not None\n            for component in (\n                self.production_pilot_ceremony,\n                self.production_pilot_budget_service,\n                self.preflight_artifact_service,\n            )\n        ):\n            self.production_pilot_pre_enable_evidence = (\n                ProductionPilotPreEnableEvidenceService(\n                    readiness_service=(\n                        self.production_pilot_readiness\n                    ),\n                    rehearsal_service=(\n                        self.production_pilot_rehearsal\n                    ),\n                    crash_rehearsal_service=(\n                        self.production_pilot_crash_recovery_rehearsal\n                    ),\n                    ceremony_service=(\n                        self.production_pilot_ceremony\n                    ),\n                    budget_service=(\n                        self.production_pilot_budget_service\n                    ),\n                    artifact_service=(\n                        self.preflight_artifact_service\n                    ),\n                    approval_service=self.approval,\n                    incident_store=self.incident_store,\n                    action_execution_service=(\n                        self.action_execution_service\n                    ),\n                    verification_service=self.verification,\n                )\n            )\n\n        # The final handoff rehearsal is also strictly read-only. It is\n        # available only with the full prepared Pilot chain and explicitly\n        # records whether production executors remain absent while the gate\n        # is disabled.\n        self.production_pilot_final_handoff_rehearsal = None\n        if self.production_pilot_pre_enable_evidence is not None:\n            self.production_pilot_final_handoff_rehearsal = (\n                ProductionPilotFinalHandoffRehearsalService(\n                    pilot_control=self.production_pilot_control,\n                    pre_enable_evidence_service=(\n                        self.production_pilot_pre_enable_evidence\n                    ),\n                    preflight_resolver=self.kubernetes_preflight,\n                    production_executor_configured=(\n                        self.kubernetes_production_executor is not None\n                    ),\n                    action_runtime_production_executor_configured=(\n                        getattr(\n                            self.action_runtime,\n                            "kubernetes_production_executor",\n                            None,\n                        )\n                        is not None\n                    ),\n                )\n            )\n\n        # A dedicated database is created only when the separately gated live\n        # probe exists and the full zero-write handoff chain is available.\n        self.production_pilot_go_no_go_store = None\n        self.production_pilot_go_no_go = None\n        if (\n            self.production_pilot_live_probe is not None\n            and self.production_pilot_final_handoff_rehearsal is not None\n            and self.preflight_artifact_service is not None\n        ):\n            self.production_pilot_go_no_go_store = (\n                ProductionPilotGoNoGoStore()\n            )\n            self.production_pilot_go_no_go = (\n                ProductionPilotGoNoGoService(\n                    store=self.production_pilot_go_no_go_store,\n                    live_probe=self.production_pilot_live_probe,\n                    final_handoff_service=(\n                        self.production_pilot_final_handoff_rehearsal\n                    ),\n                    artifact_service=self.preflight_artifact_service,\n                    pilot_control=self.production_pilot_control,\n                )\n            )\n\n        self.sandbox = LocalSandboxExecutor()\n\n        self.sandbox_policy = SandboxPolicyValidator()\n\n        if self.llm_gateway is None:\n            self.llm_gateway = create_llm_gateway()\n\n        self.registry = create_agent_registry(\n            llm_gateway=self.llm_gateway,\n        )\n\n        self.planner = AgentPlanner()\n\n        self.pipeline = PlannerPipeline(\n            self.registry,\n            self.planner,\n            self.tracer,\n            self.evaluators,\n            incident_store=self.incident_store,\n            incident_service=self.incident_service,\n            workflow_service=self.workflow_service,\n        )\n\n    async def execute(\n        self,\n        context: AgentContext,\n    ):\n        """\n        Execute the primary PlannerPipeline and, when explicitly enabled,\n        run Investigation automatically as a best-effort Shadow.\n\n        Ordering is deliberate:\n\n        1. PlannerPipeline completes first.\n        2. Investigation receives an isolated AgentContext.\n        3. Only the bounded investigation_shadow snapshot is copied back.\n\n        Investigation can never change the Pipeline result, Incident,\n        variables, results, trace, Approval, executions or evaluations.\n\n        Investigation orchestration failure is sanitized and recorded in\n        metadata without failing an otherwise successful Pipeline execution.\n        """\n\n        if not isinstance(\n            context,\n            AgentContext,\n        ):\n            raise TypeError(\n                "AgentRuntime execution context is invalid"\n            )\n\n        # Reserved Shadow metadata from a previous execution must never be\n        # visible to the primary Pipeline, even when this Runtime currently\n        # has Investigation disabled.\n        for reserved_key in (\n            "investigation_shadow",\n            "investigation_shadow_orchestration",\n            "investigation_rca_comparison",\n        ):\n            context.metadata.pop(\n                reserved_key,\n                None,\n            )\n\n        # Primary workflow semantics remain authoritative. Pipeline failure\n        # propagates normally and Investigation is not attempted afterward.\n        context.metadata.pop(\n            "incident_evidence_recorder",\n            None,\n        )\n\n        results = await self.pipeline.execute(\n            context\n        )\n\n        # Evidence Recorder is evaluation-only and best-effort.\n        await self._record_incident_evidence_shadow(\n            context\n        )\n\n        if self.investigation_coordinator is None:\n            return results\n\n        shadow_context = (\n            self._create_investigation_shadow_context(\n                context\n            )\n        )\n\n        try:\n            await self.run_investigation_shadow(\n                shadow_context\n            )\n\n            snapshot = shadow_context.metadata.get(\n                "investigation_shadow"\n            )\n\n            if (\n                not isinstance(\n                    snapshot,\n                    dict,\n                )\n                or snapshot.get(\n                    "shadow_mode"\n                )\n                is not True\n                or snapshot.get(\n                    "read_only"\n                )\n                is not True\n            ):\n                raise RuntimeError(\n                    "Investigation Shadow snapshot is invalid"\n                )\n\n            context.metadata[\n                "investigation_shadow"\n            ] = deepcopy(\n                snapshot\n            )\n\n        except Exception as exc:\n            # Shadow means Shadow: an Investigation orchestration fault must\n            # never convert a successful PlannerPipeline execution to failed.\n            #\n            # Raw exception text is deliberately excluded because provider,\n            # URL, credential or tool details may be present in it.\n            context.metadata[\n                "investigation_shadow_orchestration"\n            ] = {\n                "shadow_mode": True,\n                "read_only": True,\n                "automatic": True,\n                "status": "failed",\n                "failure_code": (\n                    type(exc).__name__[:256]\n                ),\n            }\n\n        # Comparison is evaluation-only. It cannot change the authoritative\n        # RCA stored in context.variables["rca"] and has no Healing authority.\n        try:\n            context.metadata[\n                "investigation_rca_comparison"\n            ] = build_rca_investigation_comparison(\n                rca=context.variables.get(\n                    "rca"\n                ),\n                investigation_snapshot=(\n                    context.metadata.get(\n                        "investigation_shadow"\n                    )\n                ),\n                orchestration_snapshot=(\n                    context.metadata.get(\n                        "investigation_shadow_orchestration"\n                    )\n                ),\n            )\n        except Exception as exc:\n            # A comparison bug must remain weaker than Shadow itself and must\n            # never fail a successful primary Pipeline.\n            context.metadata[\n                "investigation_rca_comparison"\n            ] = {\n                "schema_version": "v1",\n                "shadow_mode": True,\n                "read_only": True,\n                "decision_influence": False,\n                "available": False,\n                "comparison_status": (\n                    "comparison_failed"\n                ),\n                "failure_code": (\n                    type(exc).__name__[:256]\n                ),\n            }\n\n        return results\n\n    def _create_investigation_shadow_context(\n        self,\n        context: AgentContext,\n    ) -> AgentContext:\n        """\n        Build the minimum-privilege context for automatic Investigation.\n\n        Copied:\n        - event input\n        - request correlation ID\n\n        Shared:\n        - exact Runtime-owned ToolManager\n\n        Deliberately not shared:\n        - Incident\n        - variables\n        - results\n        - metadata\n        - trace\n        - memory\n        - skills\n        - MCP\n        - sandbox\n        - Approval\n        - executions\n        - evaluations\n        """\n\n        return AgentContext(\n            request_id=context.request_id,\n            event=deepcopy(\n                context.event\n            ),\n            tools=self.tools,\n            metadata={},\n        )\n\n    async def run_investigation_shadow(\n        self,\n        context: AgentContext,\n    ) -> InvestigationState:\n        """\n        Explicitly execute the enabled read-only Investigation Shadow.\n\n        This method is intentionally separate from PlannerPipeline.\n\n        PlannerPipeline itself never invokes Investigation. AgentRuntime\n        may call this lower-level entry point after a successful Pipeline\n        execution when automatic Shadow Investigation is enabled.\n\n        The supplied AgentContext must use the exact Runtime ToolManager so\n        Investigation probes cannot bypass Runtime-owned tool boundaries.\n        """\n\n        if not isinstance(\n            context,\n            AgentContext,\n        ):\n            raise TypeError(\n                "AgentRuntime Investigation Shadow context is invalid"\n            )\n\n        if self.investigation_coordinator is None:\n            raise RuntimeError(\n                "AgentRuntime Investigation Shadow is disabled"\n            )\n\n        if context.tools is not self.tools:\n            raise TypeError(\n                "AgentRuntime Investigation Shadow requires shared Runtime tools"\n            )\n\n        return await (\n            self.investigation_coordinator.investigate(\n                context\n            )\n        )\n\n    async def _record_incident_evidence_shadow(\n        self,\n        context: AgentContext,\n    ) -> None:\n        """\n        Best-effort, decision-isolated production evidence preservation.\n\n        Runs only after the authoritative PlannerPipeline succeeds.\n        Disabled mode constructs no Recorder and issues no production Probe.\n        """\n\n        try:\n            settings = (\n                IncidentEvidenceRecorderSettings\n                .from_environment()\n            )\n        except Exception as exc:\n            context.metadata[\n                "incident_evidence_recorder"\n            ] = {\n                "schema_version": "v1",\n                "shadow_mode": True,\n                "read_only": True,\n                "decision_influence": False,\n                "automatic": True,\n                "status": "failed",\n                "failure_code": (\n                    type(exc).__name__[:256]\n                ),\n            }\n            return\n\n        if not settings.enabled:\n            return\n\n        recorder_context = AgentContext(\n            request_id=context.request_id,\n            event=deepcopy(\n                context.event\n            ),\n            tools=self.tools,\n            metadata={},\n        )\n\n        try:\n            recorder = ProductionIncidentEvidenceRecorder(\n                settings.resolve_output_dir()\n            )\n\n            result = await recorder.record(\n                recorder_context\n            )\n\n            context.metadata[\n                "incident_evidence_recorder"\n            ] = {\n                "schema_version": "v1",\n                "shadow_mode": True,\n                "read_only": True,\n                "decision_influence": False,\n                "automatic": True,\n                "status": "captured",\n                "created": result.created,\n                "incident_id": result.incident_id,\n                "observation_count": (\n                    result.observation_count\n                ),\n                "capture_file": result.path.name,\n            }\n\n        except Exception as exc:\n            context.metadata[\n                "incident_evidence_recorder"\n            ] = {\n                "schema_version": "v1",\n                "shadow_mode": True,\n                "read_only": True,\n                "decision_influence": False,\n                "automatic": True,\n                "status": "failed",\n                "failure_code": (\n                    type(exc).__name__[:256]\n                ),\n            }\n'
TEST_SOURCE = 'from __future__ import annotations\n\nfrom typing import Any\n\nimport pytest\n\nimport services.agent_runtime.app.runtime.runtime as runtime_module\nimport services.agent_runtime.app.tools.kubernetes.router as router_module\n\nfrom common.config.settings import (\n    AuthenticationConfig,\n)\n\nfrom services.agent_runtime.app.investigation.settings import (\n    InvestigationSettings,\n)\nfrom services.agent_runtime.app.security.factory import (\n    create_authentication_service,\n)\nfrom services.agent_runtime.app.tools.factory import (\n    create_tool_manager,\n)\nfrom services.agent_runtime.app.tools.kubernetes.change_tool import (\n    KubernetesChangeTool,\n)\nfrom services.agent_runtime.app.tools.kubernetes.router import (\n    KubernetesClusterRegistry,\n    KubernetesClusterRoutingError,\n    MultiClusterKubernetesChangeToolRouter,\n    MultiClusterKubernetesToolRouter,\n)\nfrom services.agent_runtime.app.tools.kubernetes.tool import (\n    KubernetesTool,\n)\nfrom services.agent_runtime.app.tools.manager import (\n    ToolManager,\n)\nfrom services.agent_runtime.app.tools.registry import (\n    ToolRegistry,\n)\n\n\nclass RecordingKubernetesTool(\n    KubernetesTool\n):\n    def __init__(\n        self,\n        cluster: str,\n    ) -> None:\n        super().__init__(\n            api_url=(\n                f"https://{cluster}.kubernetes.test"\n            ),\n            cluster_name=cluster,\n            bearer_token=(\n                f"{cluster}-unit-token-123456"\n            ),\n            allow_dry_run_fallback=False,\n        )\n\n        self.calls: list[\n            dict[str, Any]\n        ] = []\n\n    async def execute(\n        self,\n        **kwargs: Any,\n    ) -> dict[str, Any]:\n        self.calls.append(\n            dict(\n                kwargs\n            )\n        )\n\n        return {\n            "success": True,\n            "source": "kubernetes",\n            "mode": "read_only",\n            "production_signal": True,\n            "cluster": self.cluster_name,\n            "observed_at": (\n                "2026-08-11T03:00:00+00:00"\n            ),\n            "data": {\n                "selected_cluster": (\n                    self.cluster_name\n                ),\n            },\n        }\n\n\nclass RecordingChangeTool:\n    created = []\n\n    def __init__(\n        self,\n        kubernetes,\n    ) -> None:\n        self.kubernetes = kubernetes\n        self.calls = []\n\n        type(\n            self\n        ).created.append(\n            self\n        )\n\n    async def execute(\n        self,\n        **kwargs,\n    ):\n        self.calls.append(\n            dict(\n                kwargs\n            )\n        )\n\n        return {\n            "success": True,\n            "source": "kubernetes_change",\n            "mode": "read_only",\n            "production_signal": True,\n            "cluster": (\n                self.kubernetes\n                .cluster_name\n            ),\n            "observed_at": (\n                "2026-08-11T03:00:00+00:00"\n            ),\n            "data": {\n                "selected_cluster": (\n                    self.kubernetes\n                    .cluster_name\n                ),\n            },\n        }\n\n\ndef cluster_tools():\n    sg = RecordingKubernetesTool(\n        "prod-sg-17"\n    )\n\n    us = RecordingKubernetesTool(\n        "prod-us-03"\n    )\n\n    return sg, us\n\n\ndef test_registry_is_exact_immutable_startup_mapping():\n    sg, us = cluster_tools()\n\n    registry = KubernetesClusterRegistry(\n        [\n            sg,\n            us,\n        ]\n    )\n\n    assert registry.count == 2\n\n    assert registry.cluster_names == (\n        "prod-sg-17",\n        "prod-us-03",\n    )\n\n    assert (\n        registry.resolve(\n            "prod-sg-17"\n        )\n        is sg\n    )\n\n    assert (\n        registry.resolve(\n            "prod-us-03"\n        )\n        is us\n    )\n\n    assert not hasattr(\n        registry,\n        "register",\n    )\n\n\ndef test_registry_rejects_duplicate_or_unbound_cluster_tools():\n    sg, _ = cluster_tools()\n\n    with pytest.raises(\n        KubernetesClusterRoutingError,\n        match="Duplicate",\n    ):\n        KubernetesClusterRegistry(\n            [\n                sg,\n                sg,\n            ]\n        )\n\n    with pytest.raises(\n        KubernetesClusterRoutingError,\n        match="cluster_name",\n    ):\n        KubernetesClusterRegistry(\n            [\n                KubernetesTool(\n                    api_url=(\n                        "https://unbound.kubernetes.test"\n                    ),\n                    cluster_name=None,\n                    bearer_token=(\n                        "unbound-unit-token-123456"\n                    ),\n                    allow_dry_run_fallback=False,\n                )\n            ]\n        )\n\n\ndef test_registry_rejects_cluster_without_live_endpoint():\n    tool = KubernetesTool(\n        api_url=None,\n        cluster_name="prod-no-endpoint",\n        bearer_token=(\n            "no-endpoint-unit-token-123456"\n        ),\n        allow_dry_run_fallback=False,\n    )\n\n    tool.api_url = None\n\n    with pytest.raises(\n        KubernetesClusterRoutingError,\n        match="API endpoint",\n    ):\n        KubernetesClusterRegistry(\n            [\n                tool\n            ]\n        )\n\n\n@pytest.mark.asyncio\nasync def test_kubernetes_router_selects_exact_requested_cluster():\n    sg, us = cluster_tools()\n\n    registry = KubernetesClusterRegistry(\n        [\n            sg,\n            us,\n        ]\n    )\n\n    router = MultiClusterKubernetesToolRouter(\n        registry\n    )\n\n    result = await router.execute(\n        action="describe",\n        resource="pod",\n        target="device-gateway-xyz789",\n        namespace="fleet-edge",\n        cluster="prod-us-03",\n    )\n\n    assert result[\n        "cluster"\n    ] == "prod-us-03"\n\n    assert sg.calls == []\n\n    assert us.calls == [\n        {\n            "action": "describe",\n            "resource": "pod",\n            "target": (\n                "device-gateway-xyz789"\n            ),\n            "namespace": "fleet-edge",\n            "cluster": "prod-us-03",\n        }\n    ]\n\n\n@pytest.mark.asyncio\nasync def test_unknown_cluster_fails_before_any_child_tool_call():\n    sg, us = cluster_tools()\n\n    router = (\n        MultiClusterKubernetesToolRouter(\n            KubernetesClusterRegistry(\n                [\n                    sg,\n                    us,\n                ]\n            )\n        )\n    )\n\n    with pytest.raises(\n        KubernetesClusterRoutingError,\n        match="not registered",\n    ):\n        await router.execute(\n            action="describe",\n            resource="pod",\n            target="x",\n            namespace="default",\n            cluster="prod-eu-05",\n        )\n\n    assert sg.calls == []\n    assert us.calls == []\n\n\n@pytest.mark.asyncio\nasync def test_multiple_clusters_require_explicit_cluster():\n    sg, us = cluster_tools()\n\n    router = (\n        MultiClusterKubernetesToolRouter(\n            KubernetesClusterRegistry(\n                [\n                    sg,\n                    us,\n                ]\n            )\n        )\n    )\n\n    with pytest.raises(\n        KubernetesClusterRoutingError,\n        match="cluster is required",\n    ):\n        await router.execute(\n            action="describe",\n            resource="pod",\n            target="x",\n            namespace="default",\n        )\n\n    assert sg.calls == []\n    assert us.calls == []\n\n\n@pytest.mark.asyncio\nasync def test_single_cluster_router_keeps_missing_cluster_compatibility():\n    sg, _ = cluster_tools()\n\n    router = (\n        MultiClusterKubernetesToolRouter(\n            KubernetesClusterRegistry(\n                [\n                    sg\n                ]\n            )\n        )\n    )\n\n    result = await router.execute(\n        action="describe",\n        resource="pod",\n        target="printer-session-api-abc123",\n        namespace="printing-control",\n    )\n\n    assert result[\n        "cluster"\n    ] == "prod-sg-17"\n\n    assert sg.calls[\n        0\n    ][\n        "cluster"\n    ] == "prod-sg-17"\n\n\n@pytest.mark.asyncio\nasync def test_change_router_uses_same_cluster_bound_kubernetes_tool(\n    monkeypatch,\n):\n    RecordingChangeTool.created = []\n\n    monkeypatch.setattr(\n        router_module,\n        "KubernetesChangeTool",\n        RecordingChangeTool,\n    )\n\n    sg, us = cluster_tools()\n\n    registry = KubernetesClusterRegistry(\n        [\n            sg,\n            us,\n        ]\n    )\n\n    router = (\n        MultiClusterKubernetesChangeToolRouter(\n            registry\n        )\n    )\n\n    result = await router.execute(\n        target="device-gateway-xyz789",\n        namespace="fleet-edge",\n        cluster="prod-us-03",\n        incident_time=(\n            "2026-08-11T03:00:00+00:00"\n        ),\n        view="workload",\n    )\n\n    assert result[\n        "cluster"\n    ] == "prod-us-03"\n\n    selected = [\n        item\n        for item\n        in RecordingChangeTool.created\n        if (\n            item.kubernetes\n            is us\n        )\n    ]\n\n    assert len(\n        selected\n    ) == 1\n\n    assert selected[\n        0\n    ].calls == [\n        {\n            "target": (\n                "device-gateway-xyz789"\n            ),\n            "namespace": "fleet-edge",\n            "incident_time": (\n                "2026-08-11T03:00:00+00:00"\n            ),\n            "view": "workload",\n            "cluster": "prod-us-03",\n        }\n    ]\n\n\ndef test_default_tool_factory_preserves_single_cluster_tool_identity(\n    monkeypatch,\n):\n    for name in (\n        "KUBERNETES_API_URL",\n        "KUBERNETES_CLUSTER_NAME",\n        "KUBERNETES_BEARER_TOKEN",\n        "KUBERNETES_TOKEN_FILE",\n        "KUBERNETES_CA_FILE",\n    ):\n        monkeypatch.delenv(\n            name,\n            raising=False,\n        )\n\n    manager = create_tool_manager()\n\n    kubernetes = (\n        manager.registry.get(\n            "kubernetes"\n        )\n    )\n\n    change = (\n        manager.registry.get(\n            "kubernetes_change"\n        )\n    )\n\n    assert isinstance(\n        kubernetes,\n        KubernetesTool,\n    )\n\n    assert isinstance(\n        change,\n        KubernetesChangeTool,\n    )\n\n    assert (\n        change.kubernetes\n        is kubernetes\n    )\n\n\ndef test_explicit_cluster_registry_switches_factory_to_router_tools():\n    sg, us = cluster_tools()\n\n    clusters = KubernetesClusterRegistry(\n        [\n            sg,\n            us,\n        ]\n    )\n\n    manager = create_tool_manager(\n        kubernetes_cluster_registry=(\n            clusters\n        )\n    )\n\n    assert isinstance(\n        manager.registry.get(\n            "kubernetes"\n        ),\n        MultiClusterKubernetesToolRouter,\n    )\n\n    assert isinstance(\n        manager.registry.get(\n            "kubernetes_change"\n        ),\n        MultiClusterKubernetesChangeToolRouter,\n    )\n\n    assert (\n        manager.registry.get(\n            "kubernetes"\n        ).clusters\n        is clusters\n    )\n\n    assert (\n        manager.registry.get(\n            "kubernetes_change"\n        ).clusters\n        is clusters\n    )\n\n\ndef test_runtime_rejects_invalid_cluster_registry_before_factories(\n    monkeypatch,\n):\n    authentication_calls = 0\n\n    def forbidden_authentication():\n        nonlocal authentication_calls\n        authentication_calls += 1\n        raise AssertionError(\n            "authentication factory must not run"\n        )\n\n    monkeypatch.setattr(\n        runtime_module,\n        "create_authentication_service",\n        forbidden_authentication,\n    )\n\n    with pytest.raises(\n        TypeError,\n        match="cluster registry",\n    ):\n        runtime_module.AgentRuntime(\n            kubernetes_cluster_registry=object(),\n        )\n\n    assert authentication_calls == 0\n\n\ndef test_runtime_passes_explicit_cluster_registry_only_when_opted_in(\n    monkeypatch,\n    tmp_path,\n):\n    monkeypatch.chdir(\n        tmp_path\n    )\n\n    sg, us = cluster_tools()\n\n    clusters = KubernetesClusterRegistry(\n        [\n            sg,\n            us,\n        ]\n    )\n\n    captured = []\n\n    def routed_manager_factory(\n        *,\n        kubernetes_cluster_registry,\n    ):\n        captured.append(\n            kubernetes_cluster_registry\n        )\n\n        registry = ToolRegistry()\n\n        return ToolManager(\n            registry\n        )\n\n    monkeypatch.setattr(\n        runtime_module,\n        "create_tool_manager",\n        routed_manager_factory,\n    )\n\n    monkeypatch.setattr(\n        runtime_module,\n        "create_kubernetes_preflight_resolver",\n        lambda: None,\n    )\n\n    monkeypatch.setattr(\n        runtime_module,\n        "create_kubernetes_production_executor",\n        lambda **_: None,\n    )\n\n    monkeypatch.setattr(\n        runtime_module,\n        "create_production_pilot_live_readiness_probe",\n        lambda: None,\n    )\n\n    runtime = runtime_module.AgentRuntime(\n        authentication_service=(\n            create_authentication_service(\n                AuthenticationConfig()\n            )\n        ),\n        kubernetes_cluster_registry=(\n            clusters\n        ),\n        investigation_settings=(\n            InvestigationSettings()\n        ),\n    )\n\n    assert captured == [\n        clusters\n    ]\n\n    assert (\n        runtime.kubernetes_cluster_registry\n        is clusters\n    )\n\n    assert isinstance(\n        runtime.tools,\n        ToolManager,\n    )\n\n\ndef test_router_module_has_no_write_or_remediation_authority():\n    source = (\n        router_module.__file__\n    )\n\n    assert source is not None\n\n    text = open(\n        source,\n        "r",\n        encoding="utf-8",\n    ).read()\n\n    forbidden = [\n        "ActionRuntime",\n        "ApprovalService",\n        "VerificationRuntime",\n        ".post(",\n        ".patch(",\n        ".put(",\n        ".delete(",\n    ]\n\n    assert [\n        item\n        for item in forbidden\n        if item in text\n    ] == []\n'


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
                f"{relative} changed after the reviewed multi-cluster snapshot. "
                f"expected_sha256={expected} actual_sha256={actual}. "
                "Refusing stale Multi-Cluster Router v1 installation."
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
        / "kubernetes"
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

    test_file = (
        root
        / "services"
        / "agent_runtime"
        / "tests"
        / "test_multi_cluster_kubernetes_router.py"
    )

    sources = {
        router_file: ROUTER_SOURCE,
        factory_file: FACTORY_SOURCE,
        runtime_file: RUNTIME_SOURCE,
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
        "Multi-Cluster Kubernetes Tool Router v1",
        f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
        "",
        "Architecture:",
        "- existing ToolRegistry remains name -> singleton tool",
        "- existing ToolManager call protocol remains unchanged",
        "- multi-cluster mode registers routing tools under the existing names: kubernetes / kubernetes_change",
        "- routing tools select one immutable cluster-bound KubernetesTool by exact scope.cluster",
        "- KubernetesChangeTool for each cluster shares that exact selected KubernetesTool",
        "",
        "Compatibility:",
        "- default create_tool_manager() remains the current single-cluster implementation",
        "- AgentRuntime uses multi-cluster routing only when KubernetesClusterRegistry is explicitly injected",
        "- existing tests that monkeypatch create_tool_manager() with a zero-argument lambda remain compatible",
        "",
        "Fail-closed rules:",
        "- unknown cluster -> reject before child Tool execution",
        "- multiple registered clusters + missing cluster -> reject",
        "- one registered cluster + missing cluster -> single-cluster compatibility",
        "- duplicate cluster -> reject",
        "- KubernetesTool without exact cluster_name -> reject",
        "- registered cluster without API endpoint -> reject",
        "",
        "Authority boundary:",
        "- Router v1 is read-only infrastructure only",
        "- no Production Preflight / Production Executor routing is changed",
        "- no Action / Approval / Verification Runtime authority is added",
        "- no credential parsing or token exposure is added",
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
                "router.py already exists; refusing to overwrite an unreviewed router"
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
                "Multi-Cluster Router v1 syntax failed"
            )

        focused = run_command(
            root=root,
            name="Multi-Cluster Router focused suite",
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
                    "test_production_scope_integrity.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_tools.py"
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
                "Multi-Cluster Router focused tests failed"
            )

        investigation = run_command(
            root=root,
            name="Investigation / Change compatibility suite",
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
                    "test_investigation_production_tool_contract.py"
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
                    "test_investigation_config_change_capability.py"
                ),
                "-q",
            ],
        )

        add_command(
            report,
            investigation,
        )

        if investigation.returncode != 0:
            raise RuntimeError(
                "Multi-Cluster Router Investigation compatibility failed"
            )

        runtime = run_command(
            root=root,
            name="Runtime ownership compatibility suite",
            command=[
                "uv",
                "run",
                "pytest",
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
                    "test_runtime_incident_evidence_recorder_wiring.py"
                ),
                (
                    "services/agent_runtime/tests/"
                    "test_investigation_rca_comparison.py"
                ),
                "-q",
            ],
        )

        add_command(
            report,
            runtime,
        )

        if runtime.returncode != 0:
            raise RuntimeError(
                "Multi-Cluster Router Runtime compatibility failed"
            )

        preflight = run_command(
            root=root,
            name="Router architecture preflight",
            command=[
                "uv",
                "run",
                "python",
                "-c",
                (
                    "from pathlib import Path; "
                    "r=Path(r'services/agent_runtime/app/tools/kubernetes/router.py').read_text(encoding='utf-8'); "
                    "f=Path(r'services/agent_runtime/app/tools/factory.py').read_text(encoding='utf-8'); "
                    "rt=Path(r'services/agent_runtime/app/runtime/runtime.py').read_text(encoding='utf-8'); "
                    "m=Path(r'services/agent_runtime/app/tools/manager.py').read_text(encoding='utf-8'); "
                    "g=Path(r'services/agent_runtime/app/tools/registry.py').read_text(encoding='utf-8'); "
                    "print('cluster_registry='+str('class KubernetesClusterRegistry' in r)); "
                    "print('kubernetes_router='+str('class MultiClusterKubernetesToolRouter' in r)); "
                    "print('change_router='+str('class MultiClusterKubernetesChangeToolRouter' in r)); "
                    "print('factory_opt_in='+str('kubernetes_cluster_registry' in f)); "
                    "print('runtime_opt_in='+str('kubernetes_cluster_registry' in rt)); "
                    "print('manager_unchanged='+str('registry.get' in m)); "
                    "print('registry_singleton_contract='+str('dict[str, BaseTool]' in g)); "
                    "assert 'class KubernetesClusterRegistry' in r; "
                    "assert 'class MultiClusterKubernetesToolRouter' in r; "
                    "assert 'class MultiClusterKubernetesChangeToolRouter' in r; "
                    "assert 'kubernetes_cluster_registry' in f; "
                    "assert 'kubernetes_cluster_registry' in rt; "
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
                "Multi-Cluster Router architecture preflight failed"
            )

        authority = run_command(
            root=root,
            name="Read-only routing authority boundary",
            command=[
                "uv",
                "run",
                "python",
                "-c",
                (
                    "from pathlib import Path; "
                    "r=Path(r'services/agent_runtime/app/tools/kubernetes/router.py').read_text(encoding='utf-8'); "
                    "bad=[x for x in ['ActionRuntime','ApprovalService','VerificationRuntime',"
                    "'.post(','.patch(','.put(','.delete(',"
                    "'kubernetes_production_executor','KubernetesProductionExecutor'] if x in r]; "
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
                "Multi-Cluster Router authority boundary failed"
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
                "Multi-Cluster Kubernetes Tool Router v1 is installed.",
                "",
                "Current production-read capability:",
                "- explicit KubernetesClusterRegistry can hold multiple exact cluster-bound KubernetesTool objects",
                "- Runtime can opt into this registry",
                "- kubernetes reads route by scope.cluster",
                "- kubernetes_change reads route through the same selected cluster client",
                "- unknown/ambiguous cluster selection fails closed",
                "",
                "Still intentionally not implemented:",
                "- environment/YAML multi-cluster credential configuration",
                "- dynamic cluster discovery",
                "- credential rotation",
                "- per-cluster Prometheus endpoint routing",
                "- multi-cluster production write routing",
                "",
                "Next recommended step:",
                "- Multi-Cluster Connection Config / Registry Factory v1, disabled by default, with non-secret cluster descriptors plus per-cluster credential references.",
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
            "MULTI-CLUSTER KUBERNETES TOOL ROUTER V1 PASSED"
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
                    "Multi-Cluster Kubernetes Tool Router v1 FAILED",
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
            "MULTI-CLUSTER KUBERNETES TOOL ROUTER V1 FAILED"
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
