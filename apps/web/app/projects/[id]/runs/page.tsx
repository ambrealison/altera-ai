"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import {
  createApi,
  type Methodology,
  type PTGroupComparisonResponse,
  type Run,
  type RunComparisonResponse,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { Button, Card, CardHeader, EmptyState, Pill, Stat } from "@/components/ui";

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

      {runs && runs.length >= 2 && (
        <div className="mt-6">
          <CompareRunsCard runs={runs} projectId={projectId} api={api} />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Compare runs card (Phase 27A)
// ---------------------------------------------------------------------------

function fmtDelta(val: string | null | undefined): string {
  if (val == null) return "—";
  const n = parseFloat(val);
  if (isNaN(n)) return val;
  return (n >= 0 ? "+" : "") + n.toFixed(2);
}

function deltaClass(val: string | null | undefined): string {
  if (val == null) return "text-gray-500";
  const n = parseFloat(val);
  if (isNaN(n) || n === 0) return "text-gray-500";
  return n > 0 ? "text-green-600 font-medium" : "text-rose-600 font-medium";
}

const DIRECTION_TONE: Record<string, "ok" | "error" | "neutral"> = {
  improving: "ok",
  declining: "error",
  stable: "neutral",
};

function PTComparisonResult({ pt }: { pt: NonNullable<RunComparisonResponse["pt_comparison"]> }) {
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <span className="text-xs text-gray-500">Direction:</span>
        <Pill tone={DIRECTION_TONE[pt.direction] ?? "neutral"}>{pt.direction}</Pill>
        {pt.baseline_methodology_version !== pt.comparison_methodology_version && (
          <Pill tone="warn">methodology version changed</Pill>
        )}
      </div>

      {/* Headline */}
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="text-xs uppercase tracking-wide text-gray-400">
            <th className="py-1.5">Metric</th>
            <th className="py-1.5 text-right">{pt.baseline_reporting_period}</th>
            <th className="py-1.5 text-right">{pt.comparison_reporting_period}</th>
            <th className="py-1.5 text-right">Δ</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          <tr>
            <td className="py-1.5 text-gray-700">Plant protein (kg)</td>
            <td className="py-1.5 text-right font-mono">{parseFloat(pt.baseline_plant_protein_kg).toFixed(2)}</td>
            <td className="py-1.5 text-right font-mono">{parseFloat(pt.comparison_plant_protein_kg).toFixed(2)}</td>
            <td className={`py-1.5 text-right font-mono ${deltaClass(pt.delta_plant_protein_kg)}`}>{fmtDelta(pt.delta_plant_protein_kg)}</td>
          </tr>
          <tr>
            <td className="py-1.5 text-gray-700">Animal protein (kg)</td>
            <td className="py-1.5 text-right font-mono">{parseFloat(pt.baseline_animal_protein_kg).toFixed(2)}</td>
            <td className="py-1.5 text-right font-mono">{parseFloat(pt.comparison_animal_protein_kg).toFixed(2)}</td>
            <td className={`py-1.5 text-right font-mono ${deltaClass(pt.delta_animal_protein_kg)}`}>{fmtDelta(pt.delta_animal_protein_kg)}</td>
          </tr>
          {pt.baseline_plant_share_pct != null && (
            <tr>
              <td className="py-1.5 text-gray-700">Plant share (%)</td>
              <td className="py-1.5 text-right font-mono">{parseFloat(pt.baseline_plant_share_pct).toFixed(1)}</td>
              <td className="py-1.5 text-right font-mono">{pt.comparison_plant_share_pct != null ? parseFloat(pt.comparison_plant_share_pct).toFixed(1) : "—"}</td>
              <td className={`py-1.5 text-right font-mono ${deltaClass(pt.delta_plant_share_pct)}`}>{fmtDelta(pt.delta_plant_share_pct)}</td>
            </tr>
          )}
        </tbody>
      </table>

      {/* Per-group breakdown */}
      {pt.per_group.length > 0 && (
        <details className="text-sm">
          <summary className="cursor-pointer text-xs text-blue-600 hover:underline">
            Group breakdown
          </summary>
          <table className="mt-2 w-full text-left text-sm">
            <thead>
              <tr className="text-xs uppercase tracking-wide text-gray-400">
                <th className="py-1">Group</th>
                <th className="py-1 text-right">{pt.baseline_reporting_period}</th>
                <th className="py-1 text-right">{pt.comparison_reporting_period}</th>
                <th className="py-1 text-right">Δ kg</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {pt.per_group.map((g: PTGroupComparisonResponse) => (
                <tr key={g.pt_group}>
                  <td className="py-1 text-gray-600">{g.pt_group.replace(/_/g, " ")}</td>
                  <td className="py-1 text-right font-mono">{parseFloat(g.baseline_protein_kg).toFixed(2)}</td>
                  <td className="py-1 text-right font-mono">{parseFloat(g.comparison_protein_kg).toFixed(2)}</td>
                  <td className={`py-1 text-right font-mono ${deltaClass(g.delta_protein_kg)}`}>{fmtDelta(g.delta_protein_kg)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </div>
  );
}

function WWFComparisonResult({ wwf }: { wwf: NonNullable<RunComparisonResponse["wwf_comparison"]> }) {
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <span className="text-xs text-gray-500">Direction:</span>
        <Pill tone={DIRECTION_TONE[wwf.direction] ?? "neutral"}>{wwf.direction}</Pill>
      </div>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Stat label={`Plant (${wwf.baseline_reporting_period})`} value={parseFloat(wwf.baseline_plant_weight_kg).toFixed(0) + " kg"} />
        <Stat label={`Plant (${wwf.comparison_reporting_period})`} value={parseFloat(wwf.comparison_plant_weight_kg).toFixed(0) + " kg"} />
        <Stat label="Δ plant" value={fmtDelta(wwf.delta_plant_weight_kg) + " kg"} />
        <Stat label="Direction" value={wwf.direction} />
      </div>
    </div>
  );
}

function CompareRunsCard({
  runs,
  projectId,
  api,
}: {
  runs: Run[];
  projectId: string;
  api: ReturnType<typeof createApi>;
}) {
  const [baselineId, setBaselineId] = useState(runs[runs.length - 2]?.id ?? "");
  const [comparisonId, setComparisonId] = useState(runs[runs.length - 1]?.id ?? "");
  const [result, setResult] = useState<RunComparisonResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleCompare() {
    if (!baselineId || !comparisonId) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const r = await api.getRunComparison(projectId, baselineId, comparisonId);
      setResult(r);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Comparison failed");
    } finally {
      setLoading(false);
    }
  }

  function runLabel(r: Run): string {
    const date = new Date(r.started_at).toLocaleDateString();
    return `${r.methodology.replace("_", " ")} · ${date} · ${r.id.slice(0, 8)}`;
  }

  return (
    <Card>
      <CardHeader
        title="Compare runs"
        subtitle="Year-over-year or period-to-period comparison. Deterministic — run data is never changed."
      />
      <div className="mt-4 flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-xs text-gray-600">
          Baseline (earlier)
          <select
            className="rounded border border-gray-200 px-2 py-1.5 text-sm"
            value={baselineId}
            onChange={(e) => setBaselineId(e.target.value)}
          >
            {runs.map((r) => (
              <option key={r.id} value={r.id}>{runLabel(r)}</option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs text-gray-600">
          Comparison (later)
          <select
            className="rounded border border-gray-200 px-2 py-1.5 text-sm"
            value={comparisonId}
            onChange={(e) => setComparisonId(e.target.value)}
          >
            {runs.map((r) => (
              <option key={r.id} value={r.id}>{runLabel(r)}</option>
            ))}
          </select>
        </label>
        <Button
          onClick={handleCompare}
          disabled={loading || !baselineId || !comparisonId || baselineId === comparisonId}
        >
          {loading ? "Comparing…" : "Compare"}
        </Button>
      </div>

      {error && (
        <p className="mt-3 text-sm text-rose-600">{error}</p>
      )}

      {result && (
        <div className="mt-5">
          {result.warnings.length > 0 && (
            <div className="mb-4 rounded border border-amber-100 bg-amber-50 px-3 py-2">
              <p className="text-xs font-medium text-amber-700 mb-1">Warnings</p>
              <ul className="space-y-0.5">
                {result.warnings.map((w, i) => (
                  <li key={i} className="text-xs text-amber-700">· {w}</li>
                ))}
              </ul>
            </div>
          )}
          {result.pt_comparison && <PTComparisonResult pt={result.pt_comparison} />}
          {result.wwf_comparison && <WWFComparisonResult wwf={result.wwf_comparison} />}
        </div>
      )}

      <p className="mt-4 text-xs text-gray-400">
        PT and WWF are compared separately. No forecasting — only measured periods are compared.
      </p>
    </Card>
  );
}
