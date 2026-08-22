from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class MCPToolSpec:
    name: str
    description: str
    permission: str = "readonly"
    handler: Callable[..., Any] | None = None


class MCPRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, MCPToolSpec] = {}

    def register(self, tool: MCPToolSpec) -> None:
        self._tools[tool.name] = tool

    def discover(self) -> list[MCPToolSpec]:
        return list(self._tools.values())

    def get(self, name: str) -> MCPToolSpec | None:
        return self._tools.get(name)
