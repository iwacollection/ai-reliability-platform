"""MCP Tool Execution Gateway.

Provides controlled execution boundary between Agent Runtime and MCP providers.
"""

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class ToolExecutionRequest:
    provider_id: str
    tool_name: str
    arguments: dict[str, Any]


@dataclass
class ToolExecutionResult:
    success: bool
    provider_id: str
    tool_name: str
    output: Any = None
    error: str | None = None


class MCPExecutionGateway:
    def __init__(self):
        self._handlers: dict[tuple[str, str], Callable[..., Any]] = {}

    def register_tool(
        self,
        provider_id: str,
        tool_name: str,
        handler: Callable[..., Any],
    ) -> None:
        self._handlers[(provider_id, tool_name)] = handler

    def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult:
        handler = self._handlers.get(
            (request.provider_id, request.tool_name)
        )

        if handler is None:
            return ToolExecutionResult(
                success=False,
                provider_id=request.provider_id,
                tool_name=request.tool_name,
                error="tool handler not registered",
            )

        try:
            output = handler(**request.arguments)
            return ToolExecutionResult(
                success=True,
                provider_id=request.provider_id,
                tool_name=request.tool_name,
                output=output,
            )
        except Exception as exc:
            return ToolExecutionResult(
                success=False,
                provider_id=request.provider_id,
                tool_name=request.tool_name,
                error=str(exc),
            )
