"""Production MCP route admission checks."""


class MCPAccessController:
    def __init__(self, discovery, policy):
        self.discovery = discovery
        self.policy = policy

    def resolve(self, identity: str, capability: str, environment: str):
        if not self.policy.allow(identity, capability, environment):
            return []

        return self.discovery.discover(capability, environment)
