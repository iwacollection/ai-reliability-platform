from __future__ import annotations

from typing import Any, TypedDict
from uuid import UUID

from langgraph.graph import END, START, StateGraph

from services.agent_runtime.app.investigation.engine import (
    BaseInvestigationEngine,
)
from services.agent_runtime.app.investigation.session_driver import (
    DurableInvestigationSessionDriver,
    InvestigationSessionDriverBlockedError,
)
from services.agent_runtime.app.investigation.session_loop import (
    InvestigationSessionLoopOutcome,
    InvestigationSessionLoopResult,
    InvestigationSessionLoopStopReason,
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


class LangGraphInvestigationEngineError(RuntimeError):
    """LangGraph orchestration could not return a bounded durable result."""


class _LangGraphInvocationState(TypedDict, total=False):
    session_id: UUID | str
    context: Any
    claimant: str
    max_external_steps: int
    expected_version: int | None
    external_calls_made: int
    result: InvestigationSessionLoopResult


class LangGraphInvestigationEngine(BaseInvestigationEngine):
    """
    LangGraph protocol orchestration over the existing durable Session core.

    LangGraph chooses the next protocol edge only. It has no Checkpointer and
    is never a second persistence authority. SessionService, SessionStore and
    Driver Claims/CAS remain the sole durable source of truth.
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
                "LangGraph Investigation Engine Service is invalid"
            )
        if not isinstance(
            session_driver,
            DurableInvestigationSessionDriver,
        ):
            raise TypeError(
                "LangGraph Investigation Engine Driver is invalid"
            )
        if session_driver.session_service is not session_service:
            raise ValueError(
                "LangGraph Investigation Engine components do not share one Service"
            )

        self._session_service = session_service
        self.session_driver = session_driver

        builder = StateGraph(
            _LangGraphInvocationState
        )
        builder.add_node(
            "advance_durable_step",
            self._advance_durable_step,
        )
        builder.add_edge(
            START,
            "advance_durable_step",
        )
        builder.add_conditional_edges(
            "advance_durable_step",
            self._next_edge,
            {
                "continue": "advance_durable_step",
                "stop": END,
            },
        )

        # Deliberately compile without a LangGraph Checkpointer. The existing
        # Session database is authoritative and already survives restarts.
        self.graph = builder.compile()

    @property
    def name(self) -> str:
        return "langgraph"

    @property
    def session_service(self) -> InvestigationSessionService:
        return self._session_service

    @property
    def checkpointer_enabled(self) -> bool:
        return False

    async def advance(
        self,
        session_id: UUID | str,
        *,
        context: Any,
        claimant: str,
        max_external_steps: int = 1,
        expected_version: int | None = None,
    ) -> InvestigationSessionLoopResult:
        self._validate_request(
            claimant=claimant,
            max_external_steps=max_external_steps,
            expected_version=expected_version,
        )
        output = await self.graph.ainvoke(
            {
                "session_id": session_id,
                "context": context,
                "claimant": claimant,
                "max_external_steps": max_external_steps,
                "expected_version": expected_version,
                "external_calls_made": 0,
            },
            config={
                "recursion_limit": max(
                    25,
                    max_external_steps + 5,
                )
            },
        )
        result = output.get(
            "result"
        )
        if not isinstance(
            result,
            InvestigationSessionLoopResult,
        ):
            raise LangGraphInvestigationEngineError(
                "LangGraph Investigation did not produce a durable result"
            )
        return result

    async def _advance_durable_step(
        self,
        state: _LangGraphInvocationState,
    ) -> dict[str, Any]:
        session = await self.session_service.require(
            state["session_id"]
        )
        external_calls_made = state[
            "external_calls_made"
        ]

        terminal = self._terminal_result(
            session,
            external_calls_made=external_calls_made,
        )
        if terminal is not None:
            return {"result": terminal}

        if self._recovery_required(session):
            return {
                "result": InvestigationSessionLoopResult(
                    session=session,
                    outcome=InvestigationSessionLoopOutcome.BLOCKED,
                    stop_reason=(
                        InvestigationSessionLoopStopReason.RECOVERY_REQUIRED
                    ),
                    external_calls_made=external_calls_made,
                )
            }

        if external_calls_made >= state[
            "max_external_steps"
        ]:
            return {
                "result": InvestigationSessionLoopResult(
                    session=session,
                    outcome=InvestigationSessionLoopOutcome.PAUSED,
                    stop_reason=InvestigationSessionLoopStopReason.STEP_LIMIT,
                    external_calls_made=external_calls_made,
                )
            }

        first_expected_version = (
            state.get(
                "expected_version"
            )
            if external_calls_made == 0
            else None
        )
        try:
            if self._reasoner_is_next(session):
                step_result = (
                    await self.session_driver.execute_reasoner_step(
                        session.session_id,
                        claimant=state["claimant"],
                        expected_version=first_expected_version,
                    )
                )
            else:
                step_result = (
                    await self.session_driver.execute_probe_step(
                        session.session_id,
                        context=state["context"],
                        claimant=state["claimant"],
                        expected_version=first_expected_version,
                    )
                )
        except InvestigationSessionDriverBlockedError:
            latest = await self.session_service.require(
                state["session_id"]
            )
            return {
                "result": InvestigationSessionLoopResult(
                    session=latest,
                    outcome=InvestigationSessionLoopOutcome.BLOCKED,
                    stop_reason=InvestigationSessionLoopStopReason.DRIVER_BLOCKED,
                    external_calls_made=external_calls_made,
                )
            }

        if step_result.replayed:
            return {
                "result": InvestigationSessionLoopResult(
                    session=step_result.session,
                    outcome=InvestigationSessionLoopOutcome.BLOCKED,
                    stop_reason=(
                        InvestigationSessionLoopStopReason.RECOVERY_REQUIRED
                        if step_result.recovery_required
                        else InvestigationSessionLoopStopReason.CONCURRENT_REPLAY
                    ),
                    external_calls_made=external_calls_made,
                )
            }

        if step_result.external_call_made:
            external_calls_made += 1

        return {
            "external_calls_made": external_calls_made
        }

    @staticmethod
    def _next_edge(
        state: _LangGraphInvocationState,
    ) -> str:
        return (
            "stop"
            if "result" in state
            else "continue"
        )

    @staticmethod
    def _validate_request(
        *,
        claimant: str,
        max_external_steps: int,
        expected_version: int | None,
    ) -> None:
        if not isinstance(claimant, str) or not claimant.strip():
            raise ValueError(
                "LangGraph Investigation claimant is invalid"
            )
        if (
            not isinstance(max_external_steps, int)
            or isinstance(max_external_steps, bool)
            or not 1 <= max_external_steps <= 32
        ):
            raise ValueError(
                "LangGraph Investigation step limit is invalid"
            )
        if (
            expected_version is not None
            and (
                not isinstance(expected_version, int)
                or isinstance(expected_version, bool)
                or expected_version < 0
            )
        ):
            raise ValueError(
                "LangGraph Investigation expected version is invalid"
            )

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
            "Investigation Session step kind is invalid"
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
    "LangGraphInvestigationEngine",
    "LangGraphInvestigationEngineError",
]
