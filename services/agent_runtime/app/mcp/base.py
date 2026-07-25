from abc import ABC, abstractmethod

from typing import Any


class BaseMCPClient(ABC):
    """
    Base interface for MCP client.

    MCP client is responsible for:
    - connecting MCP server
    - calling MCP tools
    - returning structured results
    """


    @property
    @abstractmethod
    def name(self) -> str:
        ...


    @abstractmethod
    async def call(
        self,
        tool: str,
        **kwargs: Any,
    ) -> dict:
        ...