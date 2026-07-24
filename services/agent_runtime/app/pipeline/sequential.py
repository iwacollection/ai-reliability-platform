import time

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

            start = time.perf_counter()

            try:

                result = await agent.run(
                    context,
                )

            except Exception as exc:

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

            result.data["execution_time"] = (
                round(
                    elapsed,
                    4,
                )
            )

            results.append(
                result,
            )

            context.results[
                agent.name
            ] = result.model_dump()


        return results