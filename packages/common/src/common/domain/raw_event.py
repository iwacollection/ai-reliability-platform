"""
Raw event received by Gateway before normalization.
"""

from typing import Any

from pydantic import Field

from common.domain.event.base import DomainModel


class RawEvent(DomainModel):
    """
    Original payload from external monitoring systems.
    """

    source: str

    payload: dict[str, Any]

    headers: dict[str, str] = Field(default_factory=dict)