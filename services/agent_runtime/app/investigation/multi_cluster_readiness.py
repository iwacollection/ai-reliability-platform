from __future__ import annotations

from dataclasses import (
    asdict,
    dataclass,
    replace,
)
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from services.agent_runtime.app.investigation.models import (
    InvestigationProbe,
    default_investigation_probes,
)
from services.agent_runtime.app.tools.kubernetes.router import (
    KubernetesClusterRegistry,
    MultiClusterKubernetesChangeToolRouter,
    MultiClusterKubernetesToolRouter,
)
from services.agent_runtime.app.tools.manager import (
    ToolManager,
)
from services.agent_runtime.app.tools.prometheus.router import (
    MultiClusterPrometheusToolRouter,
    PrometheusClusterRegistry,
)


class ProductionMultiClusterReadinessError(
    RuntimeError
):
    """
    Production Shadow cannot prove complete multi-cluster read coverage.
    """


@dataclass(
    frozen=True,
    slots=True,
)
class ProductionClusterReadinessReport:
    schema_version: str
    read_only: bool
    decision_influence: bool
    applicable: bool
    ready: bool
    cluster: str | None
    strict_evidence_required: bool
    kubernetes_registry_present: bool
    prometheus_registry_present: bool
    kubernetes_route_ready: bool
    kubernetes_change_route_ready: bool
    prometheus_route_ready: bool
    required_investigation_probes: tuple[
        str,
        ...,
    ]
    covered_investigation_probes: tuple[
        str,
        ...,
    ]
    required_verification_tools: tuple[
        str,
        ...,
    ]
    covered_verification_tools: tuple[
        str,
        ...,
    ]
    issues: tuple[
        str,
        ...,
    ]

    def snapshot(
        self,
    ) -> dict[str, Any]:
        value = asdict(
            self
        )

        value[
            "required_investigation_probes"
        ] = list(
            self.required_investigation_probes
        )

        value[
            "covered_investigation_probes"
        ] = list(
            self.covered_investigation_probes
        )

        value[
            "required_verification_tools"
        ] = list(
            self.required_verification_tools
        )

        value[
            "covered_verification_tools"
        ] = list(
            self.covered_verification_tools
        )

        value[
            "issues"
        ] = list(
            self.issues
        )

        return value


@dataclass(
    frozen=True,
    slots=True,
)
class ProductionMultiClusterCoverageReport:
    schema_version: str
    read_only: bool
    decision_influence: bool
    applicable: bool
    ready: bool
    clusters: tuple[
        ProductionClusterReadinessReport,
        ...,
    ]
    issues: tuple[
        str,
        ...,
    ]

    def snapshot(
        self,
    ) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "read_only": self.read_only,
            "decision_influence": (
                self.decision_influence
            ),
            "applicable": self.applicable,
            "ready": self.ready,
            "clusters": [
                item.snapshot()
                for item in self.clusters
            ],
            "issues": list(
                self.issues
            ),
        }


