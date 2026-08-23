"""Shared runtime store for MCP federation components."""

from .registry import MCPRegistry


_registry = MCPRegistry()


def get_registry() -> MCPRegistry:
    """Return process-level MCP registry instance."""

    return _registry
