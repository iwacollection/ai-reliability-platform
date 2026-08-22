from dataclasses import dataclass
from typing import List


@dataclass
class RCAEvaluation:
    accuracy: float
    evidence_coverage: float
    benchmark_count: int


class RCAAccuracyEvaluator:
    def evaluate(self, results: List[bool], evidence_scores: List[float]):
        if not results:
            return RCAEvaluation(0.0, 0.0, 0)

        accuracy = sum(results) / len(results)
        coverage = sum(evidence_scores) / len(evidence_scores) if evidence_scores else 0.0

        return RCAEvaluation(
            accuracy=accuracy,
            evidence_coverage=coverage,
            benchmark_count=len(results),
        )
