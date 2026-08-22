"""Evidence relationship engine foundation.

Builds relationships between metrics, logs, traces, events and cloud resources.
"""

from dataclasses import dataclass
from typing import List


@dataclass
class EvidenceRelation:
    source_id: str
    target_id: str
    relation: str
    confidence: float


class EvidenceRelationshipEngine:
    def build(self, evidence_nodes: List[dict]):
        relations = []
        for source in evidence_nodes:
            for target in evidence_nodes:
                if source is target:
                    continue
                if source.get("service") == target.get("service"):
                    relations.append(
                        EvidenceRelation(
                            source.get("id", ""),
                            target.get("id", ""),
                            "same_service",
                            0.5,
                        )
                    )
        return relations
