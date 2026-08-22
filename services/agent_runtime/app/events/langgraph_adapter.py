from __future__ import annotations

from typing import Any

from .event_bus import AgentEvent, agent_event_bus


class LangGraphEventAdapter:
    """Translate workflow callbacks into platform AgentEvents."""

    def emit_node_started(self, incident_id: str, node: str) -> None:
        agent_event_bus.publish(
            AgentEvent(
                event_type="workflow_node_started",
                incident_id=incident_id,
                payload={"node": node},
            )
        )

    def emit_tool_call(
        self,
        incident_id: str,
        tool: str,
        arguments: dict[str, Any] | None = None,
    ) -> None:
        agent_event_bus.publish(
            AgentEvent(
                event_type="tool_call",
                incident_id=incident_id,
                payload={
                    "tool": tool,
                    "arguments": arguments or {},
                },
            )
        )

    def emit_state_update(
        self,
        incident_id: str,
        state: dict[str, Any],
    ) -> None:
        agent_event_bus.publish(
            AgentEvent(
                event_type="state_update",
                incident_id=incident_id,
                payload=state,
            )
        )


langgraph_adapter = LangGraphEventAdapter()
