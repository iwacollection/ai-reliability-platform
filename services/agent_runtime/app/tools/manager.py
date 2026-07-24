from typing import Any

from services.agent_runtime.app.tools.registry import (
    ToolRegistry,
)


class ToolManager:
    """
    Runtime tool manager.
    """


    def __init__(
        self,
        registry: ToolRegistry,
    ) -> None:

        self.registry = registry


    async def call(
        self,
        name: str,
        **kwargs: Any,
    ) -> dict:


        tool = self.registry.get(
            name
        )


        return await tool.execute(
            **kwargs
        )