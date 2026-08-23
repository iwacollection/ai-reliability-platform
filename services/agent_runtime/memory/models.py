from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class IncidentMemory:
    """Long-term incident knowledge record."""

    incident_id: str
    service: str
    root_cause: str
    resolution: str
    confidence: float = 0.0
    evidence: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class MemoryQuery:
    """Query used by agents to retrieve historical knowledge."""

    service: str | None = None
    symptom: str | None = None
    root_cause: str | None = None
    limit: int = 5
