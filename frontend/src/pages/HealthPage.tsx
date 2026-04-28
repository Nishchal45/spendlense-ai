import { useHealth } from '@/api/health';

// Placeholder landing page that proves the whole stack is wired:
// browser → vite dev server → /api proxy → FastAPI → JSON back. PR #B
// replaces this with a login screen the moment auth is in place.
export function HealthPage() {
  const { data, isPending, isError, error } = useHealth();

  return (
    <section className="bg-white border border-slate-200 rounded-lg p-6 shadow-sm">
      <h2 className="text-lg font-semibold mb-2">Backend health</h2>
      <p className="text-sm text-slate-600 mb-4">
        This page hits <code className="font-mono text-brand-600">GET /api/v1/health</code> through
        the dev-server proxy. If the values below render, the front-end → back-end path is alive
        end-to-end.
      </p>

      {isPending && <p className="text-slate-500">Loading…</p>}
      {isError && (
        <p className="text-red-600">Failed to reach the API: {(error as Error).message}</p>
      )}
      {data && (
        <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-2 text-sm">
          <dt className="font-medium text-slate-500">Status</dt>
          <dd>{data.status}</dd>
          <dt className="font-medium text-slate-500">Version</dt>
          <dd className="font-mono">{data.version}</dd>
          <dt className="font-medium text-slate-500">Environment</dt>
          <dd>{data.environment}</dd>
        </dl>
      )}
    </section>
  );
}
