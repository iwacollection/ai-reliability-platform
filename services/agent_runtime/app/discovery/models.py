from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class DiscoveryObservation(BaseModel):
    """A read-only observation collected without requiring an alert."""

    source: str
    kind: str
    resource: dict[str, Any]
    signal: dict[str, Any]
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    fingerprint: str | None = None


class DiscoveryFinding(BaseModel):
    """An abnormal observation promoted by a deterministic discovery rule."""

    rule_id: str
    severity: str
    title: str
    summary: str
    score: float
    observation: DiscoveryObservation
    should_investigate: bool = False
    evidence: dict[str, Any] = Field(default_factory=dict)


class DiscoveryBatch(BaseModel):
    """Result of one proactive discovery scan."""

    scanned: int
    findings: list[DiscoveryFinding] = Field(default_factory=list)
    promoted: list[DiscoveryFinding] = Field(default_factory=list)
