from typing import Any

from datetime import UTC, datetime


from services.agent_runtime.app.mcp.base import (
    BaseMCPClient,
)


from services.agent_runtime.app.observability.models import (
    TraceSpan,
)



class MockMCPClient(BaseMCPClient):
    """
    Mock MCP client.

    First version:
    simulate MCP server call.
    """



    @property
    def name(
        self,
    ) -> str:

        return "mock_mcp"



    async def call(
        self,
        tool: str,
        context=None,
        **kwargs: Any,
    ) -> dict:
        """
        Call MCP tool.

        Later this will become:
        MCP protocol request.
        """



        span = None



        #
        # MCP Trace
        #

        if context and context.trace:


            span = TraceSpan(

                type="mcp",

                name=(

                    f"{self.name}:"

                    f"{tool}"

                ),

                start_time=datetime.now(
                    UTC
                ),

                input_data=kwargs,

            )


            context.trace.spans.append(
                span
            )



        try:


            result = {


                "success": True,


                "mcp_client":
                    self.name,


                "tool":
                    tool,


                "arguments":
                    kwargs,


                "message":
                    "MCP call simulated",

            }



            if span:


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



            return result



        except Exception as exc:



            if span:


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



            raise