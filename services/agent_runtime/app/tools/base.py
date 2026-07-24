from abc import ABC, abstractmethod

from typing import Any


class BaseTool(ABC):
    """
    Base interface for all Agent tools.
    """


    @property
    @abstractmethod
    def name(self) -> str:
        ...


    @abstractmethod
    async def execute(
        self,
        **kwargs: Any,
    ) -> dict:
        ...