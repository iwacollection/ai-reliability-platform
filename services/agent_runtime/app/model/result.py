from typing import Any

from pydantic import BaseModel


class AgentResult(BaseModel):
    agent: str

    success: bool

    score: float

    message: str

    data: dict[str, Any] = {}