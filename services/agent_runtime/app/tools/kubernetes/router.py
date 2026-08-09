from __future__ import annotations

from collections.abc import Iterable
from types import MappingProxyType
from typing import Any

from services.agent_runtime.app.tools.base import (
    BaseTool,
)
from services.agent_runtime.app.tools.kubernetes.change_tool import (
    KubernetesChangeTool,
)
from services.agent_runtime.app.tools.kubernetes.tool import (
    KubernetesTool,
)


class KubernetesClusterRoutingError(
    RuntimeError
):
    """
    The read-only Kubernetes cluster route cannot be resolved safely.
    """


class KubernetesClusterRegistry:
    """
    Immutable startup registry of cluster-bound read-only Kubernetes tools.

    This registry stores only already-constructed KubernetesTool objects.
    It does not parse credentials, expose tokens, mutate Runtime scope, or
    perform network calls.

    Multi-cluster registration is intentionally startup-only. Dynamic cluster
    discovery and credential rotation belong to a future configuration layer.
    """

    _MAX_CLUSTERS = 64
    _MAX_CLUSTER_NAME_LENGTH = 253

    def __init__(
        self,
        tools: Iterable[
            KubernetesTool
        ],
    ) -> None:
        if isinstance(
            tools,
            (
                str,
                bytes,
                KubernetesTool,
            ),
        ):
            raise TypeError(
                "Kubernetes cluster registry requires a collection of KubernetesTool objects"
            )

        try:
            items = tuple(
                tools
            )
        except TypeError:
            raise TypeError(
                "Kubernetes cluster registry requires an iterable"
            ) from None

        if len(
            items
        ) > self._MAX_CLUSTERS:
            raise KubernetesClusterRoutingError(
                "Kubernetes cluster registry exceeds the bounded cluster limit"
            )

        mapping: dict[
            str,
            KubernetesTool,
        ] = {}

        for tool in items:
            if not isinstance(
                tool,
                KubernetesTool,
            ):
                raise TypeError(
                    "Kubernetes cluster registry accepts KubernetesTool only"
                )

            cluster = self._tool_cluster_name(
                tool
            )

            if tool.api_url is None:
                raise KubernetesClusterRoutingError(
                    "Registered Kubernetes cluster is not configured with an API endpoint"
                )

            if cluster in mapping:
                raise KubernetesClusterRoutingError(
                    "Duplicate Kubernetes cluster registration is not allowed"
                )

            mapping[
                cluster
            ] = tool

        self._tools = MappingProxyType(
            mapping
        )

    @property
    def count(
        self,
    ) -> int:
        return len(
            self._tools
        )

    @property
    def cluster_names(
        self,
    ) -> tuple[str, ...]:
        return tuple(
            sorted(
                self._tools
            )
        )

    def resolve(
        self,
        cluster: str | None,
    ) -> KubernetesTool:
        requested = self._requested_cluster(
            cluster
        )

        try:
            return self._tools[
                requested
            ]
        except KeyError:
            raise KubernetesClusterRoutingError(
                "Requested Kubernetes cluster is not registered"
            ) from None

    def _requested_cluster(
        self,
        cluster: str | None,
    ) -> str:
        if cluster is None:
            if self.count == 1:
                return next(
                    iter(
                        self._tools
                    )
                )

            if self.count == 0:
                raise KubernetesClusterRoutingError(
                    "No Kubernetes clusters are registered"
                )

            raise KubernetesClusterRoutingError(
                "Kubernetes cluster is required when multiple clusters are registered"
            )

        if (
            not isinstance(
                cluster,
                str,
            )
            or not cluster
            or cluster != cluster.strip()
            or len(
                cluster
            )
            > self._MAX_CLUSTER_NAME_LENGTH
            or "\x00" in cluster
        ):
            raise KubernetesClusterRoutingError(
                "Requested Kubernetes cluster is invalid"
            )

        return cluster

    @classmethod
    def _tool_cluster_name(
        cls,
        tool: KubernetesTool,
    ) -> str:
        value = tool.cluster_name

        if (
            not isinstance(
                value,
                str,
            )
            or not value
            or value != value.strip()
            or len(
                value
            )
            > cls._MAX_CLUSTER_NAME_LENGTH
            or "\x00" in value
        ):
            raise KubernetesClusterRoutingError(
                "Registered KubernetesTool requires an exact cluster_name"
            )

        return value


class MultiClusterKubernetesToolRouter(
    BaseTool
):
    """
    Route the existing read-only `kubernetes` contract by exact cluster.

    The caller still supplies the original Tool arguments. The only routing
    authority added here is exact selection of an already-bound KubernetesTool.
    """

    def __init__(
        self,
        clusters: KubernetesClusterRegistry,
    ) -> None:
        if not isinstance(
            clusters,
            KubernetesClusterRegistry,
        ):
            raise TypeError(
                "Multi-cluster Kubernetes router requires KubernetesClusterRegistry"
            )

        if clusters.count == 0:
            raise KubernetesClusterRoutingError(
                "Multi-cluster Kubernetes router requires at least one cluster"
            )

        self.clusters = clusters

    @property
    def name(
        self,
    ) -> str:
        return "kubernetes"

    @property
    def is_available(
        self,
    ) -> bool:
        return self.clusters.count > 0

    async def execute(
        self,
        *,
        cluster: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        tool = self.clusters.resolve(
            cluster
        )

        selected_cluster = (
            tool.cluster_name
        )

        assert isinstance(
            selected_cluster,
            str,
        )

        return await tool.execute(
            cluster=selected_cluster,
            **kwargs,
        )


class MultiClusterKubernetesChangeToolRouter(
    BaseTool
):
    """
    Route workload/config change evidence through the same selected cluster.

    Each KubernetesChangeTool is constructed from the exact KubernetesTool
    already registered for that cluster, so Pod/ReplicaSet/Deployment reads
    cannot silently switch Kubernetes clients.
    """

    def __init__(
        self,
        clusters: KubernetesClusterRegistry,
    ) -> None:
        if not isinstance(
            clusters,
            KubernetesClusterRegistry,
        ):
            raise TypeError(
                "Multi-cluster Kubernetes change router requires KubernetesClusterRegistry"
            )

        if clusters.count == 0:
            raise KubernetesClusterRoutingError(
                "Multi-cluster Kubernetes change router requires at least one cluster"
            )

        self.clusters = clusters

        self._change_tools = {
            name: KubernetesChangeTool(
                clusters.resolve(
                    name
                )
            )
            for name in clusters.cluster_names
        }

    @property
    def name(
        self,
    ) -> str:
        return "kubernetes_change"

    @property
    def is_available(
        self,
    ) -> bool:
        return self.clusters.count > 0

    async def execute(
        self,
        *,
        cluster: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        kubernetes = (
            self.clusters.resolve(
                cluster
            )
        )

        selected_cluster = (
            kubernetes.cluster_name
        )

        assert isinstance(
            selected_cluster,
            str,
        )

        change_tool = (
            self._change_tools[
                selected_cluster
            ]
        )

        if (
            change_tool.kubernetes
            is not kubernetes
        ):
            raise KubernetesClusterRoutingError(
                "Kubernetes change router lost cluster-client identity"
            )

        return await change_tool.execute(
            cluster=selected_cluster,
            **kwargs,
        )


__all__ = [
    "KubernetesClusterRegistry",
    "KubernetesClusterRoutingError",
    "MultiClusterKubernetesChangeToolRouter",
    "MultiClusterKubernetesToolRouter",
]
