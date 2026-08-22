from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class MCPServer:
    name: str
    endpoint: str
    capabilities: list[str]


class MCPServerRegistry:
    def __init__(self):
        self.servers: dict[str, MCPServer] = {}

    def register(self, server: MCPServer):
        self.servers[server.name] = server

    def discover(self):
        return list(self.servers.values())


class MCPClient:
    def __init__(self, registry: MCPServerRegistry):
        self.registry = registry

    def call_tool(self, server: str, tool: str, payload: dict[str, Any]):
        target = self.registry.servers[server]
        return {
            "server": target.name,
            "tool": tool,
            "payload": payload,
            "status": "dispatched"
        }
