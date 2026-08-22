from dataclasses import dataclass


@dataclass
class HealthCheckResult:
    healthy: bool
    checks: list[str]


class HealthChecker:
    def check(self, service: str) -> HealthCheckResult:
        return HealthCheckResult(
            healthy=True,
            checks=[f"health_check:{service}"],
        )
