export interface EvidenceNode {
  id: string;
  type: string;
  label: string;
  confidence?: number;
}

export interface EvidenceEdge {
  source: string;
  target: string;
  relation: string;
}

export interface InvestigationTrace {
  id: string;
  stage: string;
  tool?: string;
  latency_ms?: number;
  status: string;
}

export interface InvestigationGraph {
  nodes: EvidenceNode[];
  edges: EvidenceEdge[];
  traces: InvestigationTrace[];
  rca?: {
    root_cause: string;
    confidence: number;
  };
}

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

export async function getInvestigationGraph(id: string): Promise<InvestigationGraph> {
  const response = await fetch(`${API_BASE}/api/incidents/${id}/graph`, {
    cache: "no-store",
  });

  if (!response.ok) {
    throw new Error("failed to fetch investigation graph");
  }

  return response.json();
}
