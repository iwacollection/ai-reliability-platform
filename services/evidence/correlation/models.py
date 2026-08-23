from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class EvidenceNode:
    evidence_id: str
    evidence_type: str
    source: str
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EvidenceRelation:
    source_id: str
    target_id: str
    relation: str
    confidence: float


@dataclass
class CorrelationResult:
    nodes: List[EvidenceNode]
    relations: List[EvidenceRelation]
