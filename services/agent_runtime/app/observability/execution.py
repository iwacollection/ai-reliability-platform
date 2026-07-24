from datetime import datetime

from typing import Any

from pydantic import (
    BaseModel,
    Field,
)



class AgentExecutionRecord(BaseModel):
    """
    Detailed agent execution record.

    Used for:
    - Harness engineering
    - AI observability
    - debugging
    """


    agent: str


    start_time: datetime | None = None


    end_time: datetime | None = None


    duration_ms: float = 0.0


    success: bool = True


    error: str | None = None



    input_data: dict[str, Any] = Field(
        default_factory=dict
    )


    output_data: dict[str, Any] = Field(
        default_factory=dict
    )


    memory_hit: bool = False


    memory_key: str | None = None


    tool_calls: list[str] = Field(
        default_factory=list
    )


    llm_calls: int = 0


    metadata: dict[str, Any] = Field(
        default_factory=dict
    )