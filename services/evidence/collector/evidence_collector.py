"""
Evidence Collector Runtime.

Collects connector results and converts them into normalized evidence objects.
"""

from datetime import datetime, timezone
from typing import Any

from services.evidence.models.evidence import Evidence


class EvidenceCollector:
    def collect(
        self,
        source: str,
        kind: str,
        resource: dict[str, str],
        signal: dict[str, Any],
        correlation_id: str | None = None,
    ) -> Evidence:
        return Evidence(
            source=source,
            kind=kind,
            timestamp=datetime.now(timezone.utc),
            resource=resource,
            signal=signal,
            confidence=0.8,
            correlation_id=correlation_id,
        )
