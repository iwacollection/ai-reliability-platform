export interface EvidenceTimeline {
  time: string;
  event: string;
  source: string;
}

export interface IncidentEvidence {
  timeline: EvidenceTimeline[];
  items: Array<{
    type: string;
    name: string;
    confidence: number;
  }>;
  rca?: {
    root_cause: string;
    confidence: number;
  } | null;
}

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

export async function getIncidentEvidence(id: string): Promise<IncidentEvidence> {
  const response = await fetch(`${API_BASE}/api/incidents/${id}/evidence`, {
    cache: "no-store",
  });

  if (!response.ok) {
    throw new Error("failed to fetch evidence");
  }

  return response.json();
}
