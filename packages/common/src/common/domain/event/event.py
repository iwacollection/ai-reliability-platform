"""
Standard Event Specification (SES).

This module defines the top-level event object shared across
the AI Reliability Platform.
"""

from __future__ import annotations

from pydantic import Field

from common.domain.event.base import DomainModel
from common.domain.event.header import Header
from common.domain.event.resource import Resource
from common.domain.event.signal import Signal


class StandardEvent(DomainModel):
    """
    Standard Event object.

    Every event flowing through the platform is represented
    by this model.
    """

    header: Header

    signal: Signal

    resources: list[Resource] = Field(default_factory=list)