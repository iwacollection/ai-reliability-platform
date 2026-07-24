import time
import uuid

from datetime import UTC, datetime

from services.agent_runtime.app.model.context import (
    AgentContext,
)

from services.agent_runtime.app.model.result import (
    AgentResult,
)

from services.agent_runtime.app.pipeline.base import (
    BasePipeline,
)

from services.agent_runtime.app.registry.agent_registry import (
    AgentRegistry,
)

from services.agent_runtime.app.planner.agent_planner import (
    AgentPlanner,
)

from services.agent_runtime.app.observability.collector import (
    TraceCollector,
)

from services.agent_runtime.app.observability.execution import (
    AgentExecutionRecord,
)

from services.agent_runtime.app.evaluation.registry import (
    EvaluationRegistry,
)



class PlannerPipeline(BasePipeline):
    """
    Execute agents by dependency planning.
    """



    def __init__(
        self,
        registry: AgentRegistry,
        planner: AgentPlanner,
        tracer: TraceCollector,
        evaluators: EvaluationRegistry,
    ) -> None:

        self.registry = registry

        self.planner = planner

        self.tracer = tracer

        self.evaluators = evaluators



    async def execute(
        self,
        context: AgentContext,
    ) -> list[AgentResult]:


        order = self.planner.build_execution_order(
            self.registry
        )


        results = []



        for agent_name in order:


            agent = (
                self.registry.get(
                    agent_name
                )
            )


            print(
                "EXECUTE:",
                agent.name,
            )



            context.metadata[
                "current_skill_calls"
            ] = []



            trace = self.tracer.start(

                agent=agent.name,

                trace_id=str(
                    uuid.uuid4()
                ),

                input_data={

                    "event":
                    context.event.signal.name

                },

            )



            execution_record = AgentExecutionRecord(

                agent=agent.name,

                input_data={

                    "event":
                    context.event.signal.name

                },

                start_time=datetime.now(
                    UTC
                ),

            )



            start = time.perf_counter()



            try:


                result = await agent.run(
                    context
                )


                execution_record.success = (
                    result.success
                )


            except Exception as exc:


                result = AgentResult(

                    agent=agent.name,

                    success=False,

                    score=0,

                    message="Agent execution failed",

                    data={

                        "error":
                        str(exc),

                    },

                )


                execution_record.success = False

                execution_record.error = str(
                    exc
                )



            end_time = datetime.now(
                UTC
            )


            execution_record.end_time = end_time


            execution_record.duration_ms = round(

                (

                    end_time
                    -
                    execution_record.start_time

                ).total_seconds()
                *
                1000,

                4,

            )



            execution_time = round(

                time.perf_counter() - start,

                4,

            )



            result.data[

                "execution_time"

            ] = execution_time



            execution_record.output_data = (
                result.data
            )



            #
            # Skill / Tool attribution
            #

            skill_calls = context.metadata.get(
                "current_skill_calls",
                [],
            )


            if skill_calls:

                execution_record.tool_calls.extend(
                    skill_calls
                )



            #
            # Memory attribution
            #

            if agent.name == "rca":


                service = (

                    context
                    .event
                    .resources[0]
                    .name

                )


                memory_key = (

                    f"incident:{service}:"
                    f"{context.event.signal.name}"

                )


                if context.memory:


                    memory_data = await (

                        context.memory.get(
                            memory_key
                        )

                    )


                    if memory_data:


                        execution_record.memory_hit = True


                        execution_record.memory_key = (
                            memory_key
                        )



            #
            # Save execution record
            #

            context.executions.append(
                execution_record
            )



            #
            # Evaluation
            #

            for evaluator in self.evaluators.list():


                evaluation = await evaluator.evaluate(

                    result,

                    execution_record,

                )


                context.evaluations.append(
                    evaluation
                )



            self.tracer.finish(

                trace=trace,

                success=result.success,

                score=result.score,

                message=result.message,

                output_data=result.data,

            )



            results.append(
                result
            )


            context.results[

                agent.name

            ] = result.model_dump()



        return results