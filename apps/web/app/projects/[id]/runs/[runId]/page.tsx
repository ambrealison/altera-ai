"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { Button, Card, CardHeader, Pill, Stat } from "@/components/ui";
import { useAuth } from "@/lib/auth-context";
import { createApi, type ExportRecord, type Run } from "@/lib/api";

export default function RunDetail() {
  const params = useParams<{ id: string; runId: string }>();
  const id = params.id;
  const runId = params.runId;
  const { accessToken, loading, isAltera, currentUser } = useAuth();
  const api = useMemo(() => createApi(accessToken), [accessToken]);
  const [run, setRun] = useState<Run | null>(null);
  const [exports, setExports] = useState<ExportRecord[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);
  const [approvalBusy, setApprovalBusy] = useState<string | null>(null);

  const isMethodologyLead = currentUser?.role === "altera_methodology_lead";

  const refreshExports = useCallback(async () => {
    if (!id || !runId) return;
    try {
      setExports(await api.listExports(id, runId));
    } catch {
      // non-fatal; exports section just stays empty
    }
  }, [api, id, runId]);

  useEffect(() => {
    if (loading || !id || !runId) return;
    let active = true;
    Promise.all([api.getRun(id, runId), api.listExports(id, runId)])
      .then(([r, exps]) => {
        if (!active) return;
        setRun(r);
        setExports(exps);
      })
      .catch((e: Error) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [api, id, runId, loading]);

  async function handleDownload(fmt: "csv" | "json" | "md") {
    setExportError(null);
    try {
      await api.downloadExport(id, runId, fmt);
    } catch (e) {
      setExportError(e instanceof Error ? e.message : "Download failed");
    }
  }

  async function handleApprove(exportId: string) {
    setApprovalBusy(exportId);
    try {
      await api.approveExport(id, runId, exportId);
      await refreshExports();
    } catch (e) {
      setExportError(e instanceof Error ? e.message : "Approval failed");
    } finally {
      setApprovalBusy(null);
    }
  }

  async function handleReject(exportId: string) {
    setApprovalBusy(exportId);
    try {
      await api.rejectExport(id, runId, exportId);
      await refreshExports();
    } catch (e) {
      setExportError(e instanceof Error ? e.message : "Rejection failed");
    } finally {
      setApprovalBusy(null);
    }
  }

  if (error)
    return (
      <div className="mx-auto max-w-3xl">
        <div className="rounded-md border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
          {error}
        </div>
      </div>
    );
  if (!run) return <div className="text-sm text-gray-500">Loading…</div>;

  const summary = run.summary as Record<string, unknown>;
  const isPt = run.methodology === "protein_tracker";
  const approvedExports = exports.filter((e) => e.approval_status === "approved");
  const hasApprovedExport = approvedExports.length > 0;

  return (
    <div className="mx-auto max-w-5xl">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            Run {run.id.slice(0, 8)}
          </h1>
          <div className="mt-1 flex items-center gap-2 text-sm text-gray-500">
            <Pill tone="brand">{run.methodology}</Pill>
            <span>{new Date(run.started_at).toLocaleString()}</span>
            <span>·</span>
            <span>{run.rows_count} rows</span>
          </div>
        </div>
        <Link href={`/projects/${id}/runs`}>
          <Button variant="ghost">← All runs</Button>
        </Link>
      </div>

      <section className="mt-8">
        {isPt ? <PTSummary summary={summary} /> : <WWFSummary summary={summary} />}
      </section>

      <section className="mt-8">
        <Card>
          <CardHeader
            title="Exports"
            subtitle={
              isAltera
                ? "Download the per-row CSV, the full JSON, or a Markdown summary."
                : hasApprovedExport
                  ? "Approved exports are available for download."
                  : "Reports are reviewed and approved by the Altera methodology team before download."
            }
          />

          {exportError && (
            <div className="mt-3 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">
              {exportError}
            </div>
          )}

          {/* Download buttons — always shown for Altera; only shown to clients when an approved export exists */}
          {(isAltera || hasApprovedExport) && (
            <div className="mt-4 flex flex-wrap gap-2">
              {(["csv", "json", "md"] as const).map((fmt) => (
                <Button
                  key={fmt}
                  variant="secondary"
                  onClick={() => handleDownload(fmt)}
                >
                  Download {fmt.toUpperCase()}
                </Button>
              ))}
            </div>
          )}

          {!isAltera && !hasApprovedExport && (
            <div className="mt-4 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
              Awaiting approval from the Altera methodology team.
            </div>
          )}

          {/* Export approval list — Altera users see all exports with approve/reject controls */}
          {isAltera && exports.length > 0 && (
            <div className="mt-6">
              <div className="text-xs font-semibold uppercase tracking-wider text-gray-500">
                Export records
              </div>
              <ul className="mt-2 divide-y divide-gray-100">
                {exports.map((exp) => (
                  <li key={exp.id} className="flex items-center justify-between py-3 text-sm">
                    <div>
                      <span className="font-medium">{exp.filename}</span>
                      <span className="ml-2 text-xs text-gray-500">
                        {new Date(exp.created_at).toLocaleString()}
                      </span>
                    </div>
                    <div className="flex items-center gap-2">
                      <Pill
                        tone={
                          exp.approval_status === "approved"
                            ? "ok"
                            : exp.approval_status === "rejected"
                              ? "error"
                              : "warn"
                        }
                      >
                        {exp.approval_status}
                      </Pill>
                      {isMethodologyLead && exp.approval_status === "draft" && (
                        <>
                          <Button
                            variant="primary"
                            onClick={() => handleApprove(exp.id)}
                            disabled={approvalBusy === exp.id}
                          >
                            {approvalBusy === exp.id ? "…" : "Approve"}
                          </Button>
                          <Button
                            variant="ghost"
                            onClick={() => handleReject(exp.id)}
                            disabled={approvalBusy === exp.id}
                          >
                            Reject
                          </Button>
                        </>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </Card>
      </section>
    </div>
  );
}

function fmt(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function PTSummary({ summary }: { summary: Record<string, unknown> }) {
  const groups = (summary.per_group as Array<Record<string, unknown>>) ?? [];
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader title="Headline" subtitle="Plant vs animal protein share." />
        <div className="mt-4 grid grid-cols-2 gap-6 sm:grid-cols-4">
          <Stat label="Plant kg" value={fmt(summary.plant_protein_kg)} />
          <Stat label="Animal kg" value={fmt(summary.animal_protein_kg)} />
          <Stat label="Plant %" value={fmt(summary.plant_share_pct)} />
          <Stat label="Animal %" value={fmt(summary.animal_share_pct)} />
        </div>
      </Card>
      <Card>
        <CardHeader title="Per group" />
        <table className="mt-4 w-full text-left text-sm">
          <thead className="text-xs uppercase tracking-wider text-gray-500">
            <tr>
              <th className="py-2">Group</th>
              <th className="py-2 text-right">Items</th>
              <th className="py-2 text-right">Volume (kg)</th>
              <th className="py-2 text-right">Protein (kg)</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {groups.map((g, i) => (
              <tr key={i}>
                <td className="py-2 font-medium">{fmt(g.pt_group)}</td>
                <td className="py-2 text-right">{fmt(g.item_count)}</td>
                <td className="py-2 text-right">{fmt(g.volume_kg)}</td>
                <td className="py-2 text-right">{fmt(g.protein_kg)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

function WWFSummary({ summary }: { summary: Record<string, unknown> }) {
  const groups = (summary.per_food_group as Array<Record<string, unknown>>) ?? [];
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader title="Headline" subtitle="Total in-scope sales weight and composites." />
        <div className="mt-4 grid grid-cols-2 gap-6 sm:grid-cols-3">
          <Stat label="In-scope kg" value={fmt(summary.total_sales_weight_in_scope_kg)} />
          <Stat label="Composite kg" value={fmt(summary.composites_total_weight_kg)} />
          <Stat
            label="Whole-diet animal kg"
            value={fmt(summary.whole_diet_animal_weight_kg)}
            hint="FG2 in dairy equivalents."
          />
        </div>
      </Card>
      <Card>
        <CardHeader title="Per food group" />
        <table className="mt-4 w-full text-left text-sm">
          <thead className="text-xs uppercase tracking-wider text-gray-500">
            <tr>
              <th className="py-2">Food group</th>
              <th className="py-2 text-right">Weight (kg)</th>
              <th className="py-2 text-right">Share %</th>
              <th className="py-2 text-right">PHD %</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {groups.map((g, i) => (
              <tr key={i}>
                <td className="py-2 font-medium">{fmt(g.food_group)}</td>
                <td className="py-2 text-right">{fmt(g.weight_kg)}</td>
                <td className="py-2 text-right">{fmt(g.share_pct)}</td>
                <td className="py-2 text-right">{fmt(g.phd_reference_share_pct)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      <Card>
        <CardHeader title="Composites (Step 1)" />
        <div className="mt-4 grid grid-cols-2 gap-4 text-sm sm:grid-cols-4">
          <Stat label="Meat-based" value={fmt(summary.composites_meat_based_kg)} />
          <Stat label="Seafood-based" value={fmt(summary.composites_seafood_based_kg)} />
          <Stat label="Vegetarian" value={fmt(summary.composites_vegetarian_kg)} />
          <Stat label="Vegan" value={fmt(summary.composites_vegan_kg)} />
        </div>
      </Card>
    </div>
  );
}
