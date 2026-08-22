from dataclasses import dataclass, field
from typing import List


@dataclass
class RetrievalEvaluation:
    query: str
    expected_incidents: List[str]
    retrieved_incidents: List[str]
    precision: float = 0.0
    recall: float = 0.0


class RAGEvaluator:
    def evaluate(self, item: RetrievalEvaluation):
        expected = set(item.expected_incidents)
        retrieved = set(item.retrieved_incidents)

        if retrieved:
            item.precision = len(expected & retrieved) / len(retrieved)
        if expected:
            item.recall = len(expected & retrieved) / len(expected)

        return item
