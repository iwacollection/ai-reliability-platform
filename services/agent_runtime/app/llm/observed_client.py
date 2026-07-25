from datetime import UTC, datetime

from services.agent_runtime.app.llm.client import (
    LLMClient,
)

from services.agent_runtime.app.llm.models import (
    ChatRequest,
    ChatResponse,
)

from services.agent_runtime.app.llm.context import (
    get_llm_context,
)

from services.agent_runtime.app.observability.models import (
    TraceSpan,
)



class ObservedLLMClient:
    """
    LLM client wrapper with observability.

    Collect:
    - llm call count
    - model
    - token usage
    - latency
    - trace span
    """



    def __init__(
        self,
        client: LLMClient,
    ) -> None:

        self.client = client



    async def chat(
        self,
        request: ChatRequest,
        metadata: dict | None = None,
        context=None,
    ) -> ChatResponse:
        """
        Execute LLM call and record metrics.
        """

        import time


        start = time.perf_counter()



        llm_context = get_llm_context()



        trace = None


        if llm_context:

            trace = llm_context.get(
                "trace"
            )


            if metadata is None:

                metadata = llm_context.get(
                    "metadata"
                )



        span = None



        #
        # LLM Trace Span
        #

        if trace:


            span = TraceSpan(

                type="llm",

                name=self.client.provider.name,

                start_time=datetime.now(
                    UTC
                ),

                input_data={

                    "system_prompt":
                    request.system_prompt,


                    "user_prompt":
                    request.user_prompt,

                },

            )


            trace.spans.append(
                span
            )



        try:


            response = await self.client.chat(
                request
            )



            duration_ms = round(

                (

                    time.perf_counter()

                    -

                    start

                )

                *

                1000,

                4,

            )



            if metadata is not None:


                llm_calls = metadata.setdefault(

                    "llm_calls",

                    [],

                )


                llm_calls.append(

                    {

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



            if span:


                span.end_time = datetime.now(
                    UTC
                )


                span.duration_ms = duration_ms


                span.success = True


                span.output_data = {

                    "model":
                    response.model,


                    "prompt_tokens":
                    response.prompt_tokens,


                    "completion_tokens":
                    response.completion_tokens,


                    "total_tokens":
                    response.total_tokens,

                }



            return response



        except Exception as exc:


            if span:


                span.end_time = datetime.now(
                    UTC
                )


                span.duration_ms = round(

                    (

                        span.end_time

                        -

                        span.start_time

                    ).total_seconds()

                    *

                    1000,

                    4,

                )


                span.success = False


                span.error = str(
                    exc
                )



            raise