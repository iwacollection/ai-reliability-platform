from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class TraceEvent(BaseModel):
    """
    Agent execution trace event.
    """


    trace_id: str


    agent: str


    start_time: datetime


    end_time: datetime | None = None


    duration_ms: float | None = None


    success: bool = False


    score: float = 0.0


    message: str = ""


    input_data: dict[str, Any] = Field(
        default_factory=dict
    )


    output_data: dict[str, Any] = Field(
        default_factory=dict
    )


    metadata: dict[str, Any] = Field(
        default_factory=dict
    )