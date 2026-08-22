from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class Evidence:
    source: str
    evidence_type: str
    timestamp: str
    resource: str
    data: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0


class EvidenceCollector:
    """Normalize tool outputs into investigation evidence objects."""

    def collect(
        self,
        source: str,
        evidence_type: str,
        resource: str,
        data: dict[str, Any],
        confidence: float = 0.5,
    ) -> Evidence:
        return Evidence(
            source=source,
            evidence_type=evidence_type,
            timestamp=datetime.now(timezone.utc).isoformat(),
            resource=resource,
            data=data,
            confidence=confidence,
        )
