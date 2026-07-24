from abc import ABC, abstractmethod
from time import perf_counter

from services.agent_runtime.app.model.context import AgentContext
from services.agent_runtime.app.model.result import AgentResult


class BaseAgent(ABC):
    """
    Base class of all agents.

    All agents should inherit from this class.
    Pipeline should always call execute(),
    never call run() directly.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique agent name."""
        ...

    @abstractmethod
    async def run(
        self,
        context: AgentContext,
    ) -> AgentResult:
        """
        Business logic implemented by subclasses.
        """
        ...

    async def execute(
        self,
        context: AgentContext,
    ) -> AgentResult:
        """
        Unified execution entry.

        Handles:
        - execution timing
        - context update
        - future logging / retry / metrics
        """

        start = perf_counter()

        result = await self.run(context)

        elapsed = perf_counter() - start

        context.results[self.name] = result.model_dump()

        print(
            f"[{self.name}] finished in {elapsed:.3f}s"
        )

        return result