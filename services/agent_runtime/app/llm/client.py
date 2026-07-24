from services.agent_runtime.app.llm.base import BaseLLMProvider
from services.agent_runtime.app.llm.models import (
    ChatRequest,
    ChatResponse,
)


class LLMClient:
    """
    Unified LLM client.
    """

    def __init__(
        self,
        provider: BaseLLMProvider,
    ) -> None:
        self.provider = provider

    async def chat(
        self,
        request: ChatRequest,
    ) -> ChatResponse:

        return await self.provider.chat(request)