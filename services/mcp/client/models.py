"""Models used by MCP protocol client."""

from dataclasses import dataclass
from typing import Any


@dataclass
class MCPToolCall:
    server: str
    tool: str
    arguments: dict[str, Any]


@dataclass
class MCPToolResult:
    success: bool
    content: Any
    error: str | None = None
