import { StatusBadge } from '../../components/console/status-badge';

const incidents = [
  {
    id: 'inc-001',
    service: 'payment-api',
    title: 'Memory pressure and OOMKilled',
    severity: 'Critical',
    status: 'Investigating',
  },
  {
    id: 'inc-002',
    service: 'gateway',
    title: 'API latency spike',
    severity: 'Warning',
    status: 'Analyzing',
  },
];

export default function IncidentsPage() {
  return (
    <main className="p-8">
      <h2 className="mb-6 text-2xl font-bold">Incident Center</h2>
      <div className="space-y-4">
        {incidents.map((incident) => (
          <div key={incident.id} className="rounded border p-4">
            <div className="flex justify-between">
              <div>
                <h3 className="font-semibold">{incident.title}</h3>
                <p>{incident.service}</p>
              </div>
              <StatusBadge status={incident.status} />
            </div>
            <p className="mt-2">Severity: {incident.severity}</p>
          </div>
        ))}
      </div>
    </main>
  );
}
