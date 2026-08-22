# Phase 4.3 Real-time Agent Streaming

## Goal

Transform Investigation Workspace from static result view into interactive operation console.

Flow:

User

-> SSE/WebSocket

-> Gateway

-> Agent Runtime

-> Workflow Events

-> Web UI

## Event Types

- agent_thought
- tool_call
- mcp_response
- evidence_added
- rca_updated

## Future Integration

- LangGraph workflow events
- MCP audit events
- Investigation timeline events
- Approval events
