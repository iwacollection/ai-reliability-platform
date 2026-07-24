from typing import Any

from pydantic import BaseModel, Field

from services.agent_runtime.app.evaluation.models import (
    EvaluationResult,
)



class EvaluationReport(BaseModel):
    """
    Aggregated evaluation report.

    Used for:
    - Harness engineering
    - Agent quality analysis
    - Regression testing
    """


    total_agents: int


    passed_agents: int


    failed_agents: int


    pass_rate: float


    overall_score: float


    evaluations: list[EvaluationResult] = Field(
        default_factory=list
    )


    metrics: dict[str, Any] = Field(
        default_factory=dict
    )