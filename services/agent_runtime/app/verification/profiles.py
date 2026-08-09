from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from typing import Any

from services.agent_runtime.app.action.models import (
    ActionPlan,
    ActionType,
)
from services.agent_runtime.app.verification.collector import (
    VerificationEvaluation,
    VerificationProbe,
)
from services.agent_runtime.app.verification.models import (
    VerificationSource,
)


class VerificationProfileError(ValueError):
    """A safe verification profile cannot be built."""


@dataclass(frozen=True, slots=True)
class VerificationProfile:
    """
    Immutable verification definition for one remediation action.

    A profile only declares read-only probes and evaluators. It does not
    execute tools, persist Verification results, or update Incident state.
    """

    name: str
    action: ActionType
    target: str
    namespace: str
    cluster: str | None
    probes: tuple[VerificationProbe, ...]

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise VerificationProfileError(
                "Verification profile name cannot be empty"
            )

        if not self.target.strip():
            raise VerificationProfileError(
                "Verification target cannot be empty"
            )

        if not self.namespace.strip():
            raise VerificationProfileError(
                "Verification namespace cannot be empty"
            )

        if not self.probes:
            raise VerificationProfileError(
                "Verification profile requires at least one probe"
            )

        if not any(probe.required for probe in self.probes):
            raise VerificationProfileError(
                "Verification profile requires a required probe"
            )


