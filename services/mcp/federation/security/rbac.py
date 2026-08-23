"""MCP Federation RBAC (Role Based Access Control) foundation.

Provides role -> permission mapping used by the federation policy engine.
"""

from dataclasses import dataclass
from typing import FrozenSet


@dataclass(frozen=True)
class MCPRole:
    name: str
    permissions: FrozenSet[str]


DEFAULT_ROLES = {
    "mcp-admin": MCPRole(
        name="mcp-admin",
        permissions=frozenset({"*"}),
    ),
    "mcp-reader": MCPRole(
        name="mcp-reader",
        permissions=frozenset(
            {
                "kubernetes.read",
                "logs.read",
                "metrics.read",
                "traces.read",
            }
        ),
    ),
}


class RBACEngine:
    def __init__(self, roles=None):
        self.roles = roles or DEFAULT_ROLES

    def allowed(self, role_names: list[str], permission: str) -> bool:
        for role_name in role_names:
            role = self.roles.get(role_name)
            if role and ("*" in role.permissions or permission in role.permissions):
                return True
        return False
