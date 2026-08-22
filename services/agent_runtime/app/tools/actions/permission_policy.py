"""Safety policy boundary for autonomous remediation."""


class ActionPermissionPolicy:
    def __init__(self):
        self.allowed = {
            "restart_pod": "medium",
            "scale_deployment": "high",
        }

    def check(self, action: str, approved: bool) -> bool:
        if action not in self.allowed:
            return False
        return approved

    def risk_level(self, action: str):
        return self.allowed.get(action, "unknown")
