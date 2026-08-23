"""
ScenarioReplayEngine + Real Kubernetes Evidence Integration

Phase 6.3.4.1.3

Purpose:
- Replace mock-only replay evidence with real Kubernetes evidence adapters.
- Keep replay engine independent from Kubernetes implementation.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ReplayEvidenceBundle:
    scenario_id: str
    source: str
    evidence: list[dict[str, Any]] = field(default_factory=list)


class KubernetesScenarioEvidenceProvider:
    """Adapter boundary between Kubernetes connector and replay runtime."""

    def __init__(self, collector):
        self.collector = collector

    def collect(self, resource_ref: dict[str, str]) -> ReplayEvidenceBundle:
        evidence = self.collector.collect(resource_ref)
        return ReplayEvidenceBundle(
            scenario_id=resource_ref.get("scenario_id", "unknown"),
            source="kubernetes",
            evidence=evidence,
        )


class ScenarioReplayRuntime:
    """Run incident scenarios using real or simulated evidence sources."""

    def __init__(self, evidence_provider):
        self.evidence_provider = evidence_provider

    def replay(self, scenario: dict[str, str]) -> ReplayEvidenceBundle:
        return self.evidence_provider.collect(scenario)