class ProductionMultiClusterReadinessGate:
    """
    Static production-read coverage proof.

    The Gate performs no Kubernetes or Prometheus request. It inspects only
    already-built registries, Router identity, endpoint hardening, and the
    required Investigation/Verification provider coverage.

    A production-ready cluster must have:
    - exact Kubernetes binding,
    - exact Prometheus binding,
    - Runtime ToolManager wired to those exact registries,
    - hardened live-read child Tools,
    - complete default Investigation probe-family coverage,
    - Kubernetes + Prometheus coverage for current required Verification.

    The current default Investigation probe list is read from
    default_investigation_probes() instead of copied into this module.
    """

    _REQUIRED_VERIFICATION_TOOLS = (
        "kubernetes",
        "prometheus",
    )

    def __init__(
        self,
        *,
        kubernetes_cluster_registry: (
            KubernetesClusterRegistry
            | None
        ),
        prometheus_cluster_registry: (
            PrometheusClusterRegistry
            | None
        ),
        tools: ToolManager,
        strict_evidence_required: bool,
    ) -> None:
        if (
            kubernetes_cluster_registry
            is not None
            and not isinstance(
                kubernetes_cluster_registry,
                KubernetesClusterRegistry,
            )
        ):
            raise TypeError(
                "Production readiness Kubernetes registry is invalid"
            )

        if (
            prometheus_cluster_registry
            is not None
            and not isinstance(
                prometheus_cluster_registry,
                PrometheusClusterRegistry,
            )
        ):
            raise TypeError(
                "Production readiness Prometheus registry is invalid"
            )

        if not isinstance(
            tools,
            ToolManager,
        ):
            raise TypeError(
                "Production readiness ToolManager is invalid"
            )

        if not isinstance(
            strict_evidence_required,
            bool,
        ):
            raise TypeError(
                "Production readiness strict-evidence policy is invalid"
            )

        self.kubernetes_cluster_registry = (
            kubernetes_cluster_registry
        )

        self.prometheus_cluster_registry = (
            prometheus_cluster_registry
        )

        self.tools = tools

        self.strict_evidence_required = (
            strict_evidence_required
        )

    @property
    def applicable(
        self,
    ) -> bool:
        return (
            self.kubernetes_cluster_registry
            is not None
            or self.prometheus_cluster_registry
            is not None
        )

    def evaluate_event(
        self,
        event: Any,
    ) -> ProductionClusterReadinessReport:
        cluster, scope_issues = (
            self._cluster_from_event(
                event
            )
        )

        report = self.evaluate(
            cluster
        )

        if not scope_issues:
            return report

        issues = self._unique(
            (
                *scope_issues,
                *report.issues,
            )
        )

        return replace(
            report,
            ready=False,
            issues=issues,
        )

    def evaluate(
        self,
        cluster: str | None,
    ) -> ProductionClusterReadinessReport:
        issues: list[str] = []

        if not self.applicable:
            issues.append(
                "multi_cluster_read_plane_inactive"
            )

        normalized_cluster = (
            self._normalize_cluster(
                cluster
            )
        )

        if normalized_cluster is None:
            issues.append(
                "incident_cluster_missing"
            )

        if not self.strict_evidence_required:
            issues.append(
                "cluster_verified_evidence_policy_inactive"
            )

        kubernetes_present = (
            self.kubernetes_cluster_registry
            is not None
        )

        prometheus_present = (
            self.prometheus_cluster_registry
            is not None
        )

        if not kubernetes_present:
            issues.append(
                "kubernetes_registry_missing"
            )

        if not prometheus_present:
            issues.append(
                "prometheus_registry_missing"
            )

        kubernetes_route_ready = False
        kubernetes_change_route_ready = False
        prometheus_route_ready = False

        if (
            kubernetes_present
            and normalized_cluster
            is not None
        ):
            (
                kubernetes_route_ready,
                kubernetes_change_route_ready,
                kubernetes_issues,
            ) = self._check_kubernetes(
                normalized_cluster
            )

            issues.extend(
                kubernetes_issues
            )

        if (
            prometheus_present
            and normalized_cluster
            is not None
        ):
            (
                prometheus_route_ready,
                prometheus_issues,
            ) = self._check_prometheus(
                normalized_cluster
            )

            issues.extend(
                prometheus_issues
            )

        required_probes = tuple(
            probe.value
            for probe
            in default_investigation_probes()
        )

        covered_probes: list[str] = []

        for probe in default_investigation_probes():
            provider = (
                self._investigation_probe_provider(
                    probe
                )
            )

            if provider == "kubernetes":
                if kubernetes_route_ready:
                    covered_probes.append(
                        probe.value
                    )

                continue

            if provider == "prometheus":
                if prometheus_route_ready:
                    covered_probes.append(
                        probe.value
                    )

                continue

            issues.append(
                "unsupported_required_investigation_probe:"
                + probe.value
            )

        if len(
            covered_probes
        ) != len(
            required_probes
        ):
            issues.append(
                "required_investigation_probe_coverage_incomplete"
            )

        covered_verification_tools: list[
            str
        ] = []

        if kubernetes_route_ready:
            covered_verification_tools.append(
                "kubernetes"
            )

        if prometheus_route_ready:
            covered_verification_tools.append(
                "prometheus"
            )

        if tuple(
            covered_verification_tools
        ) != self._REQUIRED_VERIFICATION_TOOLS:
            issues.append(
                "required_verification_tool_coverage_incomplete"
            )

        normalized_issues = self._unique(
            issues
        )

        ready = (
            self.applicable
            and normalized_cluster
            is not None
            and self.strict_evidence_required
            and kubernetes_route_ready
            and kubernetes_change_route_ready
            and prometheus_route_ready
            and len(
                covered_probes
            )
            == len(
                required_probes
            )
            and tuple(
                covered_verification_tools
            )
            == self._REQUIRED_VERIFICATION_TOOLS
            and not normalized_issues
        )

        return ProductionClusterReadinessReport(
            schema_version="v1",
            read_only=True,
            decision_influence=False,
            applicable=self.applicable,
            ready=ready,
            cluster=normalized_cluster,
            strict_evidence_required=(
                self.strict_evidence_required
            ),
            kubernetes_registry_present=(
                kubernetes_present
            ),
            prometheus_registry_present=(
                prometheus_present
            ),
            kubernetes_route_ready=(
                kubernetes_route_ready
            ),
            kubernetes_change_route_ready=(
                kubernetes_change_route_ready
            ),
            prometheus_route_ready=(
                prometheus_route_ready
            ),
            required_investigation_probes=(
                required_probes
            ),
            covered_investigation_probes=tuple(
                covered_probes
            ),
            required_verification_tools=(
                self._REQUIRED_VERIFICATION_TOOLS
            ),
            covered_verification_tools=tuple(
                covered_verification_tools
            ),
            issues=normalized_issues,
        )

    def evaluate_all(
        self,
    ) -> ProductionMultiClusterCoverageReport:
        issues: list[str] = []

        kubernetes_names = (
            set(
                self.kubernetes_cluster_registry
                .cluster_names
            )
            if self.kubernetes_cluster_registry
            is not None
            else set()
        )

        prometheus_names = (
            set(
                self.prometheus_cluster_registry
                .cluster_names
            )
            if self.prometheus_cluster_registry
            is not None
            else set()
        )

        cluster_names = sorted(
            kubernetes_names
            | prometheus_names
        )

        if not self.applicable:
            issues.append(
                "multi_cluster_read_plane_inactive"
            )

        if (
            self.applicable
            and kubernetes_names
            != prometheus_names
        ):
            issues.append(
                "cluster_registry_coverage_sets_differ"
            )

        if (
            self.applicable
            and not cluster_names
        ):
            issues.append(
                "no_cluster_bindings"
            )

        reports = tuple(
            self.evaluate(
                cluster
            )
            for cluster in cluster_names
        )

        if any(
            not report.ready
            for report in reports
        ):
            issues.append(
                "one_or_more_clusters_not_ready"
            )

        normalized_issues = self._unique(
            issues
        )

        ready = (
            self.applicable
            and bool(
                reports
            )
            and not normalized_issues
            and all(
                report.ready
                for report in reports
            )
        )

        return ProductionMultiClusterCoverageReport(
            schema_version="v1",
            read_only=True,
            decision_influence=False,
            applicable=self.applicable,
            ready=ready,
            clusters=reports,
            issues=normalized_issues,
        )

    def assert_event_ready(
        self,
        event: Any,
    ) -> ProductionClusterReadinessReport:
        report = self.evaluate_event(
            event
        )

        if not report.ready:
            raise ProductionMultiClusterReadinessError(
                "Production multi-cluster read coverage is not ready"
            )

        return report

    def _check_kubernetes(
        self,
        cluster: str,
    ) -> tuple[
        bool,
        bool,
        tuple[str, ...],
    ]:
        issues: list[str] = []

        assert (
            self.kubernetes_cluster_registry
            is not None
        )

        try:
            child = (
                self.kubernetes_cluster_registry
                .resolve(
                    cluster
                )
            )

        except Exception:
            return (
                False,
                False,
                (
                    "kubernetes_cluster_binding_missing",
                ),
            )

        if child.cluster_name != cluster:
            issues.append(
                "kubernetes_child_cluster_identity_invalid"
            )

        if not self._clean_https_origin(
            child.api_url
        ):
            issues.append(
                "kubernetes_https_endpoint_required"
            )

        if child.verify_tls is not True:
            issues.append(
                "kubernetes_tls_verification_required"
            )

        if (
            child.allow_dry_run_fallback
            is not False
        ):
            issues.append(
                "kubernetes_dry_run_fallback_must_be_disabled"
            )

        token = getattr(
            child,
            "bearer_token",
            None,
        )

        if (
            not isinstance(
                token,
                str,
            )
            or not token.strip()
        ):
            issues.append(
                "kubernetes_credential_unresolved"
            )

        ca_file = getattr(
            child,
            "ca_file",
            None,
        )

        if (
            ca_file is not None
            and not Path(
                ca_file
            ).is_file()
        ):
            issues.append(
                "kubernetes_ca_file_unavailable"
            )

        kubernetes_router_ready = False
        kubernetes_change_router_ready = False

        try:
            router = (
                self.tools.registry.get(
                    "kubernetes"
                )
            )

            kubernetes_router_ready = (
                isinstance(
                    router,
                    MultiClusterKubernetesToolRouter,
                )
                and router.clusters
                is self.kubernetes_cluster_registry
            )

        except Exception:
            kubernetes_router_ready = False

        if not kubernetes_router_ready:
            issues.append(
                "kubernetes_runtime_router_mismatch"
            )

        try:
            change_router = (
                self.tools.registry.get(
                    "kubernetes_change"
                )
            )

            kubernetes_change_router_ready = (
                isinstance(
                    change_router,
                    MultiClusterKubernetesChangeToolRouter,
                )
                and change_router.clusters
                is self.kubernetes_cluster_registry
            )

        except Exception:
            kubernetes_change_router_ready = False

        if not kubernetes_change_router_ready:
            issues.append(
                "kubernetes_change_runtime_router_mismatch"
            )

        child_ready = not any(
            issue
            for issue in issues
            if issue.startswith(
                "kubernetes_"
            )
            and issue
            not in {
                "kubernetes_runtime_router_mismatch",
                "kubernetes_change_runtime_router_mismatch",
            }
        )

        return (
            (
                child_ready
                and kubernetes_router_ready
            ),
            (
                child_ready
                and kubernetes_change_router_ready
            ),
            self._unique(
                issues
            ),
        )

    def _check_prometheus(
        self,
        cluster: str,
    ) -> tuple[
        bool,
        tuple[str, ...],
    ]:
        issues: list[str] = []

        assert (
            self.prometheus_cluster_registry
            is not None
        )

        try:
            (
                selected_cluster,
                child,
            ) = (
                self.prometheus_cluster_registry
                .resolve(
                    cluster
                )
            )

        except Exception:
            return (
                False,
                (
                    "prometheus_cluster_binding_missing",
                ),
            )

        if selected_cluster != cluster:
            issues.append(
                "prometheus_child_cluster_identity_invalid"
            )

        if not self._clean_https_origin(
            child.base_url
        ):
            issues.append(
                "prometheus_https_endpoint_required"
            )

        if child.verify_tls is not True:
            issues.append(
                "prometheus_tls_verification_required"
            )

        if (
            child.allow_mock_fallback
            is not False
        ):
            issues.append(
                "prometheus_mock_fallback_must_be_disabled"
            )

        ca_file = getattr(
            child,
            "ca_file",
            None,
        )

        if (
            ca_file is not None
            and not Path(
                ca_file
            ).is_file()
        ):
            issues.append(
                "prometheus_ca_file_unavailable"
            )

        router_ready = False

        try:
            router = (
                self.tools.registry.get(
                    "prometheus"
                )
            )

            router_ready = (
                isinstance(
                    router,
                    MultiClusterPrometheusToolRouter,
                )
                and router.clusters
                is self.prometheus_cluster_registry
            )

        except Exception:
            router_ready = False

        if not router_ready:
            issues.append(
                "prometheus_runtime_router_mismatch"
            )

        child_ready = not any(
            issue
            for issue in issues
            if issue.startswith(
                "prometheus_"
            )
            and issue
            != "prometheus_runtime_router_mismatch"
        )

        return (
            (
                child_ready
                and router_ready
            ),
            self._unique(
                issues
            ),
        )

    @staticmethod
    def _investigation_probe_provider(
        probe: InvestigationProbe,
    ) -> str | None:
        value = probe.value

        if value.startswith(
            "kubernetes_"
        ):
            return "kubernetes"

        if value.startswith(
            "prometheus_"
        ):
            return "prometheus"

        return None

    @staticmethod
    def _cluster_from_event(
        event: Any,
    ) -> tuple[
        str | None,
        tuple[str, ...],
    ]:
        resources = getattr(
            event,
            "resources",
            None,
        )

        if not isinstance(
            resources,
            (
                list,
                tuple,
            ),
        ) or not resources:
            return (
                None,
                (
                    "incident_resource_missing",
                ),
            )

        clusters = set()

        for resource in resources:
            value = getattr(
                resource,
                "cluster",
                None,
            )

            if value is None:
                continue

            normalized = str(
                value
            ).strip()

            if normalized:
                clusters.add(
                    normalized
                )

        if not clusters:
            return (
                None,
                (
                    "incident_cluster_missing",
                ),
            )

        if len(
            clusters
        ) != 1:
            return (
                None,
                (
                    "incident_cluster_ambiguous",
                ),
            )

        return (
            next(
                iter(
                    clusters
                )
            ),
            (),
        )

    @staticmethod
    def _normalize_cluster(
        value: Any,
    ) -> str | None:
        if value is None:
            return None

        if not isinstance(
            value,
            str,
        ):
            return None

        normalized = value.strip()

        if (
            not normalized
            or normalized != value
            or len(
                normalized
            )
            > 256
            or "\x00" in normalized
        ):
            return None

        return normalized

    @staticmethod
    def _clean_https_origin(
        value: Any,
    ) -> bool:
        if not isinstance(
            value,
            str,
        ):
            return False

        parsed = urlparse(
            value
        )

        return (
            parsed.scheme == "https"
            and bool(
                parsed.netloc
            )
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
            and parsed.path in {
                "",
                "/",
            }
        )

    @staticmethod
    def _unique(
        values,
    ) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                str(
                    value
                )
                for value in values
            )
        )


__all__ = [
    "ProductionClusterReadinessReport",
    "ProductionMultiClusterCoverageReport",
    "ProductionMultiClusterReadinessError",
    "ProductionMultiClusterReadinessGate",
]
