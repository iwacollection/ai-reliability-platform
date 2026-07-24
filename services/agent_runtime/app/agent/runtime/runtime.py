from services.agent_runtime.app.model.context import (
    AgentContext,
)

from services.agent_runtime.app.model.result import (
    AgentResult,
)

from services.agent_runtime.app.runtime.scheduler import (
    AgentScheduler,
)



class AgentRuntime:
    """
    Production Agent Runtime.

    Responsible for:

    - agent scheduling
    - execution
    - context propagation
    """


    def __init__(
        self,
        registry,
    ):

        self.registry = registry

        self.scheduler = (
            AgentScheduler(
                registry
            )
        )



    async def run(
        self,
        context: AgentContext,
    ) -> list[AgentResult]:


        plan = (
            self.scheduler
            .build_plan()
        )


        print()

        print(
            "Runtime Execution Plan"
        )

        print(
            plan
        )


        results = []


        for agent_name in plan:


            agent = (
                self.registry
                .get(
                    agent_name
                )
            )


            print(
                "Runtime Execute:",
                agent.name
            )


            result = await agent.run(
                context
            )


            context.results[
                agent.name
            ] = (
                result
                .model_dump()
            )


            results.append(
                result
            )


        return results