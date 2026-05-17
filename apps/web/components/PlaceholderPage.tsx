export function PlaceholderPage({
  title,
  description,
  plannedPhase,
}: {
  title: string;
  description: string;
  plannedPhase: string;
}) {
  return (
    <div className="mx-auto max-w-3xl">
      <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
      <p className="mt-2 text-sm text-gray-600">{description}</p>

      <div className="mt-8 rounded-lg border border-dashed border-gray-300 bg-white p-6">
        <div className="text-xs font-semibold uppercase tracking-wider text-gray-500">
          Not implemented yet
        </div>
        <p className="mt-2 text-sm text-gray-700">
          This view is a placeholder. Functional implementation lands in:
        </p>
        <p className="mt-2 text-sm font-medium text-brand-700">
          {plannedPhase}
        </p>
      </div>
    </div>
  );
}
