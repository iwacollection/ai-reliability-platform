"""SLO driven decision policy."""
from dataclasses import dataclass


@dataclass
class SLOSignal:
    availability_error_budget: float
    latency_breach: bool
    business_impact: bool


class SLOAwarePolicy:
    def decide(self, signal: SLOSignal) -> str:
        if signal.business_impact:
            return "HUMAN_APPROVAL_REQUIRED"
        if signal.latency_breach and signal.availability_error_budget < 0.2:
            return "ALLOW_LOW_RISK_REMEDIATION"
        return "OBSERVE_ONLY"
