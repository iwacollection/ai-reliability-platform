"""
Autonomous Investigation Policy Engine.

Controls when the investigation loop should continue, stop, or require human review.
"""

from dataclasses import dataclass


@dataclass
class InvestigationDecision:
    action: str
    reason: str


class InvestigationPolicy:
    def __init__(self, confidence_threshold: float = 0.9, max_steps: int = 10):
        self.confidence_threshold = confidence_threshold
        self.max_steps = max_steps

    def decide(self, confidence: float, step_count: int, high_risk_action: bool = False):
        if high_risk_action:
            return InvestigationDecision("human_review", "high risk action requires approval")

        if confidence >= self.confidence_threshold:
            return InvestigationDecision("stop", "root cause confidence reached threshold")

        if step_count >= self.max_steps:
            return InvestigationDecision("stop", "maximum investigation steps reached")

        return InvestigationDecision("continue", "more evidence required")
