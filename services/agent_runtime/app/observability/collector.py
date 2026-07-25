from datetime import datetime, UTC

from services.agent_runtime.app.observability.models import (
    TraceEvent,
    TraceSpan,
)

from services.agent_runtime.app.observability.store import (
    TraceStore,
)



class TraceCollector:
    """
    Collect agent execution traces.
    """


    def __init__(self):

        self.store = TraceStore()



    def start(
        self,
        agent: str,
        trace_id: str,
        input_data: dict | None = None,
    ) -> TraceEvent:
        """
        Start agent trace.
        """


        event = TraceEvent(

            trace_id=trace_id,

            agent=agent,

            start_time=datetime.now(
                UTC
            ),

            input_data=(
                input_data
                or {}
            ),
        )


        self.store.save(
            event
        )


        return event



    def start_span(
        self,
        trace: TraceEvent,
        span_type: str,
        name: str,
        input_data: dict | None = None,
    ) -> TraceSpan:
        """
        Start child execution span.

        Used for:
        - tool
        - skill
        - llm
        - mcp
        """


        span = TraceSpan(

            type=span_type,

            name=name,

            start_time=datetime.now(
                UTC
            ),

            input_data=(
                input_data
                or {}
            ),

        )


        trace.spans.append(
            span
        )


        return span



    def finish_span(
        self,
        span: TraceSpan,
        success: bool = True,
        output_data: dict | None = None,
        error: str | None = None,
    ) -> None:
        """
        Finish child execution span.
        """


        end_time = datetime.now(
            UTC
        )


        span.end_time = end_time


        span.duration_ms = (

            end_time

            -

            span.start_time

        ).total_seconds() * 1000



        span.success = success


        span.output_data = (
            output_data
            or {}
        )


        span.error = error



    def finish(
        self,
        trace: TraceEvent,
        success: bool,
        score: float,
        message: str,
        output_data: dict | None = None,
    ) -> None:
        """
        Finish agent trace.
        """


        end_time = datetime.now(
            UTC
        )


        trace.end_time = end_time


        trace.duration_ms = (

            end_time

            -

            trace.start_time

        ).total_seconds() * 1000



        trace.success = success


        trace.score = score


        trace.message = message


        trace.output_data = (

            output_data

            or {}

        )



    def list(
        self,
    ) -> list[TraceEvent]:

        return self.store.list()