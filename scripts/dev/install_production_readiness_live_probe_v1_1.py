from __future__ import annotations

import hashlib
import shutil
import subprocess
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


VERSION = "production-readiness-live-probe-v1.1"
AFTER_NAME = "production_readiness_live_probe_v1_1_after.txt"
ERROR_NAME = "production_readiness_live_probe_v1_1_error.txt"

EXPECTED_RAW_HASHES = {'services/agent_runtime/app/runtime/runtime.py': '09606a8f640bedd8890a7f435f52852b7448e09ef27f7f260f563e094748f6c9', 'services/agent_runtime/app/investigation/multi_cluster_readiness.py': '8bf79e4823e53560d88ac028d62a02478461360483995fcf0f8dbfc9c7b1174f'}
LIVE_READINESS_SOURCE = 'from __future__ import annotations\n\nimport asyncio\nfrom dataclasses import asdict, dataclass\nfrom typing import Any\n\nfrom services.agent_runtime.app.investigation.multi_cluster_readiness import (\n    ProductionMultiClusterReadinessGate,\n)\nfrom services.agent_runtime.app.tools.manager import ToolManager\n\n\nclass ProductionReadinessLiveProbeError(RuntimeError):\n    """Explicit bounded production-read readiness probe cannot execute safely."""\n\n\n@dataclass(frozen=True, slots=True)\nclass ProductionReadinessLiveProbeReport:\n    schema_version: str\n    read_only: bool\n    decision_influence: bool\n    ready: bool\n    cluster: str | None\n    kubernetes_probe_ready: bool\n    prometheus_probe_ready: bool\n    issues: tuple[str, ...]\n\n    def snapshot(self) -> dict[str, Any]:\n        value = asdict(self)\n        value["issues"] = list(self.issues)\n        return value\n\n\nclass ProductionReadinessLiveProbe:\n    """\n    Explicit bounded proof of live production read reachability.\n\n    It is never invoked automatically. Every execution requires the exact\n    acknowledgement string, a non-empty reason, and a passing static readiness\n    report. Only one Kubernetes Pod GET and one aggregate Prometheus query are\n    attempted. Raw backend payloads and exception text are never returned.\n    """\n\n    ACKNOWLEDGEMENT = "I_ACKNOWLEDGE_PRODUCTION_READINESS_LIVE_READS"\n\n    def __init__(\n        self,\n        *,\n        readiness_gate: ProductionMultiClusterReadinessGate,\n        tools: ToolManager,\n        timeout_seconds: float = 6.0,\n    ) -> None:\n        if not isinstance(readiness_gate, ProductionMultiClusterReadinessGate):\n            raise TypeError("Production live readiness Gate is invalid")\n        if not isinstance(tools, ToolManager):\n            raise TypeError("Production live readiness ToolManager is invalid")\n        if (\n            not isinstance(timeout_seconds, (int, float))\n            or isinstance(timeout_seconds, bool)\n        ):\n            raise TypeError("Production live readiness timeout is invalid")\n\n        normalized_timeout = float(timeout_seconds)\n        if normalized_timeout <= 0 or normalized_timeout > 30:\n            raise ValueError("Production live readiness timeout is out of bounds")\n\n        self.readiness_gate = readiness_gate\n        self.tools = tools\n        self.timeout_seconds = normalized_timeout\n\n    async def probe_event(\n        self,\n        event: Any,\n        *,\n        acknowledgement: str,\n        reason: str,\n    ) -> ProductionReadinessLiveProbeReport:\n        self._validate_operator_intent(\n            acknowledgement=acknowledgement,\n            reason=reason,\n        )\n\n        static_report = self.readiness_gate.evaluate_event(event)\n\n        if not static_report.ready:\n            return ProductionReadinessLiveProbeReport(\n                schema_version="v1",\n                read_only=True,\n                decision_influence=False,\n                ready=False,\n                cluster=static_report.cluster,\n                kubernetes_probe_ready=False,\n                prometheus_probe_ready=False,\n                issues=("static_readiness_not_ready",),\n            )\n\n        cluster, namespace, target, scope_issue = self._event_scope(event)\n\n        if scope_issue is not None:\n            return ProductionReadinessLiveProbeReport(\n                schema_version="v1",\n                read_only=True,\n                decision_influence=False,\n                ready=False,\n                cluster=cluster,\n                kubernetes_probe_ready=False,\n                prometheus_probe_ready=False,\n                issues=(scope_issue,),\n            )\n\n        assert cluster is not None\n        assert namespace is not None\n        assert target is not None\n\n        kubernetes_ready = await self._probe_kubernetes(\n            cluster=cluster,\n            namespace=namespace,\n            target=target,\n        )\n        prometheus_ready = await self._probe_prometheus(cluster=cluster)\n\n        issues = []\n        if not kubernetes_ready:\n            issues.append("kubernetes_live_probe_failed")\n        if not prometheus_ready:\n            issues.append("prometheus_live_probe_failed")\n\n        return ProductionReadinessLiveProbeReport(\n            schema_version="v1",\n            read_only=True,\n            decision_influence=False,\n            ready=(kubernetes_ready and prometheus_ready and not issues),\n            cluster=cluster,\n            kubernetes_probe_ready=kubernetes_ready,\n            prometheus_probe_ready=prometheus_ready,\n            issues=tuple(issues),\n        )\n\n    async def _probe_kubernetes(\n        self,\n        *,\n        cluster: str,\n        namespace: str,\n        target: str,\n    ) -> bool:\n        try:\n            result = await asyncio.wait_for(\n                self.tools.call(\n                    "kubernetes",\n                    action="get",\n                    resource="pod",\n                    target=target,\n                    namespace=namespace,\n                    cluster=cluster,\n                ),\n                timeout=self.timeout_seconds,\n            )\n        except Exception:\n            return False\n\n        return self._valid_result(\n            result,\n            expected_source="kubernetes",\n            expected_cluster=cluster,\n        )\n\n    async def _probe_prometheus(self, *, cluster: str) -> bool:\n        query = (\n            \'count(up{cluster="\'\n            + self._promql_label_value(cluster)\n            + \'"})\'\n        )\n\n        try:\n            result = await asyncio.wait_for(\n                self.tools.call(\n                    "prometheus",\n                    query=query,\n                    cluster=cluster,\n                ),\n                timeout=self.timeout_seconds,\n            )\n        except Exception:\n            return False\n\n        return self._valid_result(\n            result,\n            expected_source="prometheus",\n            expected_cluster=cluster,\n        )\n\n    @staticmethod\n    def _valid_result(\n        result: Any,\n        *,\n        expected_source: str,\n        expected_cluster: str,\n    ) -> bool:\n        if not isinstance(result, dict):\n            return False\n\n        return (\n            result.get("success") is True\n            and result.get("source") == expected_source\n            and result.get("mode") == "read_only"\n            and result.get("production_signal") is True\n            and result.get("cluster") == expected_cluster\n            and isinstance(result.get("data"), dict)\n        )\n\n    @classmethod\n    def _validate_operator_intent(\n        cls,\n        *,\n        acknowledgement: str,\n        reason: str,\n    ) -> None:\n        if acknowledgement != cls.ACKNOWLEDGEMENT:\n            raise ProductionReadinessLiveProbeError(\n                "Production live readiness acknowledgement is invalid"\n            )\n\n        if (\n            not isinstance(reason, str)\n            or not reason.strip()\n            or reason != reason.strip()\n            or len(reason) > 512\n        ):\n            raise ProductionReadinessLiveProbeError(\n                "Production live readiness reason is invalid"\n            )\n\n    @staticmethod\n    def _event_scope(\n        event: Any,\n    ) -> tuple[str | None, str | None, str | None, str | None]:\n        resources = getattr(event, "resources", None)\n\n        if not isinstance(resources, (list, tuple)) or not resources:\n            return (None, None, None, "incident_resource_missing")\n\n        candidates = set()\n\n        for resource in resources:\n            values = (\n                getattr(resource, "cluster", None),\n                getattr(resource, "namespace", None),\n                getattr(resource, "name", None),\n            )\n            if any(\n                not isinstance(item, str)\n                or not item.strip()\n                or item != item.strip()\n                for item in values\n            ):\n                continue\n            candidates.add(values)\n\n        if len(candidates) != 1:\n            return (\n                None,\n                None,\n                None,\n                "incident_resource_missing"\n                if not candidates\n                else "incident_resource_ambiguous",\n            )\n\n        cluster, namespace, target = next(iter(candidates))\n        return (cluster, namespace, target, None)\n\n    @staticmethod\n    def _promql_label_value(value: str) -> str:\n        return (\n            value.replace("\\\\", "\\\\\\\\")\n            .replace(\'"\', \'\\\\"\')\n            .replace("\\n", "\\\\n")\n            .replace("\\r", "\\\\r")\n        )\n\n\n__all__ = [\n    "ProductionReadinessLiveProbe",\n    "ProductionReadinessLiveProbeError",\n    "ProductionReadinessLiveProbeReport",\n]\n'
RUNTIME_SOURCE = 'from copy import deepcopy\nfrom typing import Any\n\nfrom services.agent_runtime.app.registry.factory import (\n    create_agent_registry,\n)\nfrom services.agent_runtime.app.llm.gateway.factory import (\n    create_llm_gateway,\n)\nfrom services.agent_runtime.app.llm.gateway.gateway import (\n    LLMGateway,\n)\nfrom services.agent_runtime.app.planner.agent_planner import (\n    AgentPlanner,\n)\nfrom services.agent_runtime.app.pipeline.planner_pipeline import (\n    PlannerPipeline,\n)\nfrom services.agent_runtime.app.memory.store import (\n    MemoryStore,\n)\nfrom services.agent_runtime.app.tools.factory import (\n    create_tool_manager,\n)\nfrom services.agent_runtime.app.tools.kubernetes.router import (\n    KubernetesClusterRegistry,\n)\nfrom services.agent_runtime.app.tools.kubernetes.connection_factory import (\n    create_kubernetes_cluster_registry,\n)\nfrom services.agent_runtime.app.tools.prometheus.router import (\n    PrometheusClusterRegistry,\n)\nfrom services.agent_runtime.app.tools.prometheus.connection_factory import (\n    create_prometheus_cluster_registry,\n)\nfrom services.agent_runtime.app.skills.factory import (\n    create_skill_registry,\n)\nfrom services.agent_runtime.app.mcp.factory import (\n    create_mcp_registry,\n)\nfrom services.agent_runtime.app.observability.collector import (\n    TraceCollector,\n)\nfrom services.agent_runtime.app.evaluation.factory import (\n    create_evaluation_registry,\n)\nfrom services.agent_runtime.app.policy.factory import (\n    create_policy_engine,\n)\nfrom services.agent_runtime.app.approval.service import (\n    ApprovalService,\n)\nfrom services.agent_runtime.app.incident.store import (\n    IncidentStore,\n)\nfrom services.agent_runtime.app.incident.service import (\n    IncidentService,\n)\nfrom services.agent_runtime.app.investigation.comparison import (\n    build_rca_investigation_comparison,\n)\nfrom services.agent_runtime.app.investigation.factory import (\n    create_investigation_coordinator,\n)\nfrom services.agent_runtime.app.investigation.llm_gateway_adapter import (\n    InvestigationLLMGatewayAdapter,\n)\nfrom services.agent_runtime.app.investigation.reasoner import (\n    BaseInvestigationReasoner,\n    LLMInvestigationReasoner,\n)\nfrom services.agent_runtime.app.investigation.settings import (\n    InvestigationSettings,\n)\nfrom services.agent_runtime.app.investigation.multi_cluster_readiness import (\n    ProductionMultiClusterReadinessError,\n    ProductionMultiClusterReadinessGate,\n)\nfrom services.agent_runtime.app.investigation.live_readiness import (\n    ProductionReadinessLiveProbe,\n    ProductionReadinessLiveProbeError,\n)\nfrom services.agent_runtime.app.investigation.models import (\n    InvestigationState,\n)\nfrom services.agent_runtime.app.model.context import (\n    AgentContext,\n)\nfrom services.agent_runtime.app.workflow.service import (\n    WorkflowService,\n)\nfrom services.agent_runtime.app.action.execution_service import (\n    ActionExecutionService,\n)\nfrom services.agent_runtime.app.action.execution_store import (\n    ActionExecutionStore,\n)\nfrom services.agent_runtime.app.action.kubernetes_preflight import (\n    KubernetesPreflightResolver,\n)\nfrom services.agent_runtime.app.action.kubernetes_preflight_factory import (\n    create_kubernetes_preflight_resolver,\n)\nfrom services.agent_runtime.app.action.kubernetes_production_executor import (\n    KubernetesProductionExecutor,\n)\nfrom services.agent_runtime.app.action.kubernetes_production_factory import (\n    create_kubernetes_production_executor,\n)\nfrom services.agent_runtime.app.action.preflight_artifact_service import (\n    PreflightArtifactService,\n)\nfrom services.agent_runtime.app.action.preflight_artifact_store import (\n    PreflightArtifactStore,\n)\nfrom services.agent_runtime.app.action.production_action_preparation import (\n    ProductionActionPreparationService,\n)\nfrom services.agent_runtime.app.action.production_action_query import (\n    ProductionActionQueryService,\n)\nfrom services.agent_runtime.app.action.production_action_guard import (\n    ProductionActionExpiryGuard,\n)\nfrom services.agent_runtime.app.action.production_pilot import (\n    KubernetesProductionPilotControl,\n    ProductionPilotReadinessService,\n)\nfrom services.agent_runtime.app.action.production_pilot_factory import (\n    create_kubernetes_production_pilot_control,\n)\nfrom services.agent_runtime.app.action.production_pilot_budget_service import (\n    ProductionPilotBudgetService,\n)\nfrom services.agent_runtime.app.action.production_pilot_budget_store import (\n    ProductionPilotBudgetStore,\n)\nfrom services.agent_runtime.app.action.production_pilot_rehearsal import (\n    ProductionPilotRehearsalService,\n)\nfrom services.agent_runtime.app.action.production_pilot_crash_rehearsal import (\n    ProductionPilotCrashRecoveryRehearsalService,\n)\nfrom services.agent_runtime.app.action.production_pilot_pre_enable_evidence import (\n    ProductionPilotPreEnableEvidenceService,\n)\nfrom services.agent_runtime.app.action.production_pilot_final_handoff import (\n    ProductionPilotFinalHandoffRehearsalService,\n)\nfrom services.agent_runtime.app.action.production_pilot_live_probe import (\n    ProductionPilotLiveReadinessProbe,\n    create_production_pilot_live_readiness_probe,\n)\nfrom services.agent_runtime.app.action.production_pilot_go_no_go_service import (\n    ProductionPilotGoNoGoService,\n)\nfrom services.agent_runtime.app.action.production_pilot_go_no_go_store import (\n    ProductionPilotGoNoGoStore,\n)\nfrom services.agent_runtime.app.action.production_pilot_ceremony_service import (\n    ProductionPilotCeremonyService,\n)\nfrom services.agent_runtime.app.action.production_pilot_ceremony_store import (\n    ProductionPilotCeremonyStore,\n)\nfrom services.agent_runtime.app.verification.collector import (\n    VerificationEvidenceCollector,\n)\nfrom services.agent_runtime.app.verification.coordinator import (\n    VerificationCoordinator,\n)\nfrom services.agent_runtime.app.verification.profiles import (\n    VerificationProfileFactory,\n)\nfrom services.agent_runtime.app.verification.service import (\n    VerificationService,\n)\nfrom services.agent_runtime.app.verification.store import (\n    VerificationStore,\n)\nfrom services.agent_runtime.app.runtime.action_runtime import (\n    ActionRuntime,\n)\nfrom services.agent_runtime.app.runtime.verification_runtime import (\n    VerificationRuntime,\n)\nfrom services.agent_runtime.app.security.factory import (\n    create_authentication_service,\n)\nfrom services.agent_runtime.app.security.policy import (\n    SecurityPolicyEngine,\n)\nfrom services.agent_runtime.app.security.service import (\n    AuthenticationService,\n)\nfrom services.sandbox.executor.local import (\n    LocalSandboxExecutor,\n)\nfrom services.sandbox.policy.validator import (\n    SandboxPolicyValidator,\n)\n\n\nfrom services.agent_runtime.app.incident_evidence.recorder import (\n    ProductionIncidentEvidenceRecorder,\n)\nfrom services.agent_runtime.app.incident_evidence.settings import (\n    IncidentEvidenceRecorderSettings,\n)\n\nclass AgentRuntime:\n    """\n    Runtime container.\n\n    Owns and shares security and runtime infrastructure\n    across Pipeline, Action and Verification.\n\n    security_policy is the RBAC authorization policy. The existing policy\n    attribute remains the remediation business policy engine.\n    """\n\n    def __init__(\n        self,\n        authentication_service: (\n            AuthenticationService | None\n        ) = None,\n        security_policy: (\n            SecurityPolicyEngine | None\n        ) = None,\n        kubernetes_preflight: (\n            KubernetesPreflightResolver | None\n        ) = None,\n        kubernetes_production_executor: (\n            KubernetesProductionExecutor | None\n        ) = None,\n        production_pilot_control: (\n            KubernetesProductionPilotControl | None\n        ) = None,\n        production_pilot_budget_service: (\n            ProductionPilotBudgetService | None\n        ) = None,\n        production_pilot_live_probe: (\n            ProductionPilotLiveReadinessProbe | None\n        ) = None,\n        kubernetes_cluster_registry: (\n            KubernetesClusterRegistry | None\n        ) = None,\n        prometheus_cluster_registry: (\n            PrometheusClusterRegistry | None\n        ) = None,\n        llm_gateway: (\n            LLMGateway | None\n        ) = None,\n        investigation_reasoner: (\n            BaseInvestigationReasoner | None\n        ) = None,\n        investigation_settings: (\n            InvestigationSettings | None\n        ) = None,\n    ) -> None:\n        # Validate every injected security component before factories, stores\n        # or other runtime components can produce side effects.\n        if (\n            authentication_service is not None\n            and not isinstance(\n                authentication_service,\n                AuthenticationService,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime authentication service is invalid"\n            )\n\n        if (\n            security_policy is not None\n            and not isinstance(\n                security_policy,\n                SecurityPolicyEngine,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime security policy is invalid"\n            )\n\n        if (\n            kubernetes_preflight is not None\n            and not isinstance(\n                kubernetes_preflight,\n                KubernetesPreflightResolver,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Kubernetes preflight resolver is invalid"\n            )\n\n        if (\n            kubernetes_production_executor is not None\n            and not isinstance(\n                kubernetes_production_executor,\n                KubernetesProductionExecutor,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Kubernetes production executor is invalid"\n            )\n\n        if (\n            production_pilot_control is not None\n            and not isinstance(\n                production_pilot_control,\n                KubernetesProductionPilotControl,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Kubernetes production pilot control is invalid"\n            )\n\n        if (\n            production_pilot_budget_service is not None\n            and not isinstance(\n                production_pilot_budget_service,\n                ProductionPilotBudgetService,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Kubernetes production pilot budget service is invalid"\n            )\n\n        if (\n            production_pilot_live_probe is not None\n            and not isinstance(\n                production_pilot_live_probe,\n                ProductionPilotLiveReadinessProbe,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Production Pilot live probe is invalid"\n            )\n\n        if (\n            kubernetes_cluster_registry is not None\n            and not isinstance(\n                kubernetes_cluster_registry,\n                KubernetesClusterRegistry,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Kubernetes cluster registry is invalid"\n            )\n\n        if (\n            prometheus_cluster_registry is not None\n            and not isinstance(\n                prometheus_cluster_registry,\n                PrometheusClusterRegistry,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Prometheus cluster registry is invalid"\n            )\n\n        if (\n            llm_gateway is not None\n            and not isinstance(\n                llm_gateway,\n                LLMGateway,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime shared LLM gateway is invalid"\n            )\n\n        if (\n            investigation_reasoner is not None\n            and not isinstance(\n                investigation_reasoner,\n                BaseInvestigationReasoner,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Investigation reasoner is invalid"\n            )\n\n        if (\n            investigation_settings is not None\n            and not isinstance(\n                investigation_settings,\n                InvestigationSettings,\n            )\n        ):\n            raise TypeError(\n                "AgentRuntime Investigation settings are invalid"\n            )\n\n        # Resolve disabled-default Investigation configuration before any\n        # Runtime store, tool, credential, network or LLM component is created.\n        self.investigation_settings = (\n            investigation_settings\n            if investigation_settings is not None\n            else InvestigationSettings.from_environment()\n        )\n\n        investigation_shared_gateway = None\n\n        # An enabled LLM-backed Investigation must use the exact shared\n        # LLMGateway instance that AgentRuntime will provide to its Agents.\n        #\n        # Disabled Investigation deliberately does not inspect or touch the\n        # supplied reasoner\'s LLM adapter.\n        if (\n            self.investigation_settings.enabled\n            and isinstance(\n                investigation_reasoner,\n                LLMInvestigationReasoner,\n            )\n        ):\n            investigation_llm = (\n                investigation_reasoner.investigation_llm\n            )\n\n            if not isinstance(\n                investigation_llm,\n                InvestigationLLMGatewayAdapter,\n            ):\n                raise TypeError(\n                    "AgentRuntime LLM Investigation requires "\n                    "InvestigationLLMGatewayAdapter"\n                )\n\n            investigation_shared_gateway = (\n                investigation_llm.llm_gateway\n            )\n\n            if not isinstance(\n                investigation_shared_gateway,\n                LLMGateway,\n            ):\n                raise TypeError(\n                    "AgentRuntime Investigation shared LLM gateway is invalid"\n                )\n\n            if (\n                llm_gateway is not None\n                and investigation_shared_gateway\n                is not llm_gateway\n            ):\n                raise TypeError(\n                    "AgentRuntime Investigation LLM gateway must be shared"\n                )\n\n        # Preserve the existing fail-closed Investigation assembly boundary.\n        # Enabled mode without an explicit reasoner still fails here before\n        # any Runtime or LLM infrastructure is constructed.\n        self.investigation_coordinator = (\n            create_investigation_coordinator(\n                reasoner=investigation_reasoner,\n                settings=self.investigation_settings,\n            )\n        )\n\n        # Do not construct a default Gateway yet. Keeping this unresolved\n        # preserves the previous initialization order. If Investigation\n        # already carries the approved Gateway Adapter, Runtime adopts that\n        # exact Gateway object as its shared instance.\n        self.llm_gateway = (\n            llm_gateway\n            if llm_gateway is not None\n            else investigation_shared_gateway\n        )\n\n        self.authentication = (\n            authentication_service\n            if authentication_service is not None\n            else create_authentication_service()\n        )\n\n        self.security_policy = (\n            security_policy\n            if security_policy is not None\n            else SecurityPolicyEngine()\n        )\n\n        self.kubernetes_preflight = (\n            kubernetes_preflight\n            if kubernetes_preflight is not None\n            else create_kubernetes_preflight_resolver()\n        )\n\n        self.production_pilot_control = (\n            production_pilot_control\n            if production_pilot_control is not None\n            else create_kubernetes_production_pilot_control()\n        )\n\n        # This independent gate may read both credential values at startup,\n        # but can construct only a two-GET probe. Disabled mode returns before\n        # any credential or CA access.\n        self.production_pilot_live_probe = (\n            production_pilot_live_probe\n            if production_pilot_live_probe is not None\n            else create_production_pilot_live_readiness_probe()\n        )\n\n        self.production_pilot_budget_store = None\n        self.production_pilot_budget_service = (\n            production_pilot_budget_service\n        )\n        if (\n            self.production_pilot_budget_service is None\n            and self.production_pilot_control.config.enabled\n        ):\n            self.production_pilot_budget_store = (\n                ProductionPilotBudgetStore()\n            )\n            self.production_pilot_budget_service = (\n                ProductionPilotBudgetService(\n                    store=(\n                        self.production_pilot_budget_store\n                    )\n                )\n            )\n\n        self.kubernetes_production_executor = (\n            kubernetes_production_executor\n            if kubernetes_production_executor is not None\n            else create_kubernetes_production_executor(\n                pilot_control=(\n                    self.production_pilot_control\n                ),\n                pilot_budget_service=(\n                    self.production_pilot_budget_service\n                ),\n            )\n        )\n\n        if self.kubernetes_production_executor is not None:\n            executor_control = getattr(\n                self.kubernetes_production_executor,\n                "pilot_control",\n                None,\n            )\n            if executor_control is None:\n                self.kubernetes_production_executor.pilot_control = (\n                    self.production_pilot_control\n                )\n            elif executor_control is not self.production_pilot_control:\n                raise TypeError(\n                    "AgentRuntime Kubernetes production pilot control must be shared"\n                )\n            executor_budget = getattr(\n                self.kubernetes_production_executor,\n                "pilot_budget_service",\n                None,\n            )\n            if executor_budget is None:\n                if self.production_pilot_budget_service is None:\n                    raise TypeError(\n                        "AgentRuntime Kubernetes production pilot budget is unavailable"\n                    )\n                self.kubernetes_production_executor.pilot_budget_service = (\n                    self.production_pilot_budget_service\n                )\n            elif executor_budget is not self.production_pilot_budget_service:\n                raise TypeError(\n                    "AgentRuntime Kubernetes production pilot budget must be shared"\n                )\n\n        if (\n            self.kubernetes_production_executor is not None\n            and self.kubernetes_preflight is None\n        ):\n            raise TypeError(\n                "AgentRuntime Kubernetes production executor requires "\n                "trusted preflight"\n            )\n\n        self.production_pilot_readiness = (\n            ProductionPilotReadinessService(\n                control=(\n                    self.production_pilot_control\n                ),\n                production_executor_configured=(\n                    self.kubernetes_production_executor\n                    is not None\n                ),\n            )\n        )\n        self.production_pilot_rehearsal = (\n            ProductionPilotRehearsalService(\n                control=(\n                    self.production_pilot_control\n                ),\n                budget_service=(\n                    self.production_pilot_budget_service\n                ),\n                production_executor_configured=(\n                    self.kubernetes_production_executor\n                    is not None\n                ),\n            )\n        )\n        # Pure recovery-policy proof. It owns no store, credential, network\n        # client or executor and is available while the production gate is\n        # disabled so operators can rehearse recovery before enablement.\n        self.production_pilot_crash_recovery_rehearsal = (\n            ProductionPilotCrashRecoveryRehearsalService()\n        )\n\n        self.memory = MemoryStore()\n\n        if (\n            kubernetes_cluster_registry\n            is None\n        ):\n            self.kubernetes_cluster_registry = (\n                create_kubernetes_cluster_registry()\n            )\n        else:\n            self.kubernetes_cluster_registry = (\n                kubernetes_cluster_registry\n            )\n\n        if (\n            prometheus_cluster_registry\n            is None\n        ):\n            self.prometheus_cluster_registry = (\n                create_prometheus_cluster_registry()\n            )\n        else:\n            self.prometheus_cluster_registry = (\n                prometheus_cluster_registry\n            )\n\n        self.cluster_verified_evidence_required = (\n            self.kubernetes_cluster_registry\n            is not None\n            or self.prometheus_cluster_registry\n            is not None\n        )\n\n        if (\n            self.investigation_coordinator\n            is not None\n        ):\n            self.investigation_coordinator.require_cluster_verified_evidence = (\n                self.cluster_verified_evidence_required\n            )\n\n        tool_manager_kwargs = {}\n\n        if (\n            self.kubernetes_cluster_registry\n            is not None\n        ):\n            tool_manager_kwargs[\n                "kubernetes_cluster_registry"\n            ] = self.kubernetes_cluster_registry\n\n        if (\n            self.prometheus_cluster_registry\n            is not None\n        ):\n            tool_manager_kwargs[\n                "prometheus_cluster_registry"\n            ] = self.prometheus_cluster_registry\n\n        if tool_manager_kwargs:\n            self.tools = create_tool_manager(\n                **tool_manager_kwargs\n            )\n        else:\n            self.tools = create_tool_manager()\n\n        readiness_registry_types_valid = (\n            (\n                self.kubernetes_cluster_registry\n                is None\n                or isinstance(\n                    self.kubernetes_cluster_registry,\n                    KubernetesClusterRegistry,\n                )\n            )\n            and (\n                self.prometheus_cluster_registry\n                is None\n                or isinstance(\n                    self.prometheus_cluster_registry,\n                    PrometheusClusterRegistry,\n                )\n            )\n        )\n\n        self.production_multi_cluster_readiness = None\n        self.production_multi_cluster_coverage = None\n\n        self.production_multi_cluster_live_readiness = None\n\n        if readiness_registry_types_valid:\n            self.production_multi_cluster_readiness = (\n                ProductionMultiClusterReadinessGate(\n                    kubernetes_cluster_registry=(\n                        self.kubernetes_cluster_registry\n                    ),\n                    prometheus_cluster_registry=(\n                        self.prometheus_cluster_registry\n                    ),\n                    tools=self.tools,\n                    strict_evidence_required=(\n                        self.cluster_verified_evidence_required\n                    ),\n                )\n            )\n\n            self.production_multi_cluster_coverage = (\n                self.production_multi_cluster_readiness\n                .evaluate_all()\n            )\n\n            if (\n                self.production_multi_cluster_readiness\n                .applicable\n            ):\n                self.production_multi_cluster_live_readiness = (\n                    ProductionReadinessLiveProbe(\n                        readiness_gate=(\n                            self.production_multi_cluster_readiness\n                        ),\n                        tools=self.tools,\n                    )\n                )\n\n        self.skills = create_skill_registry()\n        self.mcp = create_mcp_registry()\n        self.tracer = TraceCollector()\n        self.evaluators = create_evaluation_registry()\n\n        # Remediation business policy. This is intentionally separate from\n        # security_policy, which authorizes operator-facing operations.\n        self.policy = create_policy_engine()\n\n        self.preflight_artifact_store = None\n        self.preflight_artifact_service = None\n        self.production_action_guard = None\n        self.production_action_preparation = None\n        self.production_action_query = None\n\n        if self.kubernetes_preflight is not None:\n            self.preflight_artifact_store = PreflightArtifactStore()\n            self.preflight_artifact_service = PreflightArtifactService(\n                store=self.preflight_artifact_store\n            )\n            self.production_action_guard = (\n                ProductionActionExpiryGuard(\n                    artifact_service=(\n                        self.preflight_artifact_service\n                    )\n                )\n            )\n\n        self.approval = ApprovalService()\n\n        if self.production_action_guard is not None:\n            self.approval.manager.set_transition_guard(\n                self.production_action_guard\n            )\n\n        if self.preflight_artifact_service is not None:\n            self.production_action_preparation = (\n                ProductionActionPreparationService(\n                    resolver=self.kubernetes_preflight,\n                    artifact_service=self.preflight_artifact_service,\n                    approval_service=self.approval,\n                )\n            )\n\n        self.production_pilot_ceremony_store = None\n        self.production_pilot_ceremony = None\n        if (\n            self.production_pilot_control.config.enabled\n            and self.production_pilot_budget_service is not None\n            and self.preflight_artifact_service is not None\n        ):\n            self.production_pilot_ceremony_store = (\n                ProductionPilotCeremonyStore()\n            )\n            self.production_pilot_ceremony = (\n                ProductionPilotCeremonyService(\n                    store=(\n                        self.production_pilot_ceremony_store\n                    ),\n                    control=(\n                        self.production_pilot_control\n                    ),\n                    rehearsal=(\n                        self.production_pilot_rehearsal\n                    ),\n                    budget_service=(\n                        self.production_pilot_budget_service\n                    ),\n                    approval_service=self.approval,\n                    artifact_service=(\n                        self.preflight_artifact_service\n                    ),\n                )\n            )\n\n        self.incident_store = IncidentStore()\n\n        if self.preflight_artifact_service is not None:\n            self.production_action_query = (\n                ProductionActionQueryService(\n                    artifact_service=(\n                        self.preflight_artifact_service\n                    ),\n                    approval_service=self.approval,\n                    incident_store=self.incident_store,\n                )\n            )\n\n        self.incident_service = IncidentService(\n            store=self.incident_store\n        )\n\n        self.workflow_service = WorkflowService(\n            incident_service=self.incident_service\n        )\n\n        self.action_execution_store = ActionExecutionStore()\n\n        self.action_execution_service = ActionExecutionService(\n            store=self.action_execution_store\n        )\n\n        self.action_runtime = ActionRuntime(\n            approval_service=self.approval,\n            incident_store=self.incident_store,\n            action_execution_service=self.action_execution_service,\n            production_action_guard=(\n                self.production_action_guard\n            ),\n            kubernetes_production_executor=(\n                self.kubernetes_production_executor\n            ),\n            preflight_artifact_service=(\n                self.preflight_artifact_service\n                if self.kubernetes_production_executor is not None\n                else None\n            ),\n            production_pilot_control=(\n                self.production_pilot_control\n            ),\n            production_pilot_budget_service=(\n                self.production_pilot_budget_service\n            ),\n            production_pilot_ceremony_service=(\n                self.production_pilot_ceremony\n                if self.kubernetes_production_executor is not None\n                else None\n            ),\n        )\n\n        self.verification_store = VerificationStore()\n\n        self.verification = VerificationService(\n            store=self.verification_store\n        )\n\n        self.verification_runtime = VerificationRuntime(\n            verification_service=self.verification,\n            incident_store=self.incident_store,\n        )\n\n        self.verification_profile_factory = VerificationProfileFactory()\n\n        self.verification_collector = VerificationEvidenceCollector(\n            tools=self.tools,\n            require_cluster_verified_evidence=(\n                self.cluster_verified_evidence_required\n            ),\n        )\n\n        self.verification_coordinator = VerificationCoordinator(\n            profile_factory=self.verification_profile_factory,\n            collector=self.verification_collector,\n            verification_runtime=self.verification_runtime,\n        )\n\n        # Final pre-enable evidence is assembled only when every production\n        # preparation component is available. The service is read-only and\n        # deliberately owns no executor or mutable workflow operation.\n        self.production_pilot_pre_enable_evidence = None\n        if all(\n            component is not None\n            for component in (\n                self.production_pilot_ceremony,\n                self.production_pilot_budget_service,\n                self.preflight_artifact_service,\n            )\n        ):\n            self.production_pilot_pre_enable_evidence = (\n                ProductionPilotPreEnableEvidenceService(\n                    readiness_service=(\n                        self.production_pilot_readiness\n                    ),\n                    rehearsal_service=(\n                        self.production_pilot_rehearsal\n                    ),\n                    crash_rehearsal_service=(\n                        self.production_pilot_crash_recovery_rehearsal\n                    ),\n                    ceremony_service=(\n                        self.production_pilot_ceremony\n                    ),\n                    budget_service=(\n                        self.production_pilot_budget_service\n                    ),\n                    artifact_service=(\n                        self.preflight_artifact_service\n                    ),\n                    approval_service=self.approval,\n                    incident_store=self.incident_store,\n                    action_execution_service=(\n                        self.action_execution_service\n                    ),\n                    verification_service=self.verification,\n                )\n            )\n\n        # The final handoff rehearsal is also strictly read-only. It is\n        # available only with the full prepared Pilot chain and explicitly\n        # records whether production executors remain absent while the gate\n        # is disabled.\n        self.production_pilot_final_handoff_rehearsal = None\n        if self.production_pilot_pre_enable_evidence is not None:\n            self.production_pilot_final_handoff_rehearsal = (\n                ProductionPilotFinalHandoffRehearsalService(\n                    pilot_control=self.production_pilot_control,\n                    pre_enable_evidence_service=(\n                        self.production_pilot_pre_enable_evidence\n                    ),\n                    preflight_resolver=self.kubernetes_preflight,\n                    production_executor_configured=(\n                        self.kubernetes_production_executor is not None\n                    ),\n                    action_runtime_production_executor_configured=(\n                        getattr(\n                            self.action_runtime,\n                            "kubernetes_production_executor",\n                            None,\n                        )\n                        is not None\n                    ),\n                )\n            )\n\n        # A dedicated database is created only when the separately gated live\n        # probe exists and the full zero-write handoff chain is available.\n        self.production_pilot_go_no_go_store = None\n        self.production_pilot_go_no_go = None\n        if (\n            self.production_pilot_live_probe is not None\n            and self.production_pilot_final_handoff_rehearsal is not None\n            and self.preflight_artifact_service is not None\n        ):\n            self.production_pilot_go_no_go_store = (\n                ProductionPilotGoNoGoStore()\n            )\n            self.production_pilot_go_no_go = (\n                ProductionPilotGoNoGoService(\n                    store=self.production_pilot_go_no_go_store,\n                    live_probe=self.production_pilot_live_probe,\n                    final_handoff_service=(\n                        self.production_pilot_final_handoff_rehearsal\n                    ),\n                    artifact_service=self.preflight_artifact_service,\n                    pilot_control=self.production_pilot_control,\n                )\n            )\n\n        self.sandbox = LocalSandboxExecutor()\n\n        self.sandbox_policy = SandboxPolicyValidator()\n\n        if self.llm_gateway is None:\n            self.llm_gateway = create_llm_gateway()\n\n        self.registry = create_agent_registry(\n            llm_gateway=self.llm_gateway,\n        )\n\n        self.planner = AgentPlanner()\n\n        self.pipeline = PlannerPipeline(\n            self.registry,\n            self.planner,\n            self.tracer,\n            self.evaluators,\n            incident_store=self.incident_store,\n            incident_service=self.incident_service,\n            workflow_service=self.workflow_service,\n        )\n\n    async def execute(\n        self,\n        context: AgentContext,\n    ):\n        """\n        Execute the primary PlannerPipeline and, when explicitly enabled,\n        run Investigation automatically as a best-effort Shadow.\n\n        Ordering is deliberate:\n\n        1. PlannerPipeline completes first.\n        2. Investigation receives an isolated AgentContext.\n        3. Only the bounded investigation_shadow snapshot is copied back.\n\n        Investigation can never change the Pipeline result, Incident,\n        variables, results, trace, Approval, executions or evaluations.\n\n        Investigation orchestration failure is sanitized and recorded in\n        metadata without failing an otherwise successful Pipeline execution.\n        """\n\n        if not isinstance(\n            context,\n            AgentContext,\n        ):\n            raise TypeError(\n                "AgentRuntime execution context is invalid"\n            )\n\n        # Reserved Shadow metadata from a previous execution must never be\n        # visible to the primary Pipeline, even when this Runtime currently\n        # has Investigation disabled.\n        for reserved_key in (\n            "investigation_shadow",\n            "investigation_shadow_orchestration",\n            "investigation_rca_comparison",\n            "production_multi_cluster_readiness",\n            "production_multi_cluster_live_readiness",\n        ):\n            context.metadata.pop(\n                reserved_key,\n                None,\n            )\n\n        # Primary workflow semantics remain authoritative. Pipeline failure\n        # propagates normally and Investigation is not attempted afterward.\n        context.metadata.pop(\n            "incident_evidence_recorder",\n            None,\n        )\n\n        results = await self.pipeline.execute(\n            context\n        )\n\n        # Evidence Recorder is evaluation-only and best-effort.\n        await self._record_incident_evidence_shadow(\n            context\n        )\n\n        if self.investigation_coordinator is None:\n            return results\n\n        shadow_context = (\n            self._create_investigation_shadow_context(\n                context\n            )\n        )\n\n        try:\n            await self.run_investigation_shadow(\n                shadow_context\n            )\n\n            readiness_snapshot = (\n                shadow_context.metadata.get(\n                    "production_multi_cluster_readiness"\n                )\n            )\n\n            if isinstance(\n                readiness_snapshot,\n                dict,\n            ):\n                context.metadata[\n                    "production_multi_cluster_readiness"\n                ] = deepcopy(\n                    readiness_snapshot\n                )\n\n            snapshot = shadow_context.metadata.get(\n                "investigation_shadow"\n            )\n\n            if (\n                not isinstance(\n                    snapshot,\n                    dict,\n                )\n                or snapshot.get(\n                    "shadow_mode"\n                )\n                is not True\n                or snapshot.get(\n                    "read_only"\n                )\n                is not True\n            ):\n                raise RuntimeError(\n                    "Investigation Shadow snapshot is invalid"\n                )\n\n            context.metadata[\n                "investigation_shadow"\n            ] = deepcopy(\n                snapshot\n            )\n\n        except Exception as exc:\n            # Shadow means Shadow: an Investigation orchestration fault must\n            # never convert a successful PlannerPipeline execution to failed.\n            #\n            # Raw exception text is deliberately excluded because provider,\n            # URL, credential or tool details may be present in it.\n            readiness_snapshot = (\n                shadow_context.metadata.get(\n                    "production_multi_cluster_readiness"\n                )\n            )\n\n            if isinstance(\n                readiness_snapshot,\n                dict,\n            ):\n                context.metadata[\n                    "production_multi_cluster_readiness"\n                ] = deepcopy(\n                    readiness_snapshot\n                )\n\n            context.metadata[\n                "investigation_shadow_orchestration"\n            ] = {\n                "shadow_mode": True,\n                "read_only": True,\n                "automatic": True,\n                "status": "failed",\n                "failure_code": (\n                    type(exc).__name__[:256]\n                ),\n            }\n\n        # Comparison is evaluation-only. It cannot change the authoritative\n        # RCA stored in context.variables["rca"] and has no Healing authority.\n        try:\n            context.metadata[\n                "investigation_rca_comparison"\n            ] = build_rca_investigation_comparison(\n                rca=context.variables.get(\n                    "rca"\n                ),\n                investigation_snapshot=(\n                    context.metadata.get(\n                        "investigation_shadow"\n                    )\n                ),\n                orchestration_snapshot=(\n                    context.metadata.get(\n                        "investigation_shadow_orchestration"\n                    )\n                ),\n            )\n        except Exception as exc:\n            # A comparison bug must remain weaker than Shadow itself and must\n            # never fail a successful primary Pipeline.\n            context.metadata[\n                "investigation_rca_comparison"\n            ] = {\n                "schema_version": "v1",\n                "shadow_mode": True,\n                "read_only": True,\n                "decision_influence": False,\n                "available": False,\n                "comparison_status": (\n                    "comparison_failed"\n                ),\n                "failure_code": (\n                    type(exc).__name__[:256]\n                ),\n            }\n\n        return results\n\n    def _create_investigation_shadow_context(\n        self,\n        context: AgentContext,\n    ) -> AgentContext:\n        """\n        Build the minimum-privilege context for automatic Investigation.\n\n        Copied:\n        - event input\n        - request correlation ID\n\n        Shared:\n        - exact Runtime-owned ToolManager\n\n        Deliberately not shared:\n        - Incident\n        - variables\n        - results\n        - metadata\n        - trace\n        - memory\n        - skills\n        - MCP\n        - sandbox\n        - Approval\n        - executions\n        - evaluations\n        """\n\n        return AgentContext(\n            request_id=context.request_id,\n            event=deepcopy(\n                context.event\n            ),\n            tools=self.tools,\n            metadata={},\n        )\n\n    async def run_production_multi_cluster_live_readiness(\n        self,\n        context: AgentContext,\n        *,\n        acknowledgement: str,\n        reason: str,\n    ) -> dict[str, Any]:\n        """\n        Explicit bounded live-read production readiness proof.\n\n        This method is never called automatically by execute() or Runtime\n        startup. It records only a sanitized readiness snapshot.\n        """\n\n        if not isinstance(context, AgentContext):\n            raise TypeError(\n                "AgentRuntime live readiness requires AgentContext"\n            )\n\n        if context.tools is not self.tools:\n            raise TypeError(\n                "AgentRuntime live readiness requires shared Runtime tools"\n            )\n\n        probe = getattr(\n            self,\n            "production_multi_cluster_live_readiness",\n            None,\n        )\n\n        if probe is None:\n            raise ProductionReadinessLiveProbeError(\n                "AgentRuntime production live readiness is unavailable"\n            )\n\n        report = await probe.probe_event(\n            context.event,\n            acknowledgement=acknowledgement,\n            reason=reason,\n        )\n\n        snapshot = report.snapshot()\n\n        context.metadata[\n            "production_multi_cluster_live_readiness"\n        ] = deepcopy(snapshot)\n\n        return snapshot\n\n    async def run_investigation_shadow(\n        self,\n        context: AgentContext,\n    ) -> InvestigationState:\n        """\n        Explicitly execute the enabled read-only Investigation Shadow.\n\n        This method is intentionally separate from PlannerPipeline.\n\n        PlannerPipeline itself never invokes Investigation. AgentRuntime\n        may call this lower-level entry point after a successful Pipeline\n        execution when automatic Shadow Investigation is enabled.\n\n        The supplied AgentContext must use the exact Runtime ToolManager so\n        Investigation probes cannot bypass Runtime-owned tool boundaries.\n        """\n\n        if not isinstance(\n            context,\n            AgentContext,\n        ):\n            raise TypeError(\n                "AgentRuntime Investigation Shadow context is invalid"\n            )\n\n        if self.investigation_coordinator is None:\n            raise RuntimeError(\n                "AgentRuntime Investigation Shadow is disabled"\n            )\n\n        if context.tools is not self.tools:\n            raise TypeError(\n                "AgentRuntime Investigation Shadow requires shared Runtime tools"\n            )\n\n        if getattr(\n            self,\n            "cluster_verified_evidence_required",\n            False,\n        ):\n            if (\n                self.production_multi_cluster_readiness\n                is None\n            ):\n                raise ProductionMultiClusterReadinessError(\n                    "AgentRuntime Production Shadow readiness proof is unavailable"\n                )\n\n            readiness = (\n                self.production_multi_cluster_readiness\n                .evaluate_event(\n                    context.event\n                )\n            )\n\n            context.metadata[\n                "production_multi_cluster_readiness"\n            ] = readiness.snapshot()\n\n            if not readiness.ready:\n                raise ProductionMultiClusterReadinessError(\n                    "AgentRuntime Production Shadow read coverage is not ready"\n                )\n\n        return await (\n            self.investigation_coordinator.investigate(\n                context\n            )\n        )\n\n    async def _record_incident_evidence_shadow(\n        self,\n        context: AgentContext,\n    ) -> None:\n        """\n        Best-effort, decision-isolated production evidence preservation.\n\n        Runs only after the authoritative PlannerPipeline succeeds.\n        Disabled mode constructs no Recorder and issues no production Probe.\n        """\n\n        try:\n            settings = (\n                IncidentEvidenceRecorderSettings\n                .from_environment()\n            )\n        except Exception as exc:\n            context.metadata[\n                "incident_evidence_recorder"\n            ] = {\n                "schema_version": "v1",\n                "shadow_mode": True,\n                "read_only": True,\n                "decision_influence": False,\n                "automatic": True,\n                "status": "failed",\n                "failure_code": (\n                    type(exc).__name__[:256]\n                ),\n            }\n            return\n\n        if not settings.enabled:\n            return\n\n        recorder_context = AgentContext(\n            request_id=context.request_id,\n            event=deepcopy(\n                context.event\n            ),\n            tools=self.tools,\n            metadata={},\n        )\n\n        try:\n            recorder = ProductionIncidentEvidenceRecorder(\n                settings.resolve_output_dir()\n            )\n\n            result = await recorder.record(\n                recorder_context\n            )\n\n            context.metadata[\n                "incident_evidence_recorder"\n            ] = {\n                "schema_version": "v1",\n                "shadow_mode": True,\n                "read_only": True,\n                "decision_influence": False,\n                "automatic": True,\n                "status": "captured",\n                "created": result.created,\n                "incident_id": result.incident_id,\n                "observation_count": (\n                    result.observation_count\n                ),\n                "capture_file": result.path.name,\n            }\n\n        except Exception as exc:\n            context.metadata[\n                "incident_evidence_recorder"\n            ] = {\n                "schema_version": "v1",\n                "shadow_mode": True,\n                "read_only": True,\n                "decision_influence": False,\n                "automatic": True,\n                "status": "failed",\n                "failure_code": (\n                    type(exc).__name__[:256]\n                ),\n            }\n'
TEST_SOURCE = 'from __future__ import annotations\n\nimport asyncio\nfrom types import SimpleNamespace\n\nimport pytest\n\nfrom services.agent_runtime.app.investigation.live_readiness import (\n    ProductionReadinessLiveProbe,\n    ProductionReadinessLiveProbeError,\n)\nfrom services.agent_runtime.app.investigation.multi_cluster_readiness import (\n    ProductionMultiClusterReadinessGate,\n)\nfrom services.agent_runtime.app.tools.factory import create_tool_manager\nfrom services.agent_runtime.app.tools.kubernetes.router import (\n    KubernetesClusterRegistry,\n)\nfrom services.agent_runtime.app.tools.kubernetes.tool import KubernetesTool\nfrom services.agent_runtime.app.tools.prometheus.router import (\n    PrometheusClusterRegistry,\n)\nfrom services.agent_runtime.app.tools.prometheus.tool import PrometheusTool\n\n\nCLUSTER = "prod-us-03"\n\n\nclass RecordingKubernetesTool(KubernetesTool):\n    def __init__(self, *, response_cluster=CLUSTER, delay=0.0, fail=False):\n        super().__init__(\n            api_url="https://prod-us-03.kubernetes.test",\n            cluster_name=CLUSTER,\n            bearer_token="prod-us-read-token-1234567890",\n            verify_tls=True,\n            allow_dry_run_fallback=False,\n        )\n        self.response_cluster = response_cluster\n        self.delay = delay\n        self.fail = fail\n        self.calls = []\n\n    async def execute(self, **kwargs):\n        self.calls.append(dict(kwargs))\n        if self.delay:\n            await asyncio.sleep(self.delay)\n        if self.fail:\n            raise RuntimeError("https://secret.example/token-value")\n        return {\n            "success": True,\n            "source": "kubernetes",\n            "mode": "read_only",\n            "production_signal": True,\n            "cluster": self.response_cluster,\n            "data": {"phase": "Running"},\n        }\n\n\nclass RecordingPrometheusTool(PrometheusTool):\n    def __init__(self, *, response_cluster=CLUSTER, delay=0.0, fail=False):\n        super().__init__(\n            base_url="https://central.prometheus.test",\n            bearer_token="",\n            verify_tls=True,\n            allow_mock_fallback=False,\n        )\n        self.response_cluster = response_cluster\n        self.delay = delay\n        self.fail = fail\n        self.calls = []\n\n    async def execute(self, **kwargs):\n        self.calls.append(dict(kwargs))\n        if self.delay:\n            await asyncio.sleep(self.delay)\n        if self.fail:\n            raise RuntimeError("https://secret.prometheus/token-value")\n        return {\n            "success": True,\n            "source": "prometheus",\n            "mode": "read_only",\n            "production_signal": True,\n            "cluster": self.response_cluster,\n            "data": {"resultType": "vector", "result": []},\n        }\n\n\ndef build_probe(*, kubernetes=None, prometheus=None, timeout_seconds=1.0):\n    kubernetes = kubernetes or RecordingKubernetesTool()\n    prometheus = prometheus or RecordingPrometheusTool()\n\n    kubernetes_registry = KubernetesClusterRegistry([kubernetes])\n    prometheus_registry = PrometheusClusterRegistry({CLUSTER: prometheus})\n\n    tools = create_tool_manager(\n        kubernetes_cluster_registry=kubernetes_registry,\n        prometheus_cluster_registry=prometheus_registry,\n    )\n\n    gate = ProductionMultiClusterReadinessGate(\n        kubernetes_cluster_registry=kubernetes_registry,\n        prometheus_cluster_registry=prometheus_registry,\n        tools=tools,\n        strict_evidence_required=True,\n    )\n\n    return (\n        ProductionReadinessLiveProbe(\n            readiness_gate=gate,\n            tools=tools,\n            timeout_seconds=timeout_seconds,\n        ),\n        kubernetes,\n        prometheus,\n    )\n\n\ndef event(*, cluster=CLUSTER, namespace="fleet-edge", name="device-gateway-xyz789"):\n    return SimpleNamespace(\n        resources=[\n            SimpleNamespace(\n                cluster=cluster,\n                namespace=namespace,\n                name=name,\n            )\n        ]\n    )\n\n\n@pytest.mark.asyncio\nasync def test_live_probe_requires_exact_acknowledgement_before_any_tool_call():\n    probe, kubernetes, prometheus = build_probe()\n    with pytest.raises(\n        ProductionReadinessLiveProbeError,\n        match="acknowledgement",\n    ):\n        await probe.probe_event(\n            event(),\n            acknowledgement="WRONG",\n            reason="operator preflight",\n        )\n    assert kubernetes.calls == []\n    assert prometheus.calls == []\n\n\n@pytest.mark.asyncio\nasync def test_live_probe_requires_non_empty_reason_before_any_tool_call():\n    probe, kubernetes, prometheus = build_probe()\n    with pytest.raises(\n        ProductionReadinessLiveProbeError,\n        match="reason",\n    ):\n        await probe.probe_event(\n            event(),\n            acknowledgement=ProductionReadinessLiveProbe.ACKNOWLEDGEMENT,\n            reason="",\n        )\n    assert kubernetes.calls == []\n    assert prometheus.calls == []\n\n\n@pytest.mark.asyncio\nasync def test_live_probe_runs_exact_bounded_reads_for_ready_incident_scope():\n    probe, kubernetes, prometheus = build_probe()\n\n    report = await probe.probe_event(\n        event(),\n        acknowledgement=ProductionReadinessLiveProbe.ACKNOWLEDGEMENT,\n        reason="pre-production connectivity proof",\n    )\n\n    assert report.ready is True\n    assert report.kubernetes_probe_ready is True\n    assert report.prometheus_probe_ready is True\n\n    assert kubernetes.calls == [\n        {\n            "action": "get",\n            "resource": "pod",\n            "target": "device-gateway-xyz789",\n            "namespace": "fleet-edge",\n        }\n    ]\n    assert len(prometheus.calls) == 1\n    assert prometheus.calls[0]["query"] == (\n        \'count(up{cluster="prod-us-03"})\'\n    )\n\n\n@pytest.mark.asyncio\nasync def test_live_probe_rejects_cluster_mismatch_without_leaking_payload():\n    probe, _, _ = build_probe(\n        prometheus=RecordingPrometheusTool(\n            response_cluster="prod-sg-17"\n        )\n    )\n\n    report = await probe.probe_event(\n        event(),\n        acknowledgement=ProductionReadinessLiveProbe.ACKNOWLEDGEMENT,\n        reason="cluster proof",\n    )\n\n    assert report.ready is False\n    assert report.kubernetes_probe_ready is True\n    assert report.prometheus_probe_ready is False\n    assert report.issues == ("prometheus_live_probe_failed",)\n\n    text = str(report.snapshot())\n    assert "prometheus.test" not in text\n    assert "token-value" not in text\n\n\n@pytest.mark.asyncio\nasync def test_live_probe_sanitizes_backend_exception_details():\n    probe, _, _ = build_probe(\n        kubernetes=RecordingKubernetesTool(fail=True)\n    )\n\n    report = await probe.probe_event(\n        event(),\n        acknowledgement=ProductionReadinessLiveProbe.ACKNOWLEDGEMENT,\n        reason="sanitized error proof",\n    )\n\n    assert report.ready is False\n    assert report.issues == ("kubernetes_live_probe_failed",)\n\n    text = str(report.snapshot())\n    assert "https://" not in text\n    assert "token-value" not in text\n\n\n@pytest.mark.asyncio\nasync def test_live_probe_timeout_is_bounded_and_sanitized():\n    probe, _, _ = build_probe(\n        prometheus=RecordingPrometheusTool(delay=0.05),\n        timeout_seconds=0.01,\n    )\n\n    report = await probe.probe_event(\n        event(),\n        acknowledgement=ProductionReadinessLiveProbe.ACKNOWLEDGEMENT,\n        reason="bounded timeout proof",\n    )\n\n    assert report.ready is False\n    assert report.issues == ("prometheus_live_probe_failed",)\n\n\n@pytest.mark.asyncio\nasync def test_static_readiness_failure_prevents_live_calls():\n    probe, kubernetes, prometheus = build_probe()\n\n    report = await probe.probe_event(\n        event(cluster="prod-sg-17"),\n        acknowledgement=ProductionReadinessLiveProbe.ACKNOWLEDGEMENT,\n        reason="unknown cluster proof",\n    )\n\n    assert report.ready is False\n    assert report.issues == ("static_readiness_not_ready",)\n    assert kubernetes.calls == []\n    assert prometheus.calls == []\n\n\ndef test_live_probe_source_contains_no_write_authority():\n    from pathlib import Path\n    import services.agent_runtime.app.investigation.live_readiness as module\n\n    source = Path(module.__file__).read_text(encoding="utf-8")\n    forbidden = [\n        "ActionRuntime",\n        "ApprovalService",\n        "KubernetesProductionExecutor",\n        ".post(",\n        ".patch(",\n        ".put(",\n        ".delete(",\n    ]\n    assert [item for item in forbidden if item in source] == []\n'
RUNTIME_TEST_SOURCE = 'from __future__ import annotations\n\nfrom types import SimpleNamespace\n\nimport pytest\n\nfrom services.agent_runtime.app.investigation.live_readiness import (\n    ProductionReadinessLiveProbeError,\n)\nfrom services.agent_runtime.app.model.context import AgentContext\n\n\nclass FakeLiveProbe:\n    def __init__(self, snapshot):\n        self.snapshot_value = snapshot\n        self.calls = []\n\n    async def probe_event(\n        self,\n        event,\n        *,\n        acknowledgement,\n        reason,\n    ):\n        self.calls.append(\n            {\n                "event": event,\n                "acknowledgement": acknowledgement,\n                "reason": reason,\n            }\n        )\n        return SimpleNamespace(\n            snapshot=lambda: dict(self.snapshot_value)\n        )\n\n\n@pytest.mark.asyncio\nasync def test_runtime_live_readiness_is_explicit_and_records_sanitized_snapshot():\n    from services.agent_runtime.app.runtime.runtime import AgentRuntime\n\n    runtime = object.__new__(AgentRuntime)\n    runtime.tools = SimpleNamespace()\n\n    live = FakeLiveProbe(\n        {\n            "schema_version": "v1",\n            "read_only": True,\n            "decision_influence": False,\n            "ready": True,\n            "cluster": "prod-us-03",\n            "kubernetes_probe_ready": True,\n            "prometheus_probe_ready": True,\n            "issues": [],\n        }\n    )\n    runtime.production_multi_cluster_live_readiness = live\n\n    context = AgentContext.model_construct(\n        event=SimpleNamespace(resources=[]),\n        tools=runtime.tools,\n        metadata={},\n    )\n\n    snapshot = await runtime.run_production_multi_cluster_live_readiness(\n        context,\n        acknowledgement=(\n            "I_ACKNOWLEDGE_PRODUCTION_READINESS_LIVE_READS"\n        ),\n        reason="operator preflight",\n    )\n\n    assert snapshot["ready"] is True\n    assert (\n        context.metadata[\n            "production_multi_cluster_live_readiness"\n        ]["ready"]\n        is True\n    )\n    assert len(live.calls) == 1\n\n\n@pytest.mark.asyncio\nasync def test_runtime_live_readiness_unavailable_fails_before_network():\n    from services.agent_runtime.app.runtime.runtime import AgentRuntime\n\n    runtime = object.__new__(AgentRuntime)\n    runtime.tools = SimpleNamespace()\n    runtime.production_multi_cluster_live_readiness = None\n\n    context = AgentContext.model_construct(\n        event=SimpleNamespace(resources=[]),\n        tools=runtime.tools,\n        metadata={},\n    )\n\n    with pytest.raises(\n        ProductionReadinessLiveProbeError,\n        match="unavailable",\n    ):\n        await runtime.run_production_multi_cluster_live_readiness(\n            context,\n            acknowledgement=(\n                "I_ACKNOWLEDGE_PRODUCTION_READINESS_LIVE_READS"\n            ),\n            reason="operator preflight",\n        )\n'


