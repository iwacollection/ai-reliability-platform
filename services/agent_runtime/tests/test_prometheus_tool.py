import asyncio


from services.agent_runtime.app.tools.factory import (
    create_tool_manager,
)



def test_prometheus_tool():


    async def run():

        manager = create_tool_manager()


        result = await manager.call(
            "prometheus",
            query="cpu_usage",
        )


        assert (
            result["metrics"]["cpu_usage"]
            == 92
        )


        assert (
            result["source"]
            == "mock_prometheus"
        )


    asyncio.run(run())