class VerificationProfileFactory:
    """
    Build deterministic action-specific verification profiles.

    The first profile supports INCREASE_MEMORY_LIMIT. Natural-language text
    from ActionPlan.metadata["verification"] is deliberately not parsed as a
    production rule.
    """

    def __init__(
        self,
        memory_utilization_threshold: float = 0.90,
        restart_increase_threshold: float = 0.0,
        restart_window: str = "5m",
    ) -> None:
        if not 0.0 < memory_utilization_threshold <= 1.0:
            raise ValueError(
                "memory_utilization_threshold must be in (0, 1]"
            )

        if restart_increase_threshold < 0:
            raise ValueError(
                "restart_increase_threshold cannot be negative"
            )

        normalized_window = restart_window.strip()
        if not normalized_window or not normalized_window.isalnum():
            raise ValueError(
                "restart_window must be a Prometheus duration"
            )

        self.memory_utilization_threshold = float(
            memory_utilization_threshold
        )
        self.restart_increase_threshold = float(
            restart_increase_threshold
        )
        self.restart_window = normalized_window

    def create(
        self,
        plan: ActionPlan,
        *,
        namespace: str | None = None,
        cluster: str | None = None,
    ) -> VerificationProfile:
        """
        Build a profile without executing any probe.

        The caller supplies scope from the StandardEvent resource because the
        current ActionPlan does not persist namespace or cluster as fields.
        """

        target = self._normalize_target(plan.target)
        namespace = self._normalize_namespace(namespace)
        cluster = self._normalize_cluster(cluster)

        if plan.type == ActionType.INCREASE_MEMORY_LIMIT:
            return self._increase_memory_limit(
                target=target,
                namespace=namespace,
                cluster=cluster,
            )

        raise VerificationProfileError(
            "No verification profile is registered for action: "
            f"{plan.type.value}"
        )

    def _increase_memory_limit(
        self,
        *,
        target: str,
        namespace: str,
        cluster: str | None,
    ) -> VerificationProfile:
        kubernetes_arguments: dict[str, Any] = {
            "action": "describe",
            "resource": "pod",
            "target": target,
            "namespace": namespace,
        }
        if cluster is not None:
            kubernetes_arguments["cluster"] = cluster

        memory_arguments: dict[str, Any] = {
            "query": self._memory_utilization_query(
                target=target,
                namespace=namespace,
                cluster=cluster,
            )
        }

        restart_arguments: dict[str, Any] = {
            "query": self._restart_increase_query(
                target=target,
                namespace=namespace,
                cluster=cluster,
            )
        }

        if cluster is not None:
            memory_arguments[
                "cluster"
            ] = cluster

            restart_arguments[
                "cluster"
            ] = cluster

        probes = (
            VerificationProbe(
                name="pod_ready_after_memory_increase",
                source=VerificationSource.WORKLOAD,
                tool="kubernetes",
                provider="kubernetes",
                arguments=kubernetes_arguments,
                evaluator=_evaluate_pod_ready,
                required=True,
            ),
            VerificationProbe(
                name="memory_headroom_after_memory_increase",
                source=VerificationSource.METRIC,
                tool="prometheus",
                provider="prometheus",
                arguments=memory_arguments,
                evaluator=_build_upper_bound_evaluator(
                    threshold=self.memory_utilization_threshold,
                    unit="ratio",
                    success_message=(
                        "Container memory utilization is within limit"
                    ),
                    failure_message=(
                        "Container memory utilization remains too high"
                    ),
                ),
                required=True,
            ),
            VerificationProbe(
                name="pod_restart_stability_after_memory_increase",
                source=VerificationSource.METRIC,
                tool="prometheus",
                provider="prometheus",
                arguments=restart_arguments,
                evaluator=_build_upper_bound_evaluator(
                    threshold=self.restart_increase_threshold,
                    unit="restarts",
                    success_message="Pod restart count is stable",
                    failure_message="Pod continues to restart",
                ),
                # Applying the remediation itself may restart the Pod. Without
                # a pre-action baseline this signal must not block resolution.
                required=False,
            ),
        )

        return VerificationProfile(
            name="increase_memory_limit_v1",
            action=ActionType.INCREASE_MEMORY_LIMIT,
            target=target,
            namespace=namespace,
            cluster=cluster,
            probes=probes,
        )

    @staticmethod
    def _memory_utilization_query(
        *,
        target: str,
        namespace: str,
        cluster: str | None,
    ) -> str:
        selector = _container_selector(
            target=target,
            namespace=namespace,
            cluster=cluster,
        )
        return (
            "max("
            f"container_memory_working_set_bytes{{{selector}}}"
            ") / clamp_min(max("
            f"container_spec_memory_limit_bytes{{{selector}}}"
            "), 1)"
        )

    def _restart_increase_query(
        self,
        *,
        target: str,
        namespace: str,
        cluster: str | None,
    ) -> str:
        labels = [
            ("pod", target),
            ("namespace", namespace),
        ]
        if cluster is not None:
            labels.append(("cluster", cluster))

        selector = ",".join(
            f'{name}="{_escape_label_value(value)}"'
            for name, value in labels
        )
        return (
            "sum(increase("
            "kube_pod_container_status_restarts_total"
            f"{{{selector}}}[{self.restart_window}]"
            "))"
        )

    @staticmethod
    def _normalize_target(value: Any) -> str:
        target = str(value if value is not None else "").strip()
        if not target or target.lower() == "unknown":
            raise VerificationProfileError(
                "Verification requires a concrete action target"
            )
        return target

    @staticmethod
    def _normalize_namespace(value: Any) -> str:
        namespace = str(value if value is not None else "").strip()
        return namespace or "default"

    @staticmethod
    def _normalize_cluster(value: Any) -> str | None:
        cluster = str(value if value is not None else "").strip()
        return cluster or None


def _evaluate_pod_ready(
    evidence: Mapping[str, Any],
) -> VerificationEvaluation:
    data = evidence.get("data")
    if not isinstance(data, Mapping):
        return VerificationEvaluation(
            passed=None,
            message=(
                "Kubernetes evidence does not contain normalized pod data"
            ),
        )

    phase = str(data.get("phase", "")).strip()
    ready = _read_bool(data, "ready", "pod_ready")
    scheduled = _read_bool(data, "scheduled", "pod_scheduled")

    observed_value = {
        "phase": phase or None,
        "ready": ready,
        "scheduled": scheduled,
        "restart_count": data.get("restart_count"),
        "oom_killed": data.get("oom_killed"),
    }
    expected_value = {
        "phase": "Running",
        "ready": True,
        "scheduled": True,
    }

    if not phase or ready is None or scheduled is None:
        return VerificationEvaluation(
            passed=None,
            observed_value=observed_value,
            expected_value=expected_value,
            message="Kubernetes pod readiness evidence is incomplete",
        )

    passed = (
        phase.lower() == "running"
        and ready is True
        and scheduled is True
    )
    return VerificationEvaluation(
        passed=passed,
        observed_value=observed_value,
        expected_value=expected_value,
        message=(
            "Pod is running, scheduled, and ready"
            if passed
            else "Pod is not ready after remediation"
        ),
        metadata={"evaluation": "pod_readiness"},
    )


