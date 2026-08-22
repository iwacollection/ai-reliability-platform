"""Correlate cloud, kubernetes and observability evidence."""

from dataclasses import dataclass, field


@dataclass
class EvidenceLink:
    source: str
    relation: str
    target: str


@dataclass
class EvidenceGraph:
    nodes: list[str] = field(default_factory=list)
    links: list[EvidenceLink] = field(default_factory=list)

    def add_relation(self, source: str, relation: str, target: str):
        self.nodes.extend([source, target])
        self.links.append(EvidenceLink(source, relation, target))
