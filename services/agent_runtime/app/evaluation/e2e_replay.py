"""
End-to-end incident replay benchmark foundation.

A scenario can replay:
Alert -> Investigation -> Evidence -> RCA -> Evaluation
"""

from dataclasses import dataclass


@dataclass
class IncidentScenario:
    name: str
    signal: str
    expected_root_cause: str


class ReplayEvaluator:
    def evaluate(self, scenario: IncidentScenario, result: dict) -> dict:
        return {
            "scenario": scenario.name,
            "rca_match": result.get("root_cause") == scenario.expected_root_cause,
            "evidence_count": len(result.get("evidence", [])),
        }