def _build_upper_bound_evaluator(
    *,
    threshold: float,
    unit: str,
    success_message: str,
    failure_message: str,
):
    def evaluate(
        evidence: Mapping[str, Any],
    ) -> VerificationEvaluation:
        values = _prometheus_values(evidence)
        expected_value = {
            "operator": "<=",
            "threshold": threshold,
            "unit": unit,
        }

        if not values:
            return VerificationEvaluation(
                passed=None,
                expected_value=expected_value,
                message="Prometheus evidence contains no numeric samples",
            )

        observed = max(values)
        passed = observed <= threshold
        return VerificationEvaluation(
            passed=passed,
            observed_value=observed,
            expected_value=expected_value,
            message=success_message if passed else failure_message,
            metadata={
                "aggregation": "max",
                "sample_count": len(values),
            },
        )

    return evaluate


def _prometheus_values(
    evidence: Mapping[str, Any],
) -> list[float]:
    """Read normalized vector, scalar, and legacy metric containers."""

    candidates: list[Any] = []
    data = evidence.get("data")

    if isinstance(data, Mapping):
        candidates.extend(
            data[key]
            for key in ("samples", "result", "value")
            if key in data
        )
    elif data is not None:
        candidates.append(data)

    candidates.extend(
        evidence[key]
        for key in ("samples", "result", "value", "metrics")
        if key in evidence
    )

    values: list[float] = []
    for candidate in candidates:
        values.extend(_numeric_samples(candidate))
    return values


def _numeric_samples(value: Any) -> list[float]:
    direct = _as_finite_float(value)
    if direct is not None:
        return [direct]

    if isinstance(value, Mapping):
        for key in ("sample_value", "value"):
            if key in value:
                return _numeric_samples(value[key])

        if "values" in value:
            matrix = value["values"]
            if isinstance(matrix, Sequence) and not isinstance(
                matrix,
                (str, bytes),
            ):
                return [
                    parsed
                    for sample in matrix
                    if (parsed := _sample_pair_value(sample)) is not None
                ]

        return [
            parsed
            for nested in value.values()
            if (parsed := _as_finite_float(nested)) is not None
        ]

    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes),
    ):
        is_sample_pair = (
            len(value) == 2
            and _as_finite_float(value[0])
            is not None
        )

        if is_sample_pair:
            pair_value = _sample_pair_value(
                value
            )

            # A Prometheus sample pair with an invalid value must not fall
            # through and expose its timestamp as a metric value.
            return (
                [pair_value]
                if pair_value is not None
                else []
            )

        values: list[float] = []
        for item in value:
            values.extend(_numeric_samples(item))
        return values

    return []


def _sample_pair_value(value: Any) -> float | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return None
    if len(value) != 2:
        return None

    timestamp = _as_finite_float(value[0])
    sample = _as_finite_float(value[1])
    if timestamp is None or sample is None:
        return None
    return sample


def _as_finite_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None

    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None

    return parsed if isfinite(parsed) else None


def _read_bool(
    data: Mapping[str, Any],
    *keys: str,
) -> bool | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, bool):
            return value
    return None


def _container_selector(
    *,
    target: str,
    namespace: str,
    cluster: str | None,
) -> str:
    selectors = [
        f'pod="{_escape_label_value(target)}"',
        f'namespace="{_escape_label_value(namespace)}"',
        'container!="POD"',
        'container!=""',
        'image!=""',
    ]
    if cluster is not None:
        selectors.append(
            f'cluster="{_escape_label_value(cluster)}"'
        )
    return ",".join(selectors)


def _escape_label_value(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )
