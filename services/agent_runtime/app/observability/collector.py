from datetime import datetime, UTC

from services.agent_runtime.app.observability.models import (
    TraceEvent,
)


class TraceCollector:
    """
    Collect agent execution traces.
    """


    def __init__(self):

        self._traces: list[TraceEvent] = []



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


        self._traces.append(
            event
        )


        return event



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

        return self._traces