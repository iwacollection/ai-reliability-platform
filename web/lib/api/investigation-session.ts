export type InvestigationMessage = {
  role: string;
  content: string;
};

export type MCPTrace = {
  tool: string;
  status: string;
  latency_ms: number;
};

const API_BASE = process.env.NEXT_PUBLIC_GATEWAY_URL ?? "http://localhost:8000";

export async function getInvestigationSession(id: string) {
  const response = await fetch(`${API_BASE}/api/incidents/${id}/investigation`);

  if (!response.ok) {
    throw new Error("failed to load investigation session");
  }

  return response.json();
}
