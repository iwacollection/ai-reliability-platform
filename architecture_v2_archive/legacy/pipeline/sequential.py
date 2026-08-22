import time

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


from services.agent_runtime.app.observability.execution import (
    AgentExecutionRecord,
)



class SequentialPipeline(BasePipeline):
    """
    Execute agents sequentially.
    """


    def __init__(
        self,
        registry: AgentRegistry,
    ) -> None:

        self.registry = registry



    async def execute(
        self,
        context: AgentContext,
    ) -> list[AgentResult]:


        results: list[AgentResult] = []



        for agent in self.registry.list_agents():


            print(
                "EXECUTE AGENT:",
                agent.name
            )



            execution = AgentExecutionRecord(

                request_id=context.request_id,


                event_id=str(
                    context.event.header.event_id
                ),


                trace_id=str(
                    context.event.header.trace_id
                ),


                agent=agent.name,


                start_time=datetime.now(
                    UTC
                ),


                input_data={

                    "event_name":
                        context.event.signal.name,


                    "severity":
                        str(
                            context.event.signal.severity
                        ),


                    "resource":

                        [

                            resource.name

                            for resource

                            in context.event.resources

                        ],

                },

            )



            start = time.perf_counter()



            try:


                result = await agent.run(
                    context,
                )


                execution.success = (
                    result.success
                )


                execution.output_data = (
                    result.model_dump()
                )


            except Exception as exc:


                execution.success = False


                execution.error = str(
                    exc
                )


                result = AgentResult(

                    agent=agent.name,

                    success=False,

                    score=0,

                    message="Agent execution failed",

                    data={

                        "error": str(exc),

                    },

                )



            elapsed = (

                time.perf_counter()

                - start

            )



            execution.end_time = datetime.now(
                UTC
            )


            execution.duration_ms = round(
                elapsed * 1000,
                4,
            )



            result.data[
                "execution_time"
            ] = round(
                elapsed,
                4,
            )



            results.append(
                result
            )



            context.results[
                agent.name
            ] = result.model_dump()



            context.executions.append(
                execution
            )



        return results