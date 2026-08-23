"""MCP client runtime boundary.

Initial implementation keeps transport isolated so stdio/http transports
can be added without changing federation routing.
"""

from collections.abc import Callable
from typing import Any

from .models import MCPToolCall, MCPToolResult


class MCPClientRuntime:
    def __init__(self):
        self._servers: dict[str, Callable[[str, dict[str, Any]], Any]] = {}

    def register_server(
        self,
        server_name: str,
        handler: Callable[[str, dict[str, Any]], Any],
    ) -> None:
        self._servers[server_name] = handler

    def call_tool(self, request: MCPToolCall) -> MCPToolResult:
        handler = self._servers.get(request.server)

        if handler is None:
            return MCPToolResult(
                success=False,
                content=None,
                error="mcp server unavailable",
            )

        try:
            result = handler(request.tool, request.arguments)
            return MCPToolResult(success=True, content=result)
        except Exception as exc:
            return MCPToolResult(
                success=False,
                content=None,
                error=str(exc),
            )
