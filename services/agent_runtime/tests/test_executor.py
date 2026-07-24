import asyncio

from services.agent_runtime.app.action.models import (
    ActionPlan,
    ActionType,
)

from services.agent_runtime.app.action.mock_executor import (
    MockExecutor,
)


def test_mock_executor():

    async def run():

        executor = MockExecutor()


        action = ActionPlan(
            type=ActionType.RESTART_POD,
            target="payment-api",
        )


        result = await executor.execute(
            action
        )


        assert result["success"] is True

        assert result["action"] == "restart_pod"

        assert result["target"] == "payment-api"


    asyncio.run(run())