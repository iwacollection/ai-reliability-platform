export type AgentStreamEvent = {
  type:
    | "agent_thought"
    | "tool_call"
    | "mcp_response"
    | "evidence_added"
    | "rca_updated";
  timestamp: string;
  payload: Record<string, unknown>;
};

export function createInvestigationStream(
  incidentId: string,
  onEvent: (event: AgentStreamEvent) => void,
) {
  const source = new EventSource(
    `/api/incidents/${incidentId}/investigation/stream`,
  );

  source.onmessage = (message) => {
    onEvent(JSON.parse(message.data));
  };

  return () => source.close();
}