@dataclass(frozen=True)
class CommandResult:
    name: str
    command: list[str]
    returncode: int
    stdout: str
    stderr: str


def find_repo_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (
            (candidate / "pyproject.toml").exists()
            and (candidate / "services").exists()
            and (candidate / "packages").exists()
        ):
            return candidate
    raise RuntimeError("Repository root not found.")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.endswith("\n"):
        normalized += "\n"
    path.write_text(
        normalized,
        encoding="utf-8",
        newline="\n",
    )


def raw_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def backup_file(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = path.with_name(
        f"{path.name}.before_{VERSION}_{stamp}.bak"
    )
    shutil.copy2(path, backup)
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


def section(lines: list[str], title: str) -> None:
    lines.extend(
        ["", "=" * 120, title, "=" * 120, ""]
    )


def add_command(lines: list[str], result: CommandResult) -> None:
    section(lines, f"COMMAND: {result.name}")
    lines.extend(
        [
            " ".join(result.command),
            "",
            f"ExitCode: {result.returncode}",
            "",
            "STDOUT",
            "-" * 120,
            result.stdout.rstrip() or "<EMPTY>",
            "",
            "STDERR",
            "-" * 120,
            result.stderr.rstrip() or "<EMPTY>",
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

    actual = raw_sha256(path)
    expected = EXPECTED_RAW_HASHES[relative]

    if actual != expected:
        raise RuntimeError(
            (
                f"{relative} changed after the installed static-readiness baseline. "
                f"expected_raw_sha256={expected} actual_raw_sha256={actual}. "
                "Refusing stale Production Readiness Live Probe installation."
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
        if not (root / relative).exists()
    ]
    if missing:
        raise RuntimeError(
            f"Required {label} tests are missing: "
            + ", ".join(missing)
        )
    return relative_paths


def main() -> int:
    root = find_repo_root(Path.cwd().resolve())
    after = root / AFTER_NAME
    error = root / ERROR_NAME

    for output in (after, error):
        try:
            output.unlink()
        except FileNotFoundError:
            pass

    live_file = root / (
        "services/agent_runtime/app/investigation/live_readiness.py"
    )
    runtime_file = root / (
        "services/agent_runtime/app/runtime/runtime.py"
    )
    test_file = root / (
        "services/agent_runtime/tests/"
        "test_production_readiness_live_probe.py"
    )
    runtime_test_file = root / (
        "services/agent_runtime/tests/"
        "test_runtime_production_readiness_live_probe.py"
    )

    sources = {
        live_file: LIVE_READINESS_SOURCE,
        runtime_file: RUNTIME_SOURCE,
        test_file: TEST_SOURCE,
        runtime_test_file: RUNTIME_TEST_SOURCE,
    }
    targets = list(sources)
    preexisting = {path: path.exists() for path in targets}
    backups = []

    report = [
        "Production Readiness Live Probe v1.1",
        f"GeneratedAt: {datetime.now().astimezone().isoformat()}",
        "",
        "Safety:",
        "- capability is installed but never executed automatically",
        "- every execution requires exact acknowledgement + non-empty reason",
        "- static ProductionMultiClusterReadinessGate must pass before network calls",
        "- only Runtime-owned shared read Tools are used",
        "- bounded timeout per backend",
        "",
        "Live reads:",
        "- Kubernetes: GET the Incident's exact Pod through the existing read-only kubernetes Router",
        "- Prometheus: instant aggregate count(up{cluster=...}) through the existing read-only prometheus Router",
        "",
        "Acceptance:",
        "- success/source/mode/production_signal/cluster/data shape must match",
        "- explicit cluster mismatch fails the live proof",
        "- backend exception text is never returned",
        "- raw backend data is never copied into the readiness snapshot",
        "",
        "Runtime:",
        "- constructs Live Probe only when static multi-cluster readiness is applicable",
        "- exposes explicit run_production_multi_cluster_live_readiness(...)",
        "- execute() and automatic Shadow never call Live Probe",
        "",
        "Installer tests use fake Tool children and send no real network request.",
    ]

    try:
        section(report, "CURRENT RAW HASH PREFLIGHT")
        for relative in EXPECTED_RAW_HASHES:
            verify_raw_hash(root=root, relative=relative)
            report.append(
                relative + "=" + EXPECTED_RAW_HASHES[relative]
            )

        for path in (live_file, test_file, runtime_test_file):
            if path.exists():
                raise RuntimeError(
                    "Production live readiness new file already exists; "
                    "refusing to overwrite an unreviewed file: "
                    + str(path.relative_to(root))
                )

        section(report, "BACKUP")
        for path in targets:
            if path.exists():
                backup = backup_file(path)
                backups.append((path, backup))
                report.append(
                    "backup=" + str(backup.relative_to(root))
                )

        for path, source in sources.items():
            write_text(path, source)

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
                    str(path.relative_to(root))
                    for path in targets
                ],
            ],
        )
        add_command(report, syntax)
        if syntax.returncode != 0:
            raise RuntimeError(
                "Production Readiness Live Probe syntax failed"
            )

        focused_paths = require_tests(
            root=root,
            label="live readiness focused",
            relative_paths=[
                "services/agent_runtime/tests/test_production_readiness_live_probe.py",
                "services/agent_runtime/tests/test_runtime_production_readiness_live_probe.py",
                "services/agent_runtime/tests/test_production_multi_cluster_readiness.py",
                "services/agent_runtime/tests/test_runtime_production_multi_cluster_readiness.py",
                "services/agent_runtime/tests/test_production_cluster_verified_evidence_policy.py",
                "services/agent_runtime/tests/test_cross_source_cluster_evidence_consistency.py",
            ],
        )
        focused = run_command(
            root=root,
            name="Production live readiness focused suite",
            command=["uv", "run", "pytest", *focused_paths, "-q"],
        )
        add_command(report, focused)
        if focused.returncode != 0:
            raise RuntimeError(
                "Production live readiness focused tests failed"
            )

        routing_paths = require_tests(
            root=root,
            label="routing/tool compatibility",
            relative_paths=[
                "services/agent_runtime/tests/test_multi_cluster_kubernetes_router.py",
                "services/agent_runtime/tests/test_multi_cluster_prometheus_router.py",
                "services/agent_runtime/tests/test_kubernetes_tool.py",
                "services/agent_runtime/tests/test_prometheus_tool.py",
                "services/agent_runtime/tests/test_investigation_production_tool_contract.py",
            ],
        )
        routing = run_command(
            root=root,
            name="Routing / Tool compatibility suite",
            command=["uv", "run", "pytest", *routing_paths, "-q"],
        )
        add_command(report, routing)
        if routing.returncode != 0:
            raise RuntimeError(
                "Production live readiness routing compatibility failed"
            )

        runtime_paths = require_tests(
            root=root,
            label="Runtime/Shadow compatibility",
            relative_paths=[
                "services/agent_runtime/tests/test_runtime_investigation_wiring.py",
                "services/agent_runtime/tests/test_investigation_auto_shadow_orchestration.py",
                "services/agent_runtime/tests/test_investigation_coordinator.py",
                "services/agent_runtime/tests/test_verification_collector.py",
                "services/agent_runtime/tests/test_verification_fail_closed_e2e.py",
            ],
        )
        runtime_compat = run_command(
            root=root,
            name="Runtime / Shadow / Verification compatibility suite",
            command=["uv", "run", "pytest", *runtime_paths, "-q"],
        )
        add_command(report, runtime_compat)
        if runtime_compat.returncode != 0:
            raise RuntimeError(
                "Production live readiness Runtime compatibility failed"
            )

        preflight = run_command(
            root=root,
            name="Production live readiness architecture preflight",
            command=[
                "uv",
                "run",
                "python",
                "-c",
                (
                    "from pathlib import Path; "
                    "l=Path(r'services/agent_runtime/app/investigation/live_readiness.py').read_text(encoding='utf-8'); "
                    "r=Path(r'services/agent_runtime/app/runtime/runtime.py').read_text(encoding='utf-8'); "
                    "print('live_probe='+str('class ProductionReadinessLiveProbe' in l)); "
                    "print('ack_required='+str('I_ACKNOWLEDGE_PRODUCTION_READINESS_LIVE_READS' in l)); "
                    "print('kubernetes_get='+str('action=\"get\"' in l)); "
                    "print('prometheus_count_query='+str('count(up{cluster=' in l)); "
                    "print('runtime_explicit_method='+str('run_production_multi_cluster_live_readiness' in r)); "
                    "print('runtime_method_count='+str(r.count('run_production_multi_cluster_live_readiness('))); "
                    "assert 'class ProductionReadinessLiveProbe' in l; "
                    "assert 'I_ACKNOWLEDGE_PRODUCTION_READINESS_LIVE_READS' in l; "
                    "assert 'action=\"get\"' in l; "
                    "assert 'count(up{cluster=' in l; "
                    "assert r.count('run_production_multi_cluster_live_readiness(') == 1"
                ),
            ],
        )
        add_command(report, preflight)
        if preflight.returncode != 0:
            raise RuntimeError(
                "Production live readiness architecture preflight failed"
            )

        authority = run_command(
            root=root,
            name="Production live readiness read-only authority boundary",
            command=[
                "uv",
                "run",
                "python",
                "-c",
                (
                    "from pathlib import Path; "
                    "l=Path(r'services/agent_runtime/app/investigation/live_readiness.py').read_text(encoding='utf-8'); "
                    "bad=[x for x in ['ActionRuntime','ApprovalService','KubernetesProductionExecutor','.post(','.patch(','.put(','.delete('] if x in l]; "
                    "print('forbidden_matches='+str(bad)); "
                    "raise SystemExit(1 if bad else 0)"
                ),
            ],
        )
        add_command(report, authority)
        if authority.returncode != 0:
            raise RuntimeError(
                "Production live readiness authority boundary failed"
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
                    str(path.relative_to(root))
                    for path in targets
                ],
            ],
        )
        add_command(report, status)

        section(report, "RESULT")
        report.extend(
            [
                "PASSED",
                "",
                "Production Readiness Live Probe v1.1 is installed.",
                "",
                "Guarantee:",
                "- no live read occurs automatically",
                "- exact operator acknowledgement + reason are mandatory",
                "- static readiness must pass before live calls",
                "- exactly one bounded Kubernetes Pod read and one bounded Prometheus aggregate query are used",
                "- both results must be read_only production signals for the exact Incident cluster",
                "- returned snapshot contains no raw payload, endpoint URL, credential, or exception text",
                "",
                "Next recommended step:",
                "- Production Live Readiness Runner / Report v1: explicit CLI/dev runner for one real Incident fixture, producing one sanitized report file without enabling automatic Shadow traffic.",
            ]
        )

        write_text(after, "\n".join(report) + "\n")

        print("=" * 72)
        print("PRODUCTION READINESS LIVE PROBE V1.1 PASSED")
        print("=" * 72)
        print()
        print("Installer tests used fake Tools only.")
        print("No real Kubernetes/Prometheus/LLM request was sent.")
        print()
        print("Upload only:")
        print(after)
        return 0

    except Exception as exc:
        rollback = []

        for original, backup in reversed(backups):
            try:
                shutil.copy2(backup, original)
                rollback.append(
                    "RESTORED " + str(original.relative_to(root))
                )
            except Exception as rollback_exc:
                rollback.append(
                    "ROLLBACK FAILED "
                    + str(original.relative_to(root))
                    + ": "
                    + f"{type(rollback_exc).__name__}: {rollback_exc}"
                )

        for path in targets:
            if not preexisting[path] and path.exists():
                try:
                    path.unlink()
                    rollback.append(
                        "REMOVED newly-created "
                        + str(path.relative_to(root))
                    )
                except Exception as rollback_exc:
                    rollback.append(
                        "ROLLBACK REMOVE FAILED "
                        + str(path.relative_to(root))
                        + ": "
                        + f"{type(rollback_exc).__name__}: {rollback_exc}"
                    )

        write_text(
            error,
            "\n".join(
                [
                    "Production Readiness Live Probe v1.1 FAILED",
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
        print("PRODUCTION READINESS LIVE PROBE V1.1 FAILED")
        print("=" * 72)
        print()
        print("Modified files were rolled back where possible.")
        print()
        print("Upload only:")
        print(error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
