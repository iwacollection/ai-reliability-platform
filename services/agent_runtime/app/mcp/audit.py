from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class MCPAuditRecord:
    tool: str
    action: str
    request: dict[str, Any]
    response: dict[str, Any] | None
    decision: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class MCPAuditLogger:
    def __init__(self):
        self.records: list[MCPAuditRecord] = []

    def record(self, item: MCPAuditRecord):
        self.records.append(item)

    def replay(self):
        return list(self.records)
