from services.agent_runtime.app.llm.client import (
    LLMClient,
)



class ProviderNotFoundError(
    Exception
):
    """
    Provider does not exist.
    """



class ProviderManager:
    """
    Manage LLM provider clients.

    Responsibilities:

    - Provider client storage
    - Provider lookup
    - Provider resolution
    - Fallback candidate management
    - Provider health abstraction

    Future:

    - retry
    - circuit breaker
    - rate limit

    """



    def __init__(
        self,
        clients: dict[str, LLMClient],
    ) -> None:


        self._clients = clients



    def get(
        self,
        provider: str,
    ) -> LLMClient:
        """
        Get provider client.

        Example:

        openai
        mock

        """


        client = self._clients.get(
            provider
        )


        if client is None:

            raise ProviderNotFoundError(
                f"LLM provider '{provider}' not found."
            )


        return client




    def resolve(
        self,
        provider: str,
    ) -> LLMClient:
        """
        Resolve provider client.

        Central entry point.

        Future:

        - health check
        - circuit breaker
        - routing policy

        """

        return self.get(
            provider
        )




    def fallback_candidates(
        self,
        primary_provider: str,
    ) -> list[str]:
        """
        Return fallback providers.

        Current policy:

        Use all available providers
        except primary.

        """

        return [

            provider

            for provider
            in self._clients.keys()

            if provider != primary_provider

        ]




    def list(
        self,
    ) -> list[str]:
        """
        List available providers.
        """


        return list(
            self._clients.keys()
        )




    def exists(
        self,
        provider: str,
    ) -> bool:
        """
        Check provider availability.
        """


        return (
            provider
            in
            self._clients
        )




    def health(
        self,
    ) -> dict[str, bool]:
        """
        Provider health status.

        Current:

        Only check registration.

        Future:

        - active probe
        - latency check
        - error rate
        """

        return {

            provider: True

            for provider
            in self._clients.keys()

        }