"""MCP execution audit records."""

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class MCPAuditEvent:
    identity: str
    provider_id: str
    tool_name: str
    success: bool
    timestamp: str


class MCPAuditLogger:
    def __init__(self):
        self.events: list[MCPAuditEvent] = []

    def record(
        self,
        identity: str,
        provider_id: str,
        tool_name: str,
        success: bool,
    ) -> MCPAuditEvent:
        event = MCPAuditEvent(
            identity=identity,
            provider_id=provider_id,
            tool_name=tool_name,
            success=success,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        self.events.append(event)
        return event
