export function StatusBadge({ status }: { status: string }) {
  return (
    <span className="rounded border px-2 py-1 text-sm">
      {status}
    </span>
  );
}
