from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class IncidentReplayScenario:
    name: str
    signals: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class ReplayResult:
    scenario: str
    rca: str
    confidence: float
    evidence_count: int
