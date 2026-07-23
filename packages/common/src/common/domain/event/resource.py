"""
Resource model for the Standard Event Specification.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from pydantic import Field

from common.domain.event.base import DomainModel
from common.domain.event.enums import ResourceKind


class Resource(DomainModel):
    """
    Resource affected by an event.

    Examples
    --------
    - Kubernetes Pod
    - Kubernetes Node
    - ECS Task
    - EC2 Instance
    - Docker Container
    """

    id: UUID = Field(default_factory=uuid4)

    kind: ResourceKind

    name: str

    namespace: str | None = None

    cluster: str | None = None

    region: str | None = None

    labels: dict[str, str] = Field(default_factory=dict)

    attributes: dict[str, Any] = Field(default_factory=dict)