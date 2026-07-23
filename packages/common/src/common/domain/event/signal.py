"""
Signal model for the Standard Event Specification.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from pydantic import Field

from common.domain.event.base import DomainModel
from common.domain.event.enums import (
    Severity,
    SignalStatus,
    SignalType,
)


class Signal(DomainModel):
    """
    A monitoring signal that represents an abnormal observation.

    A signal can originate from alerts, metrics, logs,
    traces or other monitoring systems.
    """

    id: UUID = Field(default_factory=uuid4)

    type: SignalType

    name: str

    message: str

    severity: Severity

    status: SignalStatus = SignalStatus.FIRING

    value: float | int | str | None = None

    threshold: float | int | None = None

    unit: str | None = None

    labels: dict[str, str] = Field(default_factory=dict)

    metadata: dict[str, Any] = Field(default_factory=dict)