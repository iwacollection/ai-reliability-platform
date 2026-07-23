from common.domain.event import StandardEvent

from services.noise_agent.app.models import NoiseResult


class RuleEngine:

    def evaluate(
        self,
        event: StandardEvent,
    ) -> NoiseResult:

        severity = event.signal.severity

        if severity == "info":
            return NoiseResult(
                is_noise=True,
                score=0.99,
                reason="Informational alert.",
            )

        if severity == "warning":
            return NoiseResult(
                is_noise=False,
                score=0.60,
                reason="Need further analysis.",
            )

        return NoiseResult(
            is_noise=False,
            score=0.05,
            reason="Critical alert.",
        )