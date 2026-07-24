from services.agent_runtime.app.tools.base import (
    BaseTool,
)


class EchoTool(BaseTool):
    """
    Simple test tool.
    """


    @property
    def name(self) -> str:

        return "echo"


    async def execute(
        self,
        message: str,
    ) -> dict:


        return {
            "message": message,
            "source": "echo_tool",
        }