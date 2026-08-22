from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class ReplayResult:
    scenario: str
    predicted_rca: str
    expected_rca: str
    matched: bool


class AgentReplayEngine:
    def replay(self, benchmark, investigation_result: Dict[str, Any]) -> ReplayResult:
        predicted = investigation_result.get("root_cause", "unknown")
        return ReplayResult(
            scenario=benchmark.name,
            predicted_rca=predicted,
            expected_rca=benchmark.expected_rca,
            matched=predicted == benchmark.expected_rca,
        )
