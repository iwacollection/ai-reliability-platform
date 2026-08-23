from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class IncidentMemory:
    incident_id: str
    root_cause: str
    remediation: str
    verification: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class IncidentMemoryStore:
    def save(self, memory: IncidentMemory) -> IncidentMemory:
        return memory
