"""
Risk Engine foundation.

Controls whether an agent action can execute automatically,
requires approval, or must be blocked.
"""

from enum import Enum


class ActionRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RiskEngine:
    def evaluate(self, action: str) -> ActionRisk:
        dangerous = {
            "terraform_destroy",
            "database_delete",
            "cluster_shutdown",
        }
        if action in dangerous:
            return ActionRisk.HIGH
        return ActionRisk.LOW
