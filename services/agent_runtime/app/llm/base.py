from abc import ABC, abstractmethod

from services.agent_runtime.app.llm.models import (
    ChatRequest,
    ChatResponse,
)


class BaseLLMProvider(ABC):
    """
    Base class for all LLM providers.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    async def chat(
        self,
        request: ChatRequest,
    ) -> ChatResponse:
        ...