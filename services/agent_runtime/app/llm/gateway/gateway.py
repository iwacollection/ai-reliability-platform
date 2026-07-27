from services.agent_runtime.app.llm.client import (
    LLMClient,
)

from services.agent_runtime.app.llm.models import (
    ChatRequest,
)

from services.agent_runtime.app.llm.gateway.models import (
    LLMGatewayRequest,
    LLMGatewayResponse,
)

from services.agent_runtime.app.llm.gateway.router import (
    LLMRouter,
)



class LLMGateway:
    """
    Production LLM Gateway.

    Responsibilities:

    - Route LLM request
    - Convert gateway request
    - Select provider client
    - Return unified response

    """


    def __init__(
        self,
        clients: dict[str, LLMClient],
        router: LLMRouter | None = None,
    ) -> None:


        self.clients = clients


        self.router = (
            router
            or
            LLMRouter()
        )



    async def chat(
        self,
        request: LLMGatewayRequest,
    ) -> LLMGatewayResponse:
        """
        Execute LLM request.
        """


        #
        # Route decision
        #

        route = self.router.route(
            request.context
        )



        #
        # Get provider client
        #

        client = self.clients.get(
            route.provider
        )



        if client is None:

            raise RuntimeError(

                f"LLM provider '{route.provider}' "
                "not configured."

            )



        #
        # Convert Gateway request
        #
        # Gateway model
        #        |
        #        v
        # Chat model
        #

        chat_request = ChatRequest(

            system_prompt=(
                request.system_prompt
            ),

            user_prompt=(
                request.prompt
            ),

            temperature=(
                request.temperature
            ),

        )



        #
        # Execute LLM
        #

        response = await client.chat(
            chat_request
        )



        return LLMGatewayResponse(

            content=response.content,

            provider=route.provider,

            model=response.model,

            fallback_used=False,

        )