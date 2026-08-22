export interface Incident {
  id: string;
  title: string;
  service: string;
  severity: string;
  status: string;
  agent?: string;
}

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

export async function listIncidents(): Promise<Incident[]> {
  const response = await fetch(`${API_BASE}/api/incidents`, {
    cache: "no-store",
  });

  if (!response.ok) {
    throw new Error("failed to fetch incidents");
  }

  const data = await response.json();
  return data.items ?? [];
}
