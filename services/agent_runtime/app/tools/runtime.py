from typing import Any

from services.agent_runtime.app.mcp.registry import MCPRegistry


class ToolRuntime:
    def __init__(self, registry: MCPRegistry) -> None:
        self.registry = registry

    def execute(self, tool_name: str, **kwargs: Any) -> Any:
        tool = self.registry.get(tool_name)
        if tool is None:
            raise ValueError(f"unknown tool: {tool_name}")
        if tool.handler is None:
            raise RuntimeError(f"tool has no handler: {tool_name}")
        return tool.handler(**kwargs)
