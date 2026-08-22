"""OpenTelemetry integration boundary."""

from dataclasses import dataclass


@dataclass
class TraceContext:
    trace_id: str
    service_name: str


class OpenTelemetryRuntime:
    def __init__(self, context: TraceContext):
        self.context = context

    def collect_trace(self) -> dict:
        return {
            "trace_id": self.context.trace_id,
            "service": self.context.service_name,
            "status": "adapter_ready",
        }
