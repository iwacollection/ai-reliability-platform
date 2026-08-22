from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class Hypothesis:
    name: str
    confidence: float
    supporting_evidence: List[str] = field(default_factory=list)
    contradicting_evidence: List[str] = field(default_factory=list)


class HypothesisEngine:
    """
    Maintain investigation hypotheses and update confidence
    according to collected evidence.
    """

    def __init__(self):
        self.hypotheses: Dict[str, Hypothesis] = {}

    def add(self, name: str, confidence: float = 0.5):
        self.hypotheses[name] = Hypothesis(
            name=name,
            confidence=confidence,
        )

    def evaluate(self, evidence_type: str, evidence_id: str, impact: float):
        for hypothesis in self.hypotheses.values():
            if impact > 0:
                hypothesis.supporting_evidence.append(evidence_id)
            else:
                hypothesis.contradicting_evidence.append(evidence_id)

            hypothesis.confidence = max(
                0.0,
                min(1.0, hypothesis.confidence + impact),
            )

        return self.rank()

    def rank(self):
        return sorted(
            self.hypotheses.values(),
            key=lambda item: item.confidence,
            reverse=True,
        )
