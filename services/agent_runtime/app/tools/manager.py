from typing import Any

from services.agent_runtime.app.tools.registry import (
    ToolRegistry,
)

from services.agent_runtime.app.observability.models import (
    TraceSpan,
)



class ToolManager:
    """
    Runtime tool manager.
    """


    def __init__(
        self,
        registry: ToolRegistry,
    ) -> None:

        self.registry = registry



    async def call(
        self,
        name: str,
        context=None,
        **kwargs: Any,
    ) -> dict:


        print("=" * 80)

        print("TOOL CALL START")

        print("TOOL NAME:")

        print(name)


        print()

        print("TOOL INPUT:")

        print(kwargs)

        print("=" * 80)



        tool = self.registry.get(
            name
        )


        span: TraceSpan | None = None



        #
        # Create Trace Span
        #
        # Unified observability path
        #

        if context and context.trace:


            span = TraceSpan(

                type="tool",

                name=name,

                start_time=context.trace.start_time,

                input_data=kwargs,

            )



            context.trace.spans.append(
                span
            )



        try:


            result = await tool.execute(
                **kwargs
            )



            if span:


                from datetime import UTC, datetime


                span.end_time = datetime.now(
                    UTC
                )


                span.duration_ms = (

                    span.end_time

                    -

                    span.start_time

                ).total_seconds() * 1000



                span.success = True


                span.output_data = result



            print("=" * 80)

            print("TOOL RESULT")

            print("TOOL NAME:")

            print(name)


            print()

            print(result)

            print("=" * 80)



            return result



        except Exception as exc:



            if span:


                from datetime import UTC, datetime


                span.end_time = datetime.now(
                    UTC
                )


                span.duration_ms = (

                    span.end_time

                    -

                    span.start_time

                ).total_seconds() * 1000



                span.success = False


                span.error = str(
                    exc
                )



            print("=" * 80)

            print("TOOL ERROR")

            print("TOOL NAME:")

            print(name)


            print()

            print(type(exc))

            print(str(exc))


            print("=" * 80)


            raise