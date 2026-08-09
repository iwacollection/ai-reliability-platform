from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from typing import Any

from services.agent_runtime.app.investigation.multi_cluster_readiness import (
    ProductionMultiClusterReadinessGate,
)
from services.agent_runtime.app.tools.manager import ToolManager


class ProductionReadinessLiveProbeError(RuntimeError):
    """Explicit bounded production-read readiness probe cannot execute safely."""


@dataclass(frozen=True, slots=True)
class ProductionReadinessLiveProbeReport:
    schema_version: str
    read_only: bool
    decision_influence: bool
    ready: bool
    cluster: str | None
    kubernetes_probe_ready: bool
    prometheus_probe_ready: bool
    issues: tuple[str, ...]

    def snapshot(self) -> dict[str, Any]:
        value = asdict(self)
        value["issues"] = list(self.issues)
        return value


class ProductionReadinessLiveProbe:
    """
    Explicit bounded proof of live production read reachability.

    It is never invoked automatically. Every execution requires the exact
    acknowledgement string, a non-empty reason, and a passing static readiness
    report. Only one Kubernetes Pod GET and one aggregate Prometheus query are
    attempted. Raw backend payloads and exception text are never returned.
    """

    ACKNOWLEDGEMENT = "I_ACKNOWLEDGE_PRODUCTION_READINESS_LIVE_READS"

    def __init__(
        self,
        *,
        readiness_gate: ProductionMultiClusterReadinessGate,
        tools: ToolManager,
        timeout_seconds: float = 6.0,
    ) -> None:
        if not isinstance(readiness_gate, ProductionMultiClusterReadinessGate):
            raise TypeError("Production live readiness Gate is invalid")
        if not isinstance(tools, ToolManager):
            raise TypeError("Production live readiness ToolManager is invalid")
        if (
            not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
        ):
            raise TypeError("Production live readiness timeout is invalid")

        normalized_timeout = float(timeout_seconds)
        if normalized_timeout <= 0 or normalized_timeout > 30:
            raise ValueError("Production live readiness timeout is out of bounds")

        self.readiness_gate = readiness_gate
        self.tools = tools
        self.timeout_seconds = normalized_timeout

    async def probe_event(
        self,
        event: Any,
        *,
        acknowledgement: str,
        reason: str,
    ) -> ProductionReadinessLiveProbeReport:
        self._validate_operator_intent(
            acknowledgement=acknowledgement,
            reason=reason,
        )

        static_report = self.readiness_gate.evaluate_event(event)

        if not static_report.ready:
            return ProductionReadinessLiveProbeReport(
                schema_version="v1",
                read_only=True,
                decision_influence=False,
                ready=False,
                cluster=static_report.cluster,
                kubernetes_probe_ready=False,
                prometheus_probe_ready=False,
                issues=("static_readiness_not_ready",),
            )

        cluster, namespace, target, scope_issue = self._event_scope(event)

        if scope_issue is not None:
            return ProductionReadinessLiveProbeReport(
                schema_version="v1",
                read_only=True,
                decision_influence=False,
                ready=False,
                cluster=cluster,
                kubernetes_probe_ready=False,
                prometheus_probe_ready=False,
                issues=(scope_issue,),
            )

        assert cluster is not None
        assert namespace is not None
        assert target is not None

        kubernetes_ready = await self._probe_kubernetes(
            cluster=cluster,
            namespace=namespace,
            target=target,
        )
        prometheus_ready = await self._probe_prometheus(cluster=cluster)

        issues = []
        if not kubernetes_ready:
            issues.append("kubernetes_live_probe_failed")
        if not prometheus_ready:
            issues.append("prometheus_live_probe_failed")

        return ProductionReadinessLiveProbeReport(
            schema_version="v1",
            read_only=True,
            decision_influence=False,
            ready=(kubernetes_ready and prometheus_ready and not issues),
            cluster=cluster,
            kubernetes_probe_ready=kubernetes_ready,
            prometheus_probe_ready=prometheus_ready,
            issues=tuple(issues),
        )

    async def _probe_kubernetes(
        self,
        *,
        cluster: str,
        namespace: str,
        target: str,
    ) -> bool:
        try:
            result = await asyncio.wait_for(
                self.tools.call(
                    "kubernetes",
                    action="get",
                    resource="pod",
                    target=target,
                    namespace=namespace,
                    cluster=cluster,
                ),
                timeout=self.timeout_seconds,
            )
        except Exception:
            return False

        return self._valid_result(
            result,
            expected_source="kubernetes",
            expected_cluster=cluster,
        )

    async def _probe_prometheus(self, *, cluster: str) -> bool:
        query = (
            'count(up{cluster="'
            + self._promql_label_value(cluster)
            + '"})'
        )

        try:
            result = await asyncio.wait_for(
                self.tools.call(
                    "prometheus",
                    query=query,
                    cluster=cluster,
                ),
                timeout=self.timeout_seconds,
            )
        except Exception:
            return False

        return self._valid_result(
            result,
            expected_source="prometheus",
            expected_cluster=cluster,
        )

    @staticmethod
    def _valid_result(
        result: Any,
        *,
        expected_source: str,
        expected_cluster: str,
    ) -> bool:
        if not isinstance(result, dict):
            return False

        return (
            result.get("success") is True
            and result.get("source") == expected_source
            and result.get("mode") == "read_only"
            and result.get("production_signal") is True
            and result.get("cluster") == expected_cluster
            and isinstance(result.get("data"), dict)
        )

    @classmethod
    def _validate_operator_intent(
        cls,
        *,
        acknowledgement: str,
        reason: str,
    ) -> None:
        if acknowledgement != cls.ACKNOWLEDGEMENT:
            raise ProductionReadinessLiveProbeError(
                "Production live readiness acknowledgement is invalid"
            )

        if (
            not isinstance(reason, str)
            or not reason.strip()
            or reason != reason.strip()
            or len(reason) > 512
        ):
            raise ProductionReadinessLiveProbeError(
                "Production live readiness reason is invalid"
            )

    @staticmethod
    def _event_scope(
        event: Any,
    ) -> tuple[str | None, str | None, str | None, str | None]:
        resources = getattr(event, "resources", None)

        if not isinstance(resources, (list, tuple)) or not resources:
            return (None, None, None, "incident_resource_missing")

        candidates = set()

        for resource in resources:
            values = (
                getattr(resource, "cluster", None),
                getattr(resource, "namespace", None),
                getattr(resource, "name", None),
            )
            if any(
                not isinstance(item, str)
                or not item.strip()
                or item != item.strip()
                for item in values
            ):
                continue
            candidates.add(values)

        if len(candidates) != 1:
            return (
                None,
                None,
                None,
                "incident_resource_missing"
                if not candidates
                else "incident_resource_ambiguous",
            )

        cluster, namespace, target = next(iter(candidates))
        return (cluster, namespace, target, None)

    @staticmethod
    def _promql_label_value(value: str) -> str:
        return (
            value.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\r", "\\r")
        )


__all__ = [
    "ProductionReadinessLiveProbe",
    "ProductionReadinessLiveProbeError",
    "ProductionReadinessLiveProbeReport",
]
