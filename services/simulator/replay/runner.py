from .models import IncidentReplayScenario, ReplayResult


class IncidentReplayRunner:
    """Execute multi signal incident replay scenarios."""

    def run(self, scenario: IncidentReplayScenario) -> ReplayResult:
        evidence_count = len(scenario.signals)
        return ReplayResult(
            scenario=scenario.name,
            rca="unknown",
            confidence=0.0,
            evidence_count=evidence_count,
        )
