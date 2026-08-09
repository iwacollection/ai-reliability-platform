from typing import Protocol, runtime_checkable

from services.agent_runtime.app.approval.models import (
    ApprovalRequest,
    ApprovalStatus,
)


@runtime_checkable
class ApprovalTransitionGuard(Protocol):
    """Optional fail-closed policy evaluated before a pending transition."""

    async def require_transition(
        self,
        request: ApprovalRequest,
        target_status: ApprovalStatus,
    ) -> None:
        """Raise when a pending Approval cannot enter the target state."""


__all__ = [
    "ApprovalTransitionGuard",
]
