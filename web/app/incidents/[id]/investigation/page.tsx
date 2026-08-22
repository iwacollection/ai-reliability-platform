import { ChatPanel } from "../../../../components/investigation/chat-panel";
import { MCPToolTrace } from "../../../../components/investigation/mcp-tool-trace";

export default function InvestigationPage() {
  return (
    <main>
      <h1>Investigation Workspace</h1>

      <ChatPanel
        messages={[
          {
            role: "agent",
            content:
              "I collected Kubernetes events, Prometheus metrics and Loki logs.",
          },
        ]}
      />

      <MCPToolTrace
        traces={[
          {
            tool: "kubernetes-mcp",
            status: "completed",
            latency_ms: 120,
          },
          {
            tool: "prometheus-mcp",
            status: "completed",
            latency_ms: 230,
          },
        ]}
      />
    </main>
  );
}
