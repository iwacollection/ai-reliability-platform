import { getIncidentEvidence } from '../../../lib/api/evidence';

export default async function IncidentDetailPage({
  params,
}: {
  params: { id: string };
}) {
  const evidence = await getIncidentEvidence(params.id);

  return (
    <main className="p-8 space-y-8">
      <section>
        <h1 className="text-2xl font-bold">Incident Detail</h1>
        <p>Incident: {params.id}</p>
      </section>

      <section>
        <h2 className="text-xl font-semibold">Investigation Timeline</h2>
        <div className="space-y-3 mt-4">
          {evidence.timeline.map((item) => (
            <div key={`${item.time}-${item.event}`} className="rounded border p-3">
              <b>{item.time}</b> - {item.event}
              <p className="text-sm">Source: {item.source}</p>
            </div>
          ))}
        </div>
      </section>

      <section>
        <h2 className="text-xl font-semibold">Evidence</h2>
        {evidence.items.map((item) => (
          <div key={item.name} className="rounded border p-3 mt-3">
            {item.type}: {item.name}
            <p>Confidence: {item.confidence}</p>
          </div>
        ))}
      </section>

      <section>
        <h2 className="text-xl font-semibold">RCA</h2>
        <p>{evidence.rca?.root_cause ?? 'Pending'}</p>
        <p>Confidence: {evidence.rca?.confidence ?? 0}</p>
      </section>
    </main>
  );
}
