from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class ReasoningNode:
    node_id: str
    node_type: str
    value: str


@dataclass
class ReasoningEdge:
    source: str
    target: str
    relation: str


class RCAReasoningGraph:
    """
    Evidence -> Hypothesis -> Root Cause reasoning graph.
    """

    def __init__(self):
        self.nodes: Dict[str, ReasoningNode] = {}
        self.edges: List[ReasoningEdge] = []

    def add_node(self, node: ReasoningNode):
        self.nodes[node.node_id] = node

    def connect(self, source: str, target: str, relation: str):
        self.edges.append(
            ReasoningEdge(
                source=source,
                target=target,
                relation=relation,
            )
        )

    def explain(self):
        return {
            "nodes": list(self.nodes.values()),
            "edges": self.edges,
        }
