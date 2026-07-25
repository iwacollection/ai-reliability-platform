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



    # ==========================
    # Trace Identity
    # ==========================

    request_id: str | None = None


    event_id: str | None = None


    trace_id: str | None = None



    # ==========================
    # Agent Information
    # ==========================

    agent: str



    start_time: datetime | None = None


    end_time: datetime | None = None


    duration_ms: float = 0.0



    success: bool = True


    error: str | None = None



    # ==========================
    # Execution Data
    # ==========================

    input_data: dict[str, Any] = Field(
        default_factory=dict
    )


    output_data: dict[str, Any] = Field(
        default_factory=dict
    )



    # ==========================
    # Memory / Tool / MCP / LLM
    # ==========================

    memory_hit: bool = False


    memory_key: str | None = None



    #
    # Runtime Tool calls
    #
    tool_calls: list[str] = Field(
        default_factory=list
    )



    #
    # MCP calls
    #
    # Example:
    #
    # [
    #   "mock_mcp:kubernetes_diagnosis"
    # ]
    #
    mcp_calls: list[str] = Field(
        default_factory=list
    )



    #
    # LLM call count
    #
    llm_calls: int = 0



    # ==========================
    # Extension Metadata
    # ==========================

    metadata: dict[str, Any] = Field(
        default_factory=dict
    )