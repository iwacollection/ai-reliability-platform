import React from "react";

type ToolTrace = {
  tool: string;
  status: string;
  latency_ms: number;
};

export function MCPToolTrace({ traces }: { traces: ToolTrace[] }) {
  return (
    <section>
      <h2>MCP Tool Trace</h2>
      {traces.map((trace, index) => (
        <div key={index}>
          <strong>{trace.tool}</strong>
          <span>{trace.status}</span>
          <span>{trace.latency_ms}ms</span>
        </div>
      ))}
    </section>
  );
}
