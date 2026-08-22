interface TraceItem {
  id: string;
  stage: string;
  tool?: string;
  latency_ms?: number;
  status: string;
}

export function AgentTraceTree({ traces }: { traces: TraceItem[] }) {
  return (
    <div className="space-y-3 rounded border p-4">
      {traces.map((trace) => (
        <div key={trace.id} className="rounded border p-3">
          <div className="font-semibold">{trace.stage}</div>
          <div>Tool: {trace.tool ?? "planner"}</div>
          <div>Latency: {trace.latency_ms ?? 0}ms</div>
          <div>Status: {trace.status}</div>
        </div>
      ))}
    </div>
  );
}
