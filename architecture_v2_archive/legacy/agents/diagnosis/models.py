from typing import Any

from pydantic import BaseModel


class Evidence(BaseModel):
    """
    Evidence collected from production systems.
    """


    source: str

    data: dict[str, Any]

    description: str = ""