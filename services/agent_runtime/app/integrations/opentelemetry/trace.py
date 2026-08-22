"""OpenTelemetry integration foundation."""

from dataclasses import dataclass


@dataclass
class TraceContext:
    trace_id: str
    span_id: str | None = None


class OpenTelemetryConnector:
    def create_span(self, name: str, context: TraceContext) -> dict:
        return {
            "name": name,
            "trace_id": context.trace_id,
            "span_id": context.span_id,
        }
