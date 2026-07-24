from services.agent_runtime.app.tools.base import (
    BaseTool,
)


class ToolRegistry:
    """
    Registry for tools.
    """


    def __init__(self):

        self._tools: dict[str, BaseTool] = {}


    def register(
        self,
        tool: BaseTool,
    ) -> None:

        self._tools[
            tool.name
        ] = tool


    def get(
        self,
        name: str,
    ) -> BaseTool:

        if name not in self._tools:

            raise KeyError(
                f"Tool '{name}' not found"
            )

        return self._tools[name]


    def list_tools(
        self,
    ) -> list[BaseTool]:

        return list(
            self._tools.values()
        )