from __future__ import annotations

from collections.abc import Mapping
from re import fullmatch
from types import MappingProxyType
from typing import Any

from services.agent_runtime.app.tools.base import (
    BaseTool,
)
from services.agent_runtime.app.tools.prometheus.tool import (
    PrometheusTool,
)


class PrometheusClusterRoutingError(
    RuntimeError
):
    """
    The read-only Prometheus cluster route cannot be resolved safely.
    """


class PrometheusClusterRegistry:
    """
    Immutable startup mapping from Incident cluster identity to PrometheusTool.

    Unlike Kubernetes, more than one cluster may intentionally map to the same
    PrometheusTool. This supports central Thanos/Mimir/Prometheus deployments
    while keeping cluster routing explicit and fail-closed.

    The registry does not parse credentials, mutate PromQL, or perform network
    calls.
    """

    _MAX_CLUSTERS = 64
    _MAX_CLUSTER_NAME_LENGTH = 128
    _CLUSTER_PATTERN = (
        r"[A-Za-z0-9]"
        r"(?:[A-Za-z0-9_.:-]{0,126}[A-Za-z0-9])?"
    )

    def __init__(
        self,
        bindings: Mapping[
            str,
            PrometheusTool,
        ],
    ) -> None:
        if not isinstance(
            bindings,
            Mapping,
        ):
            raise TypeError(
                "Prometheus cluster registry requires a mapping"
            )

        items = tuple(
            bindings.items()
        )

        if len(
            items
        ) > self._MAX_CLUSTERS:
            raise PrometheusClusterRoutingError(
                "Prometheus cluster registry exceeds the bounded cluster limit"
            )

        mapping: dict[
            str,
            PrometheusTool,
        ] = {}

        for cluster, tool in items:
            normalized_cluster = (
                self._cluster_name(
                    cluster
                )
            )

            if not isinstance(
                tool,
                PrometheusTool,
            ):
                raise TypeError(
                    "Prometheus cluster registry accepts PrometheusTool values only"
                )

            if tool.base_url is None:
                raise PrometheusClusterRoutingError(
                    "Registered Prometheus cluster has no live endpoint"
                )

            if (
                tool.allow_mock_fallback
                is not False
            ):
                raise PrometheusClusterRoutingError(
                    "Registered Prometheus cluster must disable mock fallback"
                )

            if tool.verify_tls is not True:
                raise PrometheusClusterRoutingError(
                    "Registered Prometheus cluster must verify TLS"
                )

            mapping[
                normalized_cluster
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
    ) -> tuple[
        str,
        PrometheusTool,
    ]:
        requested = (
            self._requested_cluster(
                cluster
            )
        )

        try:
            return (
                requested,
                self._tools[
                    requested
                ],
            )

        except KeyError:
            raise PrometheusClusterRoutingError(
                "Requested Prometheus cluster is not registered"
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
                raise PrometheusClusterRoutingError(
                    "No Prometheus clusters are registered"
                )

            raise PrometheusClusterRoutingError(
                "Prometheus cluster is required when multiple clusters are registered"
            )

        return self._cluster_name(
            cluster
        )

    @classmethod
    def _cluster_name(
        cls,
        value: Any,
    ) -> str:
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
            or fullmatch(
                cls._CLUSTER_PATTERN,
                value,
            )
            is None
        ):
            raise PrometheusClusterRoutingError(
                "Prometheus cluster identifier is invalid"
            )

        return value


class MultiClusterPrometheusToolRouter(
    BaseTool
):
    """
    Route the existing read-only `prometheus` Tool contract by exact cluster.

    Cluster identity selects only the endpoint binding. The existing bounded
    PromQL remains owned by Investigation ProbeExecutor.
    """

    def __init__(
        self,
        clusters: PrometheusClusterRegistry,
    ) -> None:
        if not isinstance(
            clusters,
            PrometheusClusterRegistry,
        ):
            raise TypeError(
                "Multi-cluster Prometheus router requires PrometheusClusterRegistry"
            )

        if clusters.count == 0:
            raise PrometheusClusterRoutingError(
                "Multi-cluster Prometheus router requires at least one cluster"
            )

        self.clusters = clusters

    @property
    def name(
        self,
    ) -> str:
        return "prometheus"

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
        selected_cluster, tool = (
            self.clusters.resolve(
                cluster
            )
        )

        result = await tool.execute(
            **kwargs,
        )

        if not isinstance(
            result,
            Mapping,
        ):
            raise PrometheusClusterRoutingError(
                "Prometheus routed Tool returned an invalid result"
            )

        existing_cluster = result.get(
            "cluster"
        )

        if (
            existing_cluster is not None
            and existing_cluster
            != selected_cluster
        ):
            raise PrometheusClusterRoutingError(
                "Prometheus routed Tool returned a mismatched cluster identity"
            )

        routed = dict(
            result
        )

        routed[
            "cluster"
        ] = selected_cluster

        return routed


__all__ = [
    "MultiClusterPrometheusToolRouter",
    "PrometheusClusterRegistry",
    "PrometheusClusterRoutingError",
]
