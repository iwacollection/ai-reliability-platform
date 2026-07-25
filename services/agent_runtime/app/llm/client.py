from time import perf_counter

from services.agent_runtime.app.llm.base import (
    BaseLLMProvider,
)

from services.agent_runtime.app.llm.models import (
    ChatRequest,
    ChatResponse,
)

from services.agent_runtime.app.llm.context import (
    get_llm_metadata,
)



class LLMClient:
    """
    Unified LLM client.

    With LLM observability support.
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


        start = perf_counter()


        response = await self.provider.chat(
            request
        )


        duration_ms = round(

            (
                perf_counter()
                -
                start
            )
            *
            1000,

            4,

        )


        metadata = get_llm_metadata()


        if metadata is not None:


            llm_calls = metadata.setdefault(
                "llm_calls",
                [],
            )


            llm_calls.append(

                {

                    "provider":
                    self.provider.name,


                    "model":
                    response.model,


                    "prompt_tokens":
                    response.prompt_tokens,


                    "completion_tokens":
                    response.completion_tokens,


                    "total_tokens":
                    response.total_tokens,


                    "duration_ms":
                    duration_ms,

                }

            )


        return response