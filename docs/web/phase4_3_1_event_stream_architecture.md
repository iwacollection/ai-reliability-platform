# Phase 4.3.1 Agent Runtime Event Streaming

## Flow

```
LangGraph Workflow
        |
        v
Event Adapter
        |
        v
Agent Event Bus
        |
        v
Gateway SSE
        |
        v
Investigation Workspace
```

## Event Types

- workflow_node_started
- tool_call
- mcp_tool_started
- mcp_tool_completed
- state_update
- evidence_added
- rca_updated

## Goal

Provide a unified event stream for real-time investigation visibility.
