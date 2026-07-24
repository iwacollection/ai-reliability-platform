import asyncio


from services.agent_runtime.app.tools.registry import (
    ToolRegistry,
)

from services.agent_runtime.app.tools.manager import (
    ToolManager,
)

from services.agent_runtime.app.tools.mock.echo import (
    EchoTool,
)



def test_tool_manager():


    async def run():

        registry = ToolRegistry()


        registry.register(
            EchoTool()
        )


        manager = ToolManager(
            registry
        )


        result = await manager.call(
            "echo",
            message="hello",
        )


        assert result["message"] == "hello"

        assert result["source"] == "echo_tool"


    asyncio.run(run())