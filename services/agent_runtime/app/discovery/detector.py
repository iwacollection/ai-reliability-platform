from services.agent_runtime.app.discovery.models import (
    DiscoveryFinding,
    DiscoveryObservation,
)


class DiscoveryDetector:
    """Deterministic first-stage detector for common Kubernetes failures.

    This intentionally avoids LLM judgment. The discovery layer should produce
    explainable candidates; Investigation/RCA can perform deeper reasoning.
    """

    def evaluate(self, observation: DiscoveryObservation) -> list[DiscoveryFinding]:
        kind = observation.kind.lower()

        if kind == "pod":
            return self._evaluate_pod(observation)
        if kind == "event":
            return self._evaluate_event(observation)
        if kind == "node":
            return self._evaluate_node(observation)
        if kind == "deployment":
            return self._evaluate_deployment(observation)

        return []

    def _finding(
        self,
        observation: DiscoveryObservation,
        *,
        rule_id: str,
        severity: str,
        title: str,
        summary: str,
        score: float,
    ) -> DiscoveryFinding:
        return DiscoveryFinding(
            rule_id=rule_id,
            severity=severity,
            title=title,
            summary=summary,
            score=score,
            observation=observation,
            evidence={
                "resource": observation.resource,
                "signal": observation.signal,
            },
        )

    def _evaluate_pod(self, observation: DiscoveryObservation) -> list[DiscoveryFinding]:
        signal = observation.signal
        findings: list[DiscoveryFinding] = []
        waiting_reason = str(signal.get("waiting_reason") or signal.get("reason") or "")
        last_termination_reason = str(signal.get("last_termination_reason") or "")
        restart_count = int(signal.get("restart_count") or 0)

        if waiting_reason == "CrashLoopBackOff" or restart_count >= 5:
            findings.append(
                self._finding(
                    observation,
                    rule_id="k8s.pod.crashloop",
                    severity="high",
                    title="Pod repeatedly restarting",
                    summary=(
                        f"Pod is in {waiting_reason or 'repeated restart'} state "
                        f"with restart_count={restart_count}."
                    ),
                    score=0.92,
                )
            )

        if last_termination_reason == "OOMKilled":
            findings.append(
                self._finding(
                    observation,
                    rule_id="k8s.pod.oomkilled",
                    severity="high",
                    title="Pod container was OOMKilled",
                    summary="A container was terminated by the kernel because its memory limit was exceeded.",
                    score=0.95,
                )
            )

        return findings

    def _evaluate_event(self, observation: DiscoveryObservation) -> list[DiscoveryFinding]:
        reason = str(observation.signal.get("reason") or "")
        if reason not in {"ImagePullBackOff", "ErrImagePull"}:
            return []

        return [
            self._finding(
                observation,
                rule_id="k8s.image.pull_failure",
                severity="high",
                title="Container image pull is failing",
                summary=f"Kubernetes reported {reason}; investigate image reference, registry reachability, and registry credentials.",
                score=0.93,
            )
        ]

    def _evaluate_node(self, observation: DiscoveryObservation) -> list[DiscoveryFinding]:
        signal = observation.signal
        pressure_names = ("MemoryPressure", "DiskPressure", "PIDPressure")
        active = [name for name in pressure_names if bool(signal.get(name))]
        if not active:
            return []

        return [
            self._finding(
                observation,
                rule_id="k8s.node.pressure",
                severity="high",
                title="Kubernetes node is under resource pressure",
                summary=f"Active node conditions: {', '.join(active)}.",
                score=0.9,
            )
        ]

    def _evaluate_deployment(self, observation: DiscoveryObservation) -> list[DiscoveryFinding]:
        signal = observation.signal
        desired = int(signal.get("desired_replicas") or 0)
        available = int(signal.get("available_replicas") or 0)

        if desired <= 0 or available >= desired:
            return []

        severity = "high" if available == 0 else "medium"
        score = 0.91 if available == 0 else 0.8
        return [
            self._finding(
                observation,
                rule_id="k8s.deployment.replica_deficit",
                severity=severity,
                title="Deployment has unavailable replicas",
                summary=f"Deployment desired_replicas={desired}, available_replicas={available}.",
                score=score,
            )
        ]
