from abc import ABC, abstractmethod

from services.agent_runtime.app.model.context import (
    AgentContext,
)

from services.agent_runtime.app.model.result import (
    AgentResult,
)


class BaseAgent(ABC):
    """
    Base class of all agents.
    """


    @property
    @abstractmethod
    def name(self) -> str:
        pass



    @property
    def agent_type(self) -> str:
        return "general"



    @property
    def depends_on(self) -> list[str]:
        return []



    @property
    def provides(self) -> list[str]:
        return []



    def metadata(self) -> dict:
        """
        Agent capability metadata.

        Used by planner to analyze dependencies.
        """

        return {

            "type": self.agent_type,

            "depends_on": self.depends_on,

            "provides": self.provides,

        }



    @abstractmethod
    async def run(
        self,
        context: AgentContext,
    ) -> AgentResult:
        pass