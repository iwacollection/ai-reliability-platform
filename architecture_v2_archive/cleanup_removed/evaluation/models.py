from typing import Any

from pydantic import BaseModel, Field



class EvaluationResult(BaseModel):
    """
    Result of agent evaluation.

    Used for:
    - Harness engineering
    - Agent quality measurement
    - Regression testing
    """


    agent: str


    passed: bool


    score: float


    message: str


    metrics: dict[str, Any] = Field(
        default_factory=dict
    )