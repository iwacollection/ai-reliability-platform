from services.agent_runtime.app.observability.models import (
    TraceEvent,
)



class TraceStore:
    """
    Trace persistence abstraction.

    First version:
    in-memory storage.

    Later:
    - SQLite
    - PostgreSQL
    - ClickHouse
    - OpenTelemetry backend
    """


    def __init__(self):

        self._traces: list[TraceEvent] = []



    def save(
        self,
        trace: TraceEvent,
    ) -> None:
        """
        Save trace event.
        """


        self._traces.append(
            trace
        )



    def list(
        self,
    ) -> list[TraceEvent]:
        """
        List all traces.
        """


        return self._traces



    def get(
        self,
        trace_id: str,
    ) -> TraceEvent | None:
        """
        Get trace by id.
        """


        for trace in self._traces:

            if trace.trace_id == trace_id:

                return trace



        return None