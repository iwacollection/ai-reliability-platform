from dataclasses import dataclass, field
from typing import Any


@dataclass
class KubernetesToolRequest:
    """Normalized MCP request for Kubernetes operations."""

    tool: str
    namespace: str | None = None
    resource: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class KubernetesToolResponse:
    """Normalized Kubernetes evidence response."""

    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
