from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Any


@dataclass
class MCPAuditEvent:
    """Immutable security audit record for MCP execution flow."""

    event_type: str
    principal: str
    tenant_id: str
    tool: str
    decision: str
    timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    metadata: Dict[str, Any] = field(default_factory=dict)
