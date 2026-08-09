from services.agent_runtime.app.llm.client import (
    LLMClient,
)

from services.agent_runtime.app.llm.gateway.provider_health import (
    ProviderHealthManager,
)



class FallbackManager:
    """
    Manage LLM provider fallback.

    Responsibilities:

    - provide backup provider
    - filter unhealthy provider


    Not responsible:

    - routing
    - retry
    - timeout
    - circuit state

    """



    def __init__(
        self,
        clients: dict[str, LLMClient],
        health_manager: ProviderHealthManager,
    ) -> None:


        self.clients = clients


        self.health_manager = (
            health_manager
        )



    def get_fallback(
        self,
        failed_provider: str,
    ) -> LLMClient | None:
        """
        Select healthy fallback provider.

        Strategy:

        1. Skip failed provider
        2. Skip unhealthy provider
        3. Return first healthy provider

        """



        for name, client in self.clients.items():


            #
            # Skip primary provider
            #

            if name == failed_provider:

                continue



            #
            # Skip unhealthy provider
            #

            if not self.health_manager.is_healthy(
                name
            ):

                continue



            return client



        return None