export function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    approved: "bg-green-100 text-green-800",
    active: "bg-green-100 text-green-800",
    draft: "bg-stone-200 text-stone-600",
    reviewing: "bg-blue-100 text-blue-800",
    rejected: "bg-red-100 text-red-700",
    deprecated: "bg-amber-100 text-amber-800",
    inactive: "bg-amber-100 text-amber-800",
  };
  return (
    <span className={`rounded px-2 py-0.5 text-xs ${colors[status] ?? ""}`}>
      {status}
    </span>
  );
}
