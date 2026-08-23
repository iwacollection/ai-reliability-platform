"""MCP federation permission policy engine.

Controls whether an agent identity can invoke a capability.
"""


class MCPPermissionPolicy:
    def __init__(self, rules=None):
        self.rules = rules or {}

    def allow(self, identity: str, capability: str, environment: str) -> bool:
        identity_rules = self.rules.get(identity, {})
        allowed = identity_rules.get("capabilities", [])
        environments = identity_rules.get("environments", [])

        return (
            capability in allowed
            and environment in environments
        )

    def register_identity(self, identity: str, capabilities: list[str], environments: list[str]):
        self.rules[identity] = {
            "capabilities": capabilities,
            "environments": environments,
        }
