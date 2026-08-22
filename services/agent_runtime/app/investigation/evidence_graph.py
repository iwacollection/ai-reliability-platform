from dataclasses import dataclass, field
from typing import Any

from .evidence_collector import Evidence


@dataclass
class EvidenceNode:
    node_id: str
    evidence: Evidence
    relations: list[str] = field(default_factory=list)


class EvidenceGraph:
    """Evidence relationship graph for RCA reasoning."""

    def __init__(self) -> None:
        self.nodes: dict[str, EvidenceNode] = {}

    def add(self, node_id: str, evidence: Evidence) -> None:
        self.nodes[node_id] = EvidenceNode(
            node_id=node_id,
            evidence=evidence,
        )

    def link(self, source_id: str, target_id: str) -> None:
        if source_id in self.nodes:
            self.nodes[source_id].relations.append(target_id)

    def snapshot(self) -> dict[str, Any]:
        return {
            key: {
                "type": value.evidence.evidence_type,
                "source": value.evidence.source,
                "relations": value.relations,
            }
            for key, value in self.nodes.items()
        }
