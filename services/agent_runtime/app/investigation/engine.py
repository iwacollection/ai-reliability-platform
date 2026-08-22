from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any
from uuid import UUID

from services.agent_runtime.app.investigation.models import (
    InvestigationState,
)
from services.agent_runtime.app.investigation.session_loop import (
    DurableInvestigationSessionLoop,
    InvestigationSessionLoopResult,
)
from services.agent_runtime.app.investigation.session_models import (
    InvestigationSessionRecord,
)
from services.agent_runtime.app.investigation.session_service import (
    InvestigationSessionService,
)
from services.agent_runtime.app.investigation.session_store import (
    InvestigationSessionCreateResult,
)


class BaseInvestigationEngine(ABC):
    """
    Framework-neutral durable Investigation execution boundary.

    SessionStore and SessionService remain authoritative. An Engine may choose
    protocol transitions, but it cannot bypass durable Claims, completion CAS,
    read-only Probe policy, or the one-step API limit.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable backend identity for diagnostics and tests."""

    @property
    @abstractmethod
    def session_service(self) -> InvestigationSessionService:
        """Return the one authoritative durable Session service."""

    async def create_or_get(
        self,
        *,
        incident_id: UUID | str,
        run_key: str,
        initial_state: InvestigationState,
        created_by: str = "runtime",
        now: datetime | None = None,
    ) -> InvestigationSessionCreateResult:
        return await self.session_service.create_or_get(
            incident_id=incident_id,
            run_key=run_key,
            initial_state=initial_state,
            created_by=created_by,
            now=now,
        )

    async def get(
        self,
        session_id: UUID | str,
    ) -> InvestigationSessionRecord | None:
        return await self.session_service.get(
            session_id
        )

    async def require(
        self,
        session_id: UUID | str,
    ) -> InvestigationSessionRecord:
        return await self.session_service.require(
            session_id
        )

    @abstractmethod
    async def advance(
        self,
        session_id: UUID | str,
        *,
        context: Any,
        claimant: str,
        max_external_steps: int = 1,
        expected_version: int | None = None,
    ) -> InvestigationSessionLoopResult:
        """Advance through a bounded number of durable read-only steps."""


class CustomInvestigationEngine(BaseInvestigationEngine):
    """Adapter preserving the existing custom durable Loop behavior."""

    def __init__(
        self,
        *,
        session_service: InvestigationSessionService,
        session_loop: DurableInvestigationSessionLoop,
    ) -> None:
        if not isinstance(
            session_service,
            InvestigationSessionService,
        ):
            raise TypeError(
                "Custom Investigation Engine Service is invalid"
            )
        if not isinstance(
            session_loop,
            DurableInvestigationSessionLoop,
        ):
            raise TypeError(
                "Custom Investigation Engine Loop is invalid"
            )
        if session_loop.session_service is not session_service:
            raise ValueError(
                "Custom Investigation Engine components do not share one Service"
            )

        self._session_service = session_service
        self.session_loop = session_loop

    @property
    def name(self) -> str:
        return "custom"

    @property
    def session_service(self) -> InvestigationSessionService:
        return self._session_service

    async def advance(
        self,
        session_id: UUID | str,
        *,
        context: Any,
        claimant: str,
        max_external_steps: int = 1,
        expected_version: int | None = None,
    ) -> InvestigationSessionLoopResult:
        return await self.session_loop.run(
            session_id,
            context=context,
            claimant=claimant,
            max_external_steps=max_external_steps,
            expected_version=expected_version,
        )


__all__ = [
    "BaseInvestigationEngine",
    "CustomInvestigationEngine",
]
