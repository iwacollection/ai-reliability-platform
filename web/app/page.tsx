const incidents = [
  { name: "payment-api OOM", severity: "Critical", status: "Investigating" },
  { name: "gateway latency spike", severity: "Warning", status: "Analyzing" },
];

export default function Home() {
  return (
    <main style={{ padding: 32 }}>
      <h1>AI Reliability Console</h1>
      <p>Incident-driven autonomous reliability platform.</p>

      <h2>Active Incidents</h2>
      {incidents.map((incident) => (
        <div key={incident.name}>
          <b>{incident.name}</b> - {incident.severity} - {incident.status}
        </div>
      ))}
    </main>
  );
}
