"""
Evidence domain model for AI Reliability Runtime.

Phase 6.3.4.1 foundation:
Connector output must be normalized before entering investigation.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class Evidence:
    source: str
    kind: str
    timestamp: datetime
    resource: dict[str, str]
    signal: dict[str, Any]
    confidence: float = 0.0
    correlation_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
