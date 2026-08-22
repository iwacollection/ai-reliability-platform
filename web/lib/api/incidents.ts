export interface Incident {
  id: string;
  name: string;
  severity: string;
  status: string;
}

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

export async function listIncidents(): Promise<Incident[]> {
  const response = await fetch(`${API_BASE}/api/incidents`);

  if (!response.ok) {
    throw new Error("failed to fetch incidents");
  }

  return response.json();
}
