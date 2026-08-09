import time
import uuid

from datetime import UTC, datetime

from services.agent_runtime.app.evaluation.registry import (
    EvaluationRegistry,
)

from services.agent_runtime.app.incident.enums import (
    IncidentStatus,
)

from services.agent_runtime.app.workflow.enums import (
    WorkflowEvent,
)

from services.agent_runtime.app.incident.store import (
    IncidentConflictError,
    IncidentStore,
)

from services.agent_runtime.app.incident.timeline import (
    IncidentTimelineEventType,
)

from services.agent_runtime.app.llm.context import (
    clear_llm_context,
    set_llm_context,
)

from services.agent_runtime.app.model.context import (
    AgentContext,
)

from services.agent_runtime.app.model.result import (
    AgentResult,
)

from services.agent_runtime.app.observability.collector import (
    TraceCollector,
)

from services.agent_runtime.app.observability.execution import (
    AgentExecutionRecord,
)

from services.agent_runtime.app.pipeline.base import (
    BasePipeline,
)

from services.agent_runtime.app.planner.agent_planner import (
    AgentPlanner,
)

from services.agent_runtime.app.registry.agent_registry import (
    AgentRegistry,
)


class PlannerPipeline(BasePipeline):
    """
    Execute agents by dependency planning.

    The pipeline drives the runtime incident lifecycle.
    Agent implementations return their own results and do not
    update the global incident state directly.

    When IncidentStore is injected, every lifecycle transition
    is persisted with compare-and-set protection.
    """

    def __init__(
        self,
        registry: AgentRegistry,
        planner: AgentPlanner,
        tracer: TraceCollector,
        evaluators: EvaluationRegistry,
        incident_store: IncidentStore | None = None,
        incident_service=None,
        workflow_service=None,
    ) -> None:
        self.registry = registry

        self.planner = planner

        self.tracer = tracer

        self.evaluators = evaluators

        self.incident_store = incident_store

        self.incident_service = incident_service

        self.workflow_service = workflow_service

    async def execute(
        self,
        context: AgentContext,
    ) -> list[AgentResult]:
        """
        Build the execution order and run every planned agent.

        Pipeline incident lifecycle:

        NEW -> ANALYZING -> CONFIRMED -> HEALING
        Any agent failure -> FAILED

        RESOLVED is owned by VerificationRuntime
        after persisted PASSED verification evidence.
        """

        await self._prepare_incident(
            context
        )

        try:
            order = (
                self.planner.build_execution_order(
                    self.registry
                )
            )

            results: list[AgentResult] = []

            for agent_name in order:
                agent = self.registry.get(
                    agent_name
                )

                await self._before_agent(
                    context=context,
                    agent_name=agent.name,
                )

                print(
                    "EXECUTE:",
                    agent.name,
                )

                context.metadata[
                    "current_skill_calls"
                ] = []

                agent_trace_id = str(
                    uuid.uuid4()
                )

                input_data = {
                    "event":
                    context.event.signal.name,

                    "severity":
                    str(
                        context.event.signal.severity
                    ),

                    "resources":
                    [
                        resource.name
                        for resource
                        in context.event.resources
                    ],

                    "incident_id":
                    str(
                        context.incident.id
                    ),

                    "incident_status":
                    context.incident.status.value,
                }

                trace = self.tracer.start(
                    agent=agent.name,
                    trace_id=agent_trace_id,
                    input_data=input_data,
                )

                context.trace = trace

                execution_record = (
                    AgentExecutionRecord(
                        request_id=(
                            context.request_id
                        ),
                        event_id=str(
                            context.event.header.event_id
                        ),
                        trace_id=agent_trace_id,
                        agent=agent.name,
                        input_data=input_data,
                        start_time=datetime.now(
                            UTC
                        ),
                    )
                )

                set_llm_context(
                    execution_record.metadata,
                    trace,
                )

                start = time.perf_counter()

                try:
                    result = await self._run_agent(
                        agent=agent,
                        context=context,
                        execution_record=(
                            execution_record
                        ),
                    )

                    await self._after_agent(
                        context=context,
                        result=result,
                    )

                    self._finish_execution_record(
                        context=context,
                        result=result,
                        execution_record=(
                            execution_record
                        ),
                        start=start,
                    )

                    await self._run_evaluators(
                        context=context,
                        result=result,
                        execution_record=(
                            execution_record
                        ),
                    )

                    self.tracer.finish(
                        trace=trace,
                        success=result.success,
                        score=result.score,
                        message=result.message,
                        output_data=result.data,
                    )

                finally:
                    clear_llm_context()

                results.append(
                    result
                )

                context.results[
                    agent.name
                ] = result.model_dump()

            return results

        except Exception as exc:
            await self._transition_incident(
                context=context,
                status=IncidentStatus.FAILED,
                reason=(
                    "Planner pipeline failed: "
                    f"{type(exc).__name__}: {exc}"
                ),
            )

            raise

    @staticmethod
    async def _run_agent(
        agent,
        context: AgentContext,
        execution_record: AgentExecutionRecord,
    ) -> AgentResult:
        """
        Run one agent and convert its exception into a failed result.
        """

        try:
            result = await agent.run(
                context
            )

            execution_record.success = (
                result.success
            )

            return result

        except Exception as exc:
            execution_record.success = False

            execution_record.error = str(
                exc
            )

            return AgentResult(
                agent=agent.name,
                success=False,
                score=0,
                message=(
                    "Agent execution failed"
                ),
                data={
                    "error":
                    str(
                        exc
                    ),

                    "error_type":
                    type(exc).__name__,
                },
            )

    async def _before_agent(
        self,
        context: AgentContext,
        agent_name: str,
    ) -> None:
        """
        Apply lifecycle transitions before an agent runs.
        """

        if self.incident_service:
            self.incident_service._append_timeline(
                context.incident.id,
                IncidentTimelineEventType.AGENT_STARTED,
                source=agent_name,
                message=f"{agent_name} started",
            )

        if (
            agent_name == "healing"
            and context.incident.status
            != IncidentStatus.FAILED
        ):
            await self._transition_incident(
                context=context,
                status=IncidentStatus.HEALING,
                reason="Healing agent started",
            )

        else:
            self._sync_incident_metadata(
                context
            )

    async def _after_agent(
        self,
        context: AgentContext,
        result: AgentResult,
    ) -> None:
        """
        Apply lifecycle transitions produced by an agent result.

        FAILED is sticky inside one pipeline execution.
        A later successful agent must not overwrite an earlier failure.
        """

        if self.incident_service:
            self.incident_service._append_timeline(
                context.incident.id,
                IncidentTimelineEventType.AGENT_COMPLETED,
                source=result.agent,
                message=result.message,
            )

        incident_already_failed = (
            context.incident.status
            == IncidentStatus.FAILED
        )

        if (
            not incident_already_failed
            and not result.success
        ):
            await self._transition_incident(
                context=context,
                status=IncidentStatus.FAILED,
                reason=(
                    f"Agent {result.agent} failed: "
                    f"{result.message}"
                ),
            )

        elif (
            not incident_already_failed
            and result.agent == "rca"
        ):
            await self._transition_incident(
                context=context,
                status=(
                    IncidentStatus.CONFIRMED
                ),
                reason=(
                    "Root cause analysis completed"
                ),
            )

        elif (
            not incident_already_failed
            and result.agent == "healing"
        ):
            if self._approval_required(
                result
            ):
                await self._transition_incident(
                    context=context,
                    status=(
                        IncidentStatus.CONFIRMED
                    ),
                    reason=(
                        "Healing plan generated; "
                        "approval is required"
                    ),
                )


            else:
                await self._transition_incident(
                    context=context,
                    status=(
                        IncidentStatus.HEALING
                    ),
                    reason=(
                        "Healing plan generated; "
                        "awaiting execution and "
                        "verification"
                    ),
                )

        self._sync_incident_metadata(
            context
        )

        result.data[
            "incident_id"
        ] = str(
            context.incident.id
        )

        result.data[
            "incident_status"
        ] = context.incident.status.value

    async def _prepare_incident(
        self,
        context: AgentContext,
    ) -> None:
        """
        Create a persistent Incident or restore the current
        database version before starting the Pipeline.
        """

        if self.incident_store is not None:
            stored_incident = (
                await self.incident_store.get(
                    str(
                        context.incident.id
                    )
                )
            )

            if stored_incident is None:
                try:
                    context.incident = (
                        await self.incident_store.save(
                            context.incident
                        )
                    )

                except IncidentConflictError:
                    stored_incident = (
                        await self.incident_store.get(
                            str(
                                context.incident.id
                            )
                        )
                    )

                    if stored_incident is None:
                        raise

                    context.incident = (
                        stored_incident
                    )

            else:
                context.incident = (
                    stored_incident
                )

        await self._transition_incident(
            context=context,
            status=IncidentStatus.ANALYZING,
            reason=(
                "Agent analysis pipeline started"
            ),
        )

    async def _transition_incident(
        self,
        context: AgentContext,
        status: IncidentStatus,
        reason: str | None = None,
    ) -> None:
        """
        Apply one lifecycle transition and persist it with CAS.

        Without an injected IncidentStore, retain the original
        in-memory behavior for backward compatibility.
        """

        previous_status = (
            context.incident.status
        )

        if self.incident_store is None:
            context.incident.update(
                status,
                reason=reason,
            )

            self._sync_incident_metadata(
                context
            )

            return

        candidate = (
            context.incident.model_copy(
                deep=True
            )
        )

        candidate.update(
            status,
            reason=reason,
        )

        try:
            context.incident = (
                await self.incident_store.update(
                    candidate,
                    expected_status=(
                        previous_status
                    ),
                )
            )

        except IncidentConflictError:
            stored_incident = (
                await self.incident_store.get(
                    str(
                        context.incident.id
                    )
                )
            )

            if stored_incident is None:
                raise

            context.incident = (
                stored_incident
            )

            if (
                stored_incident.status
                != status
            ):
                raise

        self._sync_incident_metadata(
            context
        )

    @staticmethod
    def _sync_incident_metadata(
        context: AgentContext,
    ) -> None:
        context.metadata[
            "incident_id"
        ] = str(
            context.incident.id
        )

        context.metadata[
            "incident_status"
        ] = context.incident.status.value

    @staticmethod
    def _approval_required(
        result: AgentResult,
    ) -> bool:
        value = result.data.get(
            "approval_required",
            False,
        )

        if isinstance(
            value,
            str,
        ):
            return (
                value.strip().lower()
                in {
                    "1",
                    "true",
                    "yes",
                    "required",
                }
            )

        return bool(
            value
        )

    @staticmethod
    def _finish_execution_record(
        context: AgentContext,
        result: AgentResult,
        execution_record: AgentExecutionRecord,
        start: float,
    ) -> None:
        end_time = datetime.now(
            UTC
        )

        execution_record.end_time = (
            end_time
        )

        execution_record.duration_ms = round(
            (
                end_time
                - execution_record.start_time
            ).total_seconds()
            * 1000,
            4,
        )

        result.data[
            "execution_time"
        ] = round(
            time.perf_counter()
            - start,
            4,
        )

        execution_record.output_data = (
            result.model_dump()
        )

        llm_calls = (
            execution_record.metadata.get(
                "llm_calls",
                [],
            )
        )

        if llm_calls:
            execution_record.llm_calls = len(
                llm_calls
            )

        skill_calls = context.metadata.get(
            "current_skill_calls",
            [],
        )

        if skill_calls:
            execution_record.tool_calls.extend(
                skill_calls
            )

        context.executions.append(
            execution_record
        )

    async def _run_evaluators(
        self,
        context: AgentContext,
        result: AgentResult,
        execution_record: AgentExecutionRecord,
    ) -> None:
        await self._record_rca_memory_hit(
            context=context,
            result=result,
            execution_record=(
                execution_record
            ),
        )

        for evaluator in self.evaluators.list():
            evaluation = (
                await evaluator.evaluate(
                    result,
                    execution_record,
                )
            )

            context.evaluations.append(
                evaluation
            )

    @staticmethod
    async def _record_rca_memory_hit(
        context: AgentContext,
        result: AgentResult,
        execution_record: AgentExecutionRecord,
    ) -> None:
        if (
            result.agent != "rca"
            or not context.memory
            or not context.event.resources
        ):
            return

        service = (
            context.event.resources[0].name
        )

        memory_key = (
            f"incident:{service}:"
            f"{context.event.signal.name}"
        )

        memory_data = (
            await context.memory.get(
                memory_key
            )
        )

        if memory_data:
            execution_record.memory_hit = True

            execution_record.memory_key = (
                memory_key
            )




