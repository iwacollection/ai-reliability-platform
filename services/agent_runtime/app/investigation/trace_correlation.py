"""OpenTelemetry trace correlation foundation.

Correlates traces with incident evidence from metrics, logs and runtime signals.
"""

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class TraceEvidence:
    trace_id: str
    service: str
    latency_ms: float
    attributes: Dict[str, str] = field(default_factory=dict)


class TraceCorrelationEngine:
    def correlate(self, traces: List[TraceEvidence], service: str):
        return [trace for trace in traces if trace.service == service]
