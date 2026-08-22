from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class MCPServerCapability:
    name: str
    description: str
    permissions: List[str] = field(default_factory=list)


@dataclass
class MCPServerMetadata:
    name: str
    endpoint: str
    capabilities: List[MCPServerCapability] = field(default_factory=list)


class MCPDynamicDiscovery:
    def __init__(self):
        self.servers: Dict[str, MCPServerMetadata] = {}

    def register(self, server: MCPServerMetadata):
        self.servers[server.name] = server

    def discover(self, capability: str):
        result = []
        for server in self.servers.values():
            if any(c.name == capability for c in server.capabilities):
                result.append(server)
        return result
