from services.agent_runtime.app.action.planner import (
    ActionPlanner,
)

from services.agent_runtime.app.action.mock_executor import (
    MockExecutor,
)

from services.agent_runtime.app.model.result import (
    AgentResult,
)


class ActionRuntime:
    """
    Handle healing result to action execution.
    """


    def __init__(self):

        self.planner = ActionPlanner()

        self.executor = MockExecutor()



    async def execute(
        self,
        healing_result: dict,
    ):


        plan = self.planner.create_plan(

            AgentResult(
                **healing_result
            )

        )


        result = await self.executor.execute(
            plan
        )


        return plan, result