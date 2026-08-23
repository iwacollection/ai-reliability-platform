from typing import List

from .audit_event import MCPAuditEvent


class MCPAuditStore:
    """Minimal audit persistence abstraction.

    Production implementation can be backed by PostgreSQL,
    Elasticsearch, Azure Monitor or a compliance data lake.
    """

    def __init__(self):
        self._events: List[MCPAuditEvent] = []

    def append(self, event: MCPAuditEvent) -> None:
        self._events.append(event)

    def query_by_tenant(self, tenant_id: str) -> List[MCPAuditEvent]:
        return [
            event
            for event in self._events
            if event.tenant_id == tenant_id
        ]
