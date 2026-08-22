from abc import ABC, abstractmethod

from services.agent_runtime.app.model.context import AgentContext
from services.agent_runtime.app.model.result import AgentResult


class BasePipeline(ABC):
    """
    Base Pipeline
    """

    @abstractmethod
    async def execute(
        self,
        context: AgentContext,
    ) -> list[AgentResult]:
        """
        Execute pipeline.
        """
        raise NotImplementedError