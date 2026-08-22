from typing import Any

from pydantic import BaseModel, Field



class AssertionResult(BaseModel):
    """
    Scenario assertion result.

    Used for:
    - Harness validation
    - Regression testing
    """


    scenario: str


    passed: bool


    checks: list[dict[str, Any]] = Field(
        default_factory=list
    )