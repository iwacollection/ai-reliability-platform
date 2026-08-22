"""MCP Federation Layer.

Provides unified routing across multiple environments:
- production Kubernetes clusters
- cloud accounts
- on-prem infrastructure
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class MCPEnvironment:
    name: str
    region: str
    provider: str
    servers: List[str] = field(default_factory=list)


class MCPFederation:
    def __init__(self):
        self.environments: Dict[str, MCPEnvironment] = {}

    def register_environment(self, environment: MCPEnvironment):
        self.environments[environment.name] = environment

    def discover(self, capability: str, environment: Optional[str] = None):
        targets = self.environments.values()
        if environment:
            targets = [self.environments[environment]]

        result = []
        for env in targets:
            for server in env.servers:
                if capability in server:
                    result.append({"environment": env.name, "server": server})
        return result
