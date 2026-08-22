from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from uuid import UUID

from services.agent_runtime.app.investigation.session_driver import (
    DurableInvestigationSessionDriver,
    InvestigationSessionDriverBlockedError,
)
from services.agent_runtime.app.investigation.session_models import (
    InvestigationSessionRecord,
    InvestigationSessionStatus,
    InvestigationStepKind,
    InvestigationStepStatus,
)
from services.agent_runtime.app.investigation.session_service import (
    InvestigationSessionService,
)


class InvestigationSessionLoopOutcome(str, Enum):
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


class InvestigationSessionLoopStopReason(str, Enum):
    STEP_LIMIT = "step_limit"
    SESSION_COMPLETED = "session_completed"
    SESSION_FAILED = "session_failed"
    RECOVERY_REQUIRED = "recovery_required"
    CONCURRENT_REPLAY = "concurrent_replay"
    DRIVER_BLOCKED = "driver_blocked"


@dataclass(frozen=True)
class InvestigationSessionLoopResult:
    session: InvestigationSessionRecord
    outcome: InvestigationSessionLoopOutcome
    stop_reason: InvestigationSessionLoopStopReason
    external_calls_made: int

    @property
    def recovery_required(self) -> bool:
        return (
            self.stop_reason
            == InvestigationSessionLoopStopReason.RECOVERY_REQUIRED
        )


class DurableInvestigationSessionLoop:
    """
    Advance one durable Investigation Session through bounded read-only steps.

    The Loop chooses only the next protocol phase. The Driver remains the sole
    owner of Reasoner and Probe calls, and the Service remains the sole owner
    of durable Claims and completion CAS. By default one invocation advances
    at most one external step, making pause and restart explicit.

    An unresolved Claim or INDETERMINATE result is never resumed
    automatically. Concurrent replay also stops the current invocation rather
    than borrowing another worker's completed step to continue the chain.
    """

    def __init__(
        self,
        *,
        session_service: InvestigationSessionService,
        session_driver: DurableInvestigationSessionDriver,
    ) -> None:
        if not isinstance(
            session_service,
            InvestigationSessionService,
        ):
            raise TypeError(
                "Investigation Session Loop Service is invalid"
            )
        if not isinstance(
            session_driver,
            DurableInvestigationSessionDriver,
        ):
            raise TypeError(
                "Investigation Session Loop Driver is invalid"
            )
        if session_driver.session_service is not session_service:
            raise ValueError(
                "Investigation Session Loop components do not share one Service"
            )

        self.session_service = session_service
        self.session_driver = session_driver

    async def run(
        self,
        session_id: UUID | str,
        *,
        context,
        claimant: str,
        max_external_steps: int = 1,
    ) -> InvestigationSessionLoopResult:
        if (
            not isinstance(max_external_steps, int)
            or isinstance(max_external_steps, bool)
            or not 1 <= max_external_steps <= 32
        ):
            raise ValueError(
                "Investigation Session Loop step limit is invalid"
            )

        external_calls_made = 0

        while True:
            session = await self.session_service.require(
                session_id
            )
            terminal = self._terminal_result(
                session,
                external_calls_made=external_calls_made,
            )
            if terminal is not None:
                return terminal

            if self._recovery_required(session):
                return InvestigationSessionLoopResult(
                    session=session,
                    outcome=InvestigationSessionLoopOutcome.BLOCKED,
                    stop_reason=(
                        InvestigationSessionLoopStopReason.RECOVERY_REQUIRED
                    ),
                    external_calls_made=external_calls_made,
                )

            if external_calls_made >= max_external_steps:
                return InvestigationSessionLoopResult(
                    session=session,
                    outcome=InvestigationSessionLoopOutcome.PAUSED,
                    stop_reason=InvestigationSessionLoopStopReason.STEP_LIMIT,
                    external_calls_made=external_calls_made,
                )

            try:
                if self._reasoner_is_next(session):
                    step_result = (
                        await self.session_driver.execute_reasoner_step(
                            session.session_id,
                            claimant=claimant,
                        )
                    )
                else:
                    step_result = (
                        await self.session_driver.execute_probe_step(
                            session.session_id,
                            context=context,
                            claimant=claimant,
                        )
                    )
            except InvestigationSessionDriverBlockedError:
                latest = await self.session_service.require(
                    session_id
                )
                return InvestigationSessionLoopResult(
                    session=latest,
                    outcome=InvestigationSessionLoopOutcome.BLOCKED,
                    stop_reason=(
                        InvestigationSessionLoopStopReason.DRIVER_BLOCKED
                    ),
                    external_calls_made=external_calls_made,
                )

            if step_result.replayed:
                return InvestigationSessionLoopResult(
                    session=step_result.session,
                    outcome=InvestigationSessionLoopOutcome.BLOCKED,
                    stop_reason=(
                        InvestigationSessionLoopStopReason.RECOVERY_REQUIRED
                        if step_result.recovery_required
                        else InvestigationSessionLoopStopReason.CONCURRENT_REPLAY
                    ),
                    external_calls_made=external_calls_made,
                )

            if step_result.external_call_made:
                external_calls_made += 1

    @staticmethod
    def _reasoner_is_next(
        session: InvestigationSessionRecord,
    ) -> bool:
        if not session.steps:
            return True
        latest = session.steps[-1]
        if latest.kind == InvestigationStepKind.PROBE:
            return True
        if latest.kind == InvestigationStepKind.REASONER:
            return False
        raise ValueError(
            "Investigation Session Loop step kind is invalid"
        )

    @staticmethod
    def _recovery_required(
        session: InvestigationSessionRecord,
    ) -> bool:
        if session.status == InvestigationSessionStatus.INDETERMINATE:
            return True
        if not session.steps:
            return False
        return session.steps[-1].status in {
            InvestigationStepStatus.CLAIMED,
            InvestigationStepStatus.INDETERMINATE,
        }

    @staticmethod
    def _terminal_result(
        session: InvestigationSessionRecord,
        *,
        external_calls_made: int,
    ) -> InvestigationSessionLoopResult | None:
        if session.status == InvestigationSessionStatus.COMPLETED:
            return InvestigationSessionLoopResult(
                session=session,
                outcome=InvestigationSessionLoopOutcome.COMPLETED,
                stop_reason=(
                    InvestigationSessionLoopStopReason.SESSION_COMPLETED
                ),
                external_calls_made=external_calls_made,
            )
        if session.status == InvestigationSessionStatus.FAILED:
            return InvestigationSessionLoopResult(
                session=session,
                outcome=InvestigationSessionLoopOutcome.FAILED,
                stop_reason=InvestigationSessionLoopStopReason.SESSION_FAILED,
                external_calls_made=external_calls_made,
            )
        return None


__all__ = [
    "DurableInvestigationSessionLoop",
    "InvestigationSessionLoopOutcome",
    "InvestigationSessionLoopResult",
    "InvestigationSessionLoopStopReason",
]
