from typing import Any

from pydantic import BaseModel


class ObservationQuery(BaseModel):
    """
    Query request for observation data.
    """

    source: str

    resource: str

    parameters: dict[str, Any] = {}


class ObservationResult(BaseModel):
    """
    Observation result returned by providers.
    """

    source: str

    success: bool

    data: dict[str, Any] = {}

    message: str = ""