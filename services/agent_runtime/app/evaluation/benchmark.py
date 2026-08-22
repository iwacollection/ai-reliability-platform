"""
Agent Evaluation Benchmark.

Provides scenario based evaluation for investigation quality.
"""

from dataclasses import dataclass


@dataclass
class BenchmarkScenario:
    name: str
    input_event: dict
    expected_root_cause: str


@dataclass
class EvaluationResult:
    scenario: str
    evidence_score: float
    rca_score: float
    tool_score: float


class InvestigationBenchmark:
    def evaluate(self, scenario: BenchmarkScenario, result: dict):
        return EvaluationResult(
            scenario=scenario.name,
            evidence_score=result.get("evidence_score", 0),
            rca_score=result.get("rca_score", 0),
            tool_score=result.get("tool_score", 0),
        )
