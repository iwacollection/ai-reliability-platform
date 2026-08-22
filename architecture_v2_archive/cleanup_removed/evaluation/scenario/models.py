from typing import Any

from pydantic import BaseModel, Field



class ScenarioDefinition(BaseModel):
    """
    Agent evaluation scenario.

    Used for:
    - Harness engineering
    - Agent replay
    - Regression testing
    """


    name: str


    description: str = ""


    event: dict[str, Any] = Field(
        default_factory=dict
    )


    expected: dict[str, Any] = Field(
        default_factory=dict
    )


    metadata: dict[str, Any] = Field(
        default_factory=dict
    )