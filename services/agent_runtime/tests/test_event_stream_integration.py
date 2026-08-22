from services.agent_runtime.app.events.event_bus import AgentEvent, agent_event_bus


def test_agent_event_model_supports_streaming_payload():
    event = AgentEvent(
        event_type="tool_call",
        incident_id="inc-test",
        payload={"tool": "kubernetes-mcp"},
    )

    assert event.event_type == "tool_call"
    assert event.incident_id == "inc-test"
    assert event.payload["tool"] == "kubernetes-mcp"
    assert agent_event_bus is not None
