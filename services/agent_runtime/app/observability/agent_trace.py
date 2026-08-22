"""Agent execution observability primitives.

Tracks agent spans, tool latency and token usage.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class AgentTrace:
    trace_id: str
    agent: str
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    tool_calls: list = field(default_factory=list)
    token_usage: dict = field(default_factory=dict)

    def record_tool(self, name: str, latency_ms: int, success: bool):
        self.tool_calls.append({"tool": name, "latency_ms": latency_ms, "success": success})
