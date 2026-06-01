"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { Button, Card, CardHeader, Pill, Stat } from "@/components/ui";
import { useAuth } from "@/lib/auth-context";
import { createApi, type ApprovalStatus, type ExportRecord, type Run } from "@/lib/api";

const STATUS_TONE: Record<ApprovalStatus, "neutral" | "warn" | "ok" | "error" | "brand"> = {
  draft: "neutral",
  under_review: "warn",
  approved: "ok",
  rejected: "error",
  delivered: "brand",
};

const CLIENT_STATUS_LABEL: Record<ApprovalStatus, string> = {
  draft: "Report is being prepared by Altera.",
  under_review: "Report is being reviewed by the Altera methodology team.",
  approved: "Report has been approved and is available for download.",
  rejected: "Report is being revised by Altera.",
  delivered: "Report has been delivered.",
};

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
  const [actionBusy, setActionBusy] = useState<string | null>(null);
  const [rejectReason, setRejectReason] = useState<Record<string, string>>({});

  const isMethodologyLead = currentUser?.role === "altera_methodology_lead";
  const isAdmin = currentUser?.role === "altera_admin";
  const canDeliver = isMethodologyLead || isAdmin;

  const refreshExports = useCallback(async () => {
    if (!id || !runId) return;
    try {
      setExports((await api.listExports(id, runId)).items);
    } catch {
      // non-fatal
    }
  }, [api, id, runId]);

  useEffect(() => {
    if (loading || !id || !runId) return;
    let active = true;
    Promise.all([api.getRun(id, runId), api.listExports(id, runId)])
      .then(([r, exps]) => {
        if (!active) return;
        setRun(r);
        setExports(exps.items);
      })
      .catch((e: Error) => {
        if (active) setError(e.message);
      });
    return () => { active = false; };
  }, [api, id, runId, loading]);

  async function handleDownload(fmt: "csv" | "json" | "md") {
    setExportError(null);
    try {
      await api.downloadExport(id, runId, fmt);
    } catch (e) {
      setExportError(e instanceof Error ? e.message : "Download failed");
    }
  }

  async function withAction(exportId: string, fn: () => Promise<unknown>) {
    setActionBusy(exportId);
    setExportError(null);
    try {
      await fn();
      await refreshExports();
    } catch (e) {
      setExportError(e instanceof Error ? e.message : "Action failed");
    } finally {
      setActionBusy(null);
    }
  }

  if (error)
    return (
      <div className="mx-auto max-w-3xl">
        <div className="rounded-md border border-danger-100 bg-danger-50 px-4 py-3 text-sm text-danger-700">
          {error}
        </div>
      </div>
    );
  if (!run) return <div className="text-sm text-ink-soft">Loading…</div>;

  const summary = run.summary as Record<string, unknown>;
  const isPt = run.methodology === "protein_tracker";
  const clientVisibleExports = exports.filter(
    (e) => e.approval_status === "approved" || e.approval_status === "delivered",
  );
  const hasClientExport = clientVisibleExports.length > 0;

  // For clients: determine the highest lifecycle status across all visible exports
  const latestClientStatus: ApprovalStatus | null =
    !isAltera && exports.length > 0
      ? (["delivered", "approved", "under_review", "rejected", "draft"] as ApprovalStatus[]).find(
          (s) => exports.some((e) => e.approval_status === s),
        ) ?? null
      : null;

  return (
    <div className="mx-auto max-w-5xl">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            Run {run.id.slice(0, 8)}
          </h1>
          <div className="mt-1 flex items-center gap-2 text-sm text-ink-soft">
            <Pill tone="brand">{run.methodology}</Pill>
            <span>{new Date(run.started_at).toLocaleString()}</span>
            <span>·</span>
            <span>{run.rows_count} rows</span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Link href={`/projects/${id}/runs/${runId}/report`}>
            <Button variant="secondary">View Report</Button>
          </Link>
          <Link href={`/projects/${id}/runs`}>
            <Button variant="ghost">← All runs</Button>
          </Link>
        </div>
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
                ? "Manage the report lifecycle: submit for review, approve, reject, and deliver."
                : hasClientExport
                  ? "Your approved report is available for download."
                  : "Reports are reviewed and approved by the Altera methodology team."
            }
          />

          {exportError && (
            <div className="mt-3 rounded-md border border-danger-100 bg-danger-50 px-3 py-2 text-sm text-danger-700">
              {exportError}
            </div>
          )}

          {/* Client status banner */}
          {!isAltera && latestClientStatus && (
            <div className="mt-4 flex items-center gap-3">
              <Pill tone={STATUS_TONE[latestClientStatus]}>{latestClientStatus.replace("_", " ")}</Pill>
              <span className="text-sm text-ink-muted">
                {CLIENT_STATUS_LABEL[latestClientStatus]}
              </span>
            </div>
          )}

          {/* Download buttons */}
          {(isAltera || hasClientExport) && (
            <div className="mt-4 flex flex-wrap gap-2">
              {(["csv", "json", "md"] as const).map((fmt) => (
                <Button key={fmt} variant="secondary" onClick={() => handleDownload(fmt)}>
                  Download {fmt.toUpperCase()}
                </Button>
              ))}
            </div>
          )}

          {!isAltera && !hasClientExport && (
            <div className="mt-4 rounded-md border border-warn-100 bg-warn-50 px-3 py-2 text-sm text-warn-700">
              {latestClientStatus
                ? CLIENT_STATUS_LABEL[latestClientStatus]
                : "Awaiting approval from the Altera methodology team."}
            </div>
          )}

          {/* Altera export list with lifecycle controls */}
          {isAltera && exports.length > 0 && (
            <div className="mt-6">
              <div className="text-xs font-semibold uppercase tracking-wider text-ink-soft">
                Export records
              </div>
              <ul className="mt-2 divide-y divide-gray-100">
                {exports.map((exp) => (
                  <li key={exp.id} className="py-3 text-sm">
                    <div className="flex items-center justify-between">
                      <div>
                        <span className="font-medium">{exp.filename}</span>
                        <span className="ml-2 text-xs text-ink-soft">
                          {new Date(exp.created_at).toLocaleString()}
                        </span>
                        {exp.client_download_count > 0 && (
                          <span className="ml-2 text-xs text-gray-400">
                            · {exp.client_download_count} client download{exp.client_download_count !== 1 ? "s" : ""}
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-2">
                        <Pill tone={STATUS_TONE[exp.approval_status]}>
                          {exp.approval_status.replace("_", " ")}
                        </Pill>

                        {/* Submit for review */}
                        {isAltera &&
                          exp.approval_status !== "delivered" &&
                          exp.approval_status !== "under_review" && (
                            <Button
                              variant="ghost"
                              onClick={() =>
                                withAction(exp.id, () => api.submitExportForReview(id, runId, exp.id))
                              }
                              disabled={actionBusy === exp.id}
                            >
                              Submit for review
                            </Button>
                          )}

                        {/* Approve / Reject — methodology lead only, on draft or under_review */}
                        {isMethodologyLead &&
                          (exp.approval_status === "draft" || exp.approval_status === "under_review") && (
                            <>
                              <Button
                                variant="primary"
                                onClick={() =>
                                  withAction(exp.id, () => api.approveExport(id, runId, exp.id))
                                }
                                disabled={actionBusy === exp.id}
                              >
                                {actionBusy === exp.id ? "…" : "Approve"}
                              </Button>
                              <Button
                                variant="ghost"
                                onClick={() =>
                                  withAction(exp.id, () =>
                                    api.rejectExport(id, runId, exp.id, rejectReason[exp.id]),
                                  )
                                }
                                disabled={actionBusy === exp.id}
                              >
                                Reject
                              </Button>
                            </>
                          )}

                        {/* Deliver — methodology lead or admin, approved only */}
                        {canDeliver && exp.approval_status === "approved" && (
                          <Button
                            variant="secondary"
                            onClick={() =>
                              withAction(exp.id, () => api.deliverExport(id, runId, exp.id))
                            }
                            disabled={actionBusy === exp.id}
                          >
                            {actionBusy === exp.id ? "…" : "Deliver to client"}
                          </Button>
                        )}
                      </div>
                    </div>

                    {/* Rejection reason */}
                    {isMethodologyLead &&
                      (exp.approval_status === "draft" || exp.approval_status === "under_review") && (
                        <div className="mt-2 ml-0">
                          <input
                            type="text"
                            placeholder="Rejection reason (optional)"
                            value={rejectReason[exp.id] ?? ""}
                            onChange={(e) =>
                              setRejectReason((prev) => ({ ...prev, [exp.id]: e.target.value }))
                            }
                            className="w-64 rounded border border-gray-200 px-2 py-1 text-xs text-gray-700 focus:outline-none focus:ring-1 focus:ring-gray-300"
                          />
                        </div>
                      )}

                    {/* Approval metadata */}
                    {exp.rejection_reason && (
                      <div className="mt-1 text-xs text-rose-600">
                        Rejection reason: {exp.rejection_reason}
                      </div>
                    )}
                    {exp.approved_at && (
                      <div className="mt-1 text-xs text-gray-400">
                        Approved {new Date(exp.approved_at).toLocaleString()}
                      </div>
                    )}
                    {exp.delivered_at && (
                      <div className="mt-1 text-xs text-gray-400">
                        Delivered {new Date(exp.delivered_at).toLocaleString()}
                      </div>
                    )}
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
          <thead className="text-xs uppercase tracking-wider text-ink-soft">
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
          <thead className="text-xs uppercase tracking-wider text-ink-soft">
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
