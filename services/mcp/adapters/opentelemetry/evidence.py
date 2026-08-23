from typing import Any, Dict


class TraceEvidenceCollector:
    """Convert distributed traces into investigation evidence."""

    def collect(self, trace_result: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "type": "trace",
            "source": "opentelemetry-mcp",
            "payload": trace_result,
        }
