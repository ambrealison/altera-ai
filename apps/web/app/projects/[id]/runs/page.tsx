"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { createApi, type Methodology, type Run } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { Button, Card, CardHeader, EmptyState, Pill } from "@/components/ui";

export default function RunsPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const projectId = params.id;
  const { accessToken } = useAuth();
  const api = useMemo(() => createApi(accessToken), [accessToken]);
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<Methodology | null>(null);
  const [enabled, setEnabled] = useState<Methodology[]>([]);

  const refresh = useCallback(async () => {
    try {
      const [list, project] = await Promise.all([
        api.listRuns(projectId),
        api.getProject(projectId),
      ]);
      setRuns(list);
      setEnabled(project.methodologies_enabled);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load");
    }
  }, [api, projectId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function trigger(methodology: Methodology) {
    setBusy(methodology);
    setError(null);
    try {
      const run = await api.createRun(projectId, methodology);
      router.push(`/projects/${projectId}/runs/${run.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Run failed");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="mx-auto max-w-4xl">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">Runs</h1>
        <Button variant="ghost" onClick={() => router.push(`/projects/${projectId}`)}>
          ← Back to project
        </Button>
      </div>
      <p className="mt-1 text-sm text-gray-600">
        Each run computes the methodology summary from the active classifications
        for every product in this project.
      </p>

      {error && (
        <div className="mt-4 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">
          {error}
        </div>
      )}

      <div className="mt-6">
        <Card>
          <CardHeader title="Trigger a calculation" />
          <div className="mt-4 flex flex-wrap gap-2">
            {enabled.map((m) => (
              <Button
                key={m}
                onClick={() => trigger(m)}
                disabled={busy !== null}
                variant={m === "protein_tracker" ? "primary" : "secondary"}
              >
                {busy === m ? "Running…" : `Run ${m}`}
              </Button>
            ))}
          </div>
        </Card>
      </div>

      <div className="mt-6">
        <Card>
          <CardHeader title="Past runs" />
          {runs === null ? (
            <div className="mt-3 text-sm text-gray-500">Loading…</div>
          ) : runs.length === 0 ? (
            <div className="mt-4">
              <EmptyState title="No runs yet" description="Trigger one above." />
            </div>
          ) : (
            <ul className="mt-4 divide-y divide-gray-100">
              {runs.map((r) => (
                <li key={r.id} className="flex items-center justify-between py-3 text-sm">
                  <div>
                    <Link
                      href={`/projects/${projectId}/runs/${r.id}`}
                      className="font-medium text-brand-700 hover:underline"
                    >
                      {r.id.slice(0, 8)}
                    </Link>
                    <div className="text-xs text-gray-500">
                      {new Date(r.started_at).toLocaleString()} · {r.rows_count} rows
                    </div>
                  </div>
                  <Pill tone="brand">{r.methodology}</Pill>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}
