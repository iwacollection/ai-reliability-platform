from abc import ABC, abstractmethod


class BaseMemory(ABC):
    """
    Agent memory interface.
    """


    @abstractmethod
    async def save(
        self,
        key: str,
        value: dict,
    ) -> None:
        ...


    @abstractmethod
    async def get(
        self,
        key: str,
    ) -> dict | None:
        ...