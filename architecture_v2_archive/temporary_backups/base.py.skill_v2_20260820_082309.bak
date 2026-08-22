from abc import ABC, abstractmethod


class BaseSkill(ABC):
    """
    Base interface for agent skills.

    Skill runs inside AgentContext,
    so it can access:
    - tools
    - memory
    - metadata
    - variables
    """


    @property
    @abstractmethod
    def name(self) -> str:
        ...


    @abstractmethod
    async def execute(
        self,
        context,
        input_data: dict,
    ) -> dict:
        ...