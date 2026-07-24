from abc import ABC, abstractmethod


class BaseSkill(ABC):
    """
    Base interface for agent skills.
    """


    @property
    @abstractmethod
    def name(self) -> str:
        ...


    @abstractmethod
    async def execute(
        self,
        input_data: dict,
    ) -> dict:
        ...