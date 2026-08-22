from __future__ import annotations

from typing import Any

from .event_bus import AgentEvent, agent_event_bus


class MCPEventAdapter:
    """Convert MCP client lifecycle events into investigation stream events."""

    def tool_started(self, incident_id: str, tool: str) -> None:
        agent_event_bus.publish(
            AgentEvent(
                event_type="mcp_tool_started",
                incident_id=incident_id,
                payload={"tool": tool},
            )
        )

    def tool_completed(
        self,
        incident_id: str,
        tool: str,
        result: Any,
        latency_ms: int,
    ) -> None:
        agent_event_bus.publish(
            AgentEvent(
                event_type="mcp_tool_completed",
                incident_id=incident_id,
                payload={
                    "tool": tool,
                    "latency_ms": latency_ms,
                    "result": result,
                },
            )
        )


mcp_event_adapter = MCPEventAdapter()
