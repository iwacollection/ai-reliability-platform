from services.agent_runtime.app.action.models import (
    ActionPlan,
)

from services.agent_runtime.app.action.executor import (
    BaseExecutor,
)

from services.agent_runtime.app.tools.manager import (
    ToolManager,
)



class MockExecutor(BaseExecutor):
    """
    Executor using Tool framework.

    Current mode:
    Kubernetes dry-run.
    """


    def __init__(
        self,
        tool_manager: ToolManager,
    ) -> None:

        self.tool_manager = tool_manager



    async def execute(
        self,
        action: ActionPlan,
    ) -> dict:


        if action.type.value == "restart_pod":


            result = await self.tool_manager.call(
                "kubernetes",
                action="restart",
                resource="pod",
                target=action.target,
            )


            return result



        return {
            "success": False,
            "message": (
                "Unsupported action"
            ),
        }