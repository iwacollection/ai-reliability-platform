from services.agent_runtime.app.llm.base import (
    BaseLLMProvider,
)


class LLMProviderRegistry:
    """
    Registry for LLM providers.
    """

    def __init__(self) -> None:
        self._providers: dict[
            str,
            BaseLLMProvider,
        ] = {}

    def register(
        self,
        provider: BaseLLMProvider,
    ) -> None:

        self._providers[
            provider.name
        ] = provider


    def get(
        self,
        name: str,
    ) -> BaseLLMProvider:

        if name not in self._providers:
            raise KeyError(
                f"LLM provider '{name}' not found."
            )

        return self._providers[name]


    def list(
        self,
    ) -> list[BaseLLMProvider]:

        return list(
            self._providers.values()
        )