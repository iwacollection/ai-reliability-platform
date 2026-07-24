from abc import ABC, abstractmethod

from services.agent_runtime.app.model.result import (
    AgentResult,
)


class BaseAggregator(ABC):
    """
    Base class for result aggregation.
    """


    @abstractmethod
    def aggregate(
        self,
        results: list[AgentResult],
    ) -> dict:
        ...