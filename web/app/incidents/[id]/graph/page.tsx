import { EvidenceGraph } from '../../../../components/investigation/evidence-graph';
import { AgentTraceTree } from '../../../../components/investigation/agent-trace-tree';
import { getInvestigationGraph } from '../../../../lib/api/investigation';

export default async function IncidentGraphPage({
  params,
}: {
  params: { id: string };
}) {
  const data = await getInvestigationGraph(params.id);

  const nodes = data.nodes.map((node) => ({
    id: node.id,
    position: { x: 0, y: 0 },
    data: {
      label: `${node.label}${node.confidence ? ` (${node.confidence})` : ''}`,
    },
  }));

  const edges = data.edges.map((edge) => ({
    id: `${edge.source}-${edge.target}`,
    source: edge.source,
    target: edge.target,
    label: edge.relation,
  }));

  return (
    <main className="space-y-6 p-8">
      <h1 className="text-2xl font-bold">Investigation Graph</h1>

      <section>
        <EvidenceGraph nodes={nodes} edges={edges} />
      </section>

      <section>
        <h2 className="mb-3 text-xl font-semibold">Agent Investigation Trace</h2>
        <AgentTraceTree traces={data.traces} />
      </section>

      {data.rca && (
        <section className="rounded border p-4">
          <h2 className="font-semibold">RCA</h2>
          <p>{data.rca.root_cause}</p>
          <p>Confidence: {data.rca.confidence}</p>
        </section>
      )}
    </main>
  );
}
