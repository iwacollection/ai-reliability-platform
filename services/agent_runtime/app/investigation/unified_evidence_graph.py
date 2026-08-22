"""Unified evidence graph foundation.

Correlates metrics, logs, traces, kubernetes events and cloud resources.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EvidenceNode:
    kind: str
    source: str
    value: Any
    relations: list[str] = field(default_factory=list)


class UnifiedEvidenceGraph:
    def __init__(self):
        self.nodes: list[EvidenceNode] = []

    def add(self, node: EvidenceNode):
        self.nodes.append(node)

    def correlate(self):
        return self.nodes
