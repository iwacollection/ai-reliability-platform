from datetime import datetime

from pydantic import BaseModel


class IncidentMemory(BaseModel):
    """
    Historical incident memory.
    """

    service: str

    alert_name: str

    root_cause: str

    action: str

    success: bool = False

    created_at: datetime