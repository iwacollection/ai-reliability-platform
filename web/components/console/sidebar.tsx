export function Sidebar() {
  const items = [
    'Dashboard',
    'Incidents',
    'Investigation',
    'Agents',
    'MCP Tools',
    'Approvals',
    'Verification',
    'Evaluation',
  ];

  return (
    <aside className="w-64 border-r p-4">
      <h1 className="mb-6 text-xl font-bold">AI Reliability Console</h1>
      <nav className="space-y-2">
        {items.map((item) => (
          <div key={item} className="rounded p-2 hover:bg-gray-100">
            {item}
          </div>
        ))}
      </nav>
    </aside>
  );
}
