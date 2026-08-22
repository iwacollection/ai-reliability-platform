"""RCA confidence ranking foundation."""

from dataclasses import dataclass
from typing import Dict, List


@dataclass
class RCAHypothesis:
    name: str
    confidence: float
    evidence_count: int


class RCAConfidenceRanker:
    def rank(self, hypotheses: List[Dict]):
        result = [
            RCAHypothesis(
                name=item.get("name", "unknown"),
                confidence=float(item.get("confidence", 0)),
                evidence_count=int(item.get("evidence_count", 0)),
            )
            for item in hypotheses
        ]
        return sorted(result, key=lambda item: item.confidence, reverse=True)
