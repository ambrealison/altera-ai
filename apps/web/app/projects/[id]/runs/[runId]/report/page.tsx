"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { Button, Card, CardHeader, Pill, Stat } from "@/components/ui";
import { useAuth } from "@/lib/auth-context";
import {
  createApi,
  type CoverageSection,
  type PTReportSection,
  type PersistedRecommendation,
  type RecommendationItem,
  type ReportDocument,
  type WWFReportSection,
} from "@/lib/api";

const STATUS_TONE: Record<string, "neutral" | "warn" | "ok" | "error" | "brand"> = {
  draft: "neutral",
  under_review: "warn",
  approved: "ok",
  rejected: "error",
  delivered: "brand",
};

export default function ReportPage() {
  const params = useParams<{ id: string; runId: string }>();
  const { id, runId } = params;
  const { accessToken, loading, isAltera } = useAuth();
  const api = useMemo(() => createApi(accessToken), [accessToken]);

  const [report, setReport] = useState<ReportDocument | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notApproved, setNotApproved] = useState(false);

  useEffect(() => {
    if (loading || !id || !runId) return;
    let active = true;
    api
      .getReport(id, runId)
      .then((doc) => {
        if (active) setReport(doc);
      })
      .catch((e: Error) => {
        if (!active) return;
        if (e.message.startsWith("403")) {
          setNotApproved(true);
        } else {
          setError(e.message);
        }
      });
    return () => {
      active = false;
    };
  }, [api, id, runId, loading]);

  if (notApproved) {
    return (
      <div className="mx-auto max-w-3xl">
        <div className="mt-12 rounded-xl border border-amber-200 bg-amber-50 px-6 py-8 text-center">
          <div className="text-lg font-semibold text-amber-800">Report under review</div>
          <p className="mt-2 text-sm text-amber-700">
            This report is being reviewed by the Altera methodology team. It will be available
            here once approved.
          </p>
          <div className="mt-4">
            <Link href={`/projects/${id}/runs/${runId}`}>
              <Button variant="ghost">← Back to run</Button>
            </Link>
          </div>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="mx-auto max-w-3xl">
        <div className="rounded-md border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
          {error}
        </div>
      </div>
    );
  }

  if (!report) return <div className="text-sm text-gray-500">Loading…</div>;

  const { meta, executive_summary, pt_section, wwf_section, review_summary, coverage, recommendations } = report;
  const statusTone = STATUS_TONE[meta.approval_status] ?? "neutral";

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            {meta.project_name} — Report
          </h1>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-sm text-gray-500">
            <Pill tone="brand">{meta.methodology.replace("_", " ")}</Pill>
            <Pill tone={statusTone}>{meta.approval_status.replace(/_/g, " ")}</Pill>
            <span>Period: {meta.reporting_period}</span>
            <span>·</span>
            <span>Generated {new Date(meta.generated_at).toLocaleString()}</span>
          </div>
        </div>
        <Link href={`/projects/${id}/runs/${runId}`}>
          <Button variant="ghost">← Back to run</Button>
        </Link>
      </div>

      {/* Altera-only: preview banner for non-client-visible statuses */}
      {isAltera && meta.approval_status !== "approved" && meta.approval_status !== "delivered" && (
        <div className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          <span className="font-medium">Altera preview — </span>
          this report has not yet been approved for client access.
        </div>
      )}

      {/* Executive summary */}
      <Card>
        <CardHeader title="Executive Summary" />
        <p className="mt-3 text-sm leading-relaxed text-gray-700">{executive_summary}</p>
        {meta.approved_at && (
          <div className="mt-3 text-xs text-gray-400">
            Approved {new Date(meta.approved_at).toLocaleString()}
            {meta.delivered_at && (
              <> · Delivered {new Date(meta.delivered_at).toLocaleString()}</>
            )}
          </div>
        )}
      </Card>

      {/* Methodology section */}
      {pt_section && <PTSection section={pt_section} />}
      {wwf_section && <WWFSection section={wwf_section} />}

      {/* Classification sources */}
      {(pt_section || wwf_section) && (
        <Card>
          <CardHeader
            title="Classification sources"
            subtitle="How product classifications were resolved."
          />
          <div className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Stat
              label="Deterministic"
              value={String((pt_section ?? wwf_section)!.classification_sources.deterministic)}
            />
            <Stat
              label="AI"
              value={String((pt_section ?? wwf_section)!.classification_sources.ai)}
            />
            <Stat
              label="Manual review"
              value={String((pt_section ?? wwf_section)!.classification_sources.manual_review)}
            />
            <Stat
              label="Total"
              value={String((pt_section ?? wwf_section)!.classification_sources.total)}
            />
          </div>
        </Card>
      )}

      {/* Review summary — only shown to Altera */}
      {isAltera && (
        <Card>
          <CardHeader
            title="Manual review summary"
            subtitle="Queue activity for this project."
          />
          <div className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-5">
            <Stat label="Reviewed" value={String(review_summary.total_reviewed)} />
            <Stat label="Accepted" value={String(review_summary.accepted)} />
            <Stat label="Changed" value={String(review_summary.changed)} />
            <Stat label="Deferred" value={String(review_summary.deferred)} />
            <Stat label="Pending" value={String(review_summary.pending)} />
          </div>
          {review_summary.top_reasons.length > 0 && (
            <div className="mt-3 text-xs text-gray-500">
              Top queue reasons:{" "}
              {review_summary.top_reasons.map((r) => r.replace(/_/g, " ")).join(", ")}
            </div>
          )}
        </Card>
      )}

      {/* Recommendations */}
      <RecommendationsCard
        recommendations={recommendations}
        isAltera={isAltera}
        projectId={id}
        runId={runId}
        api={api}
      />

      {/* Data coverage and uncertainty */}
      <CoverageSectionCard coverage={coverage} />

      {/* Scenarios — Altera only (Phase 26A) */}
      {isAltera && meta.methodology === "protein_tracker" && (
        <ScenariosPlaceholderCard projectId={id} runId={runId} />
      )}
    </div>
  );
}

const PRIORITY_TONE: Record<string, "ok" | "warn" | "error" | "neutral"> = {
  low: "neutral",
  medium: "warn",
  high: "error",
  critical: "error",
};

const REC_STATUS_TONE: Record<string, "neutral" | "warn" | "ok" | "error" | "brand"> = {
  draft: "neutral",
  proposed: "warn",
  accepted: "ok",
  dismissed: "error",
  archived: "neutral",
};

function RecommendationsCard({
  recommendations: initialRecs,
  isAltera,
  projectId,
  runId,
  api,
}: {
  recommendations: RecommendationItem[];
  isAltera: boolean;
  projectId: string;
  runId: string;
  api: ReturnType<typeof createApi>;
}) {
  const [recs, setRecs] = useState<RecommendationItem[]>(initialRecs);
  const [generating, setGenerating] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const visible = recs.filter((r) => r.client_facing || isAltera);

  async function handleGenerate() {
    setGenerating(true);
    setActionError(null);
    try {
      const updated = await api.generateRecommendations(projectId, runId);
      setRecs(updated);
    } catch (e: unknown) {
      setActionError(e instanceof Error ? e.message : "Failed to generate recommendations");
    } finally {
      setGenerating(false);
    }
  }

  async function handleTransition(
    id: string,
    action: "propose" | "dismiss" | "archive" | "accept",
  ) {
    setActionError(null);
    try {
      let updated: PersistedRecommendation;
      if (action === "propose") updated = await api.proposeRecommendation(id);
      else if (action === "dismiss") updated = await api.dismissRecommendation(id);
      else if (action === "archive") updated = await api.archiveRecommendation(id);
      else updated = await api.acceptRecommendation(id);
      setRecs((prev) => prev.map((r) => (r.id === id ? { ...r, status: updated.status } : r)));
    } catch (e: unknown) {
      setActionError(e instanceof Error ? e.message : "Action failed");
    }
  }

  return (
    <Card>
      <div className="flex items-start justify-between">
        <CardHeader
          title="Recommendations"
          subtitle="Deterministic, directional signals from this run. No numeric impact estimates."
        />
        {isAltera && (
          <Button
            variant="ghost"
            onClick={handleGenerate}
            disabled={generating}
          >
            {generating ? "Generating…" : "Generate / refresh"}
          </Button>
        )}
      </div>
      {actionError && (
        <p className="mt-2 text-xs text-rose-600">{actionError}</p>
      )}
      {visible.length === 0 ? (
        <p className="mt-3 text-sm text-gray-500">No recommendations generated yet.</p>
      ) : (
        <ul className="mt-4 space-y-4">
          {visible.map((r) => (
            <li key={r.action_type} className="rounded-lg border border-gray-100 bg-gray-50 p-4">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium text-sm text-gray-900">{r.title}</span>
                <Pill tone={PRIORITY_TONE[r.priority] ?? "neutral"}>{r.priority}</Pill>
                <Pill tone={REC_STATUS_TONE[r.status] ?? "neutral"}>{r.status}</Pill>
                {!r.client_facing && isAltera && (
                  <Pill tone="brand">Altera only</Pill>
                )}
              </div>
              <p className="mt-1 text-xs text-gray-500 uppercase tracking-wide">
                {r.action_type.replace(/_/g, " ")}
              </p>
              <p className="mt-2 text-sm text-gray-700">{r.rationale}</p>
              <p className="mt-1 text-xs text-gray-500 italic">{r.expected_direction}</p>
              {r.evidence.length > 0 && (
                <ul className="mt-2 space-y-0.5">
                  {r.evidence.map((e, i) => (
                    <li key={i} className="text-xs text-gray-600">· {e}</li>
                  ))}
                </ul>
              )}
              {r.caveats.length > 0 && (
                <div className="mt-2 rounded border border-amber-100 bg-amber-50 px-2 py-1.5">
                  <p className="text-xs font-medium text-amber-700 mb-1">Caveats</p>
                  <ul className="space-y-0.5">
                    {r.caveats.map((c, i) => (
                      <li key={i} className="text-xs text-amber-700">· {c}</li>
                    ))}
                  </ul>
                </div>
              )}
              {isAltera && r.id && (
                <div className="mt-3 flex flex-wrap gap-2">
                  {r.status === "draft" && (
                    <Button variant="ghost" onClick={() => handleTransition(r.id!, "propose")}>
                      Propose
                    </Button>
                  )}
                  {r.status === "proposed" && (
                    <Button variant="ghost" onClick={() => handleTransition(r.id!, "accept")}>
                      Accept
                    </Button>
                  )}
                  {(r.status === "draft" || r.status === "proposed") && (
                    <Button variant="ghost" onClick={() => handleTransition(r.id!, "dismiss")}>
                      Dismiss
                    </Button>
                  )}
                  {r.status !== "archived" && r.status !== "dismissed" && (
                    <Button variant="ghost" onClick={() => handleTransition(r.id!, "archive")}>
                      Archive
                    </Button>
                  )}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

const UNCERTAINTY_TONE: Record<string, "ok" | "warn" | "error"> = {
  low: "ok",
  medium: "warn",
  high: "error",
};

function CoverageSectionCard({ coverage: c }: { coverage: CoverageSection }) {
  const tone = UNCERTAINTY_TONE[c.uncertainty_level] ?? "neutral";
  return (
    <div className="space-y-4">
      <Card>
        <div className="flex items-center justify-between">
          <CardHeader
            title="Data coverage"
            subtitle="Upload validation and product classification coverage."
          />
          <Pill tone={tone}>{c.uncertainty_level} uncertainty</Pill>
        </div>
        <p className="mt-3 text-sm text-gray-600">{c.uncertainty_rationale}</p>

        {c.uploaded_rows != null && (
          <div className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Stat label="Uploaded rows" value={String(c.uploaded_rows)} />
            <Stat label="Valid rows" value={c.valid_rows != null ? `${c.valid_rows} (${c.valid_row_share_pct ?? "—"}%)` : "—"} />
            <Stat label="Errors" value={String(c.error_count ?? 0)} />
            <Stat label="Warnings" value={String(c.warning_count ?? 0)} />
          </div>
        )}

        <div className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
          <Stat label="Total products" value={String(c.products_total)} />
          <Stat label="Classified" value={c.classified_product_share_pct != null ? `${c.products_classified} (${c.classified_product_share_pct}%)` : String(c.products_classified)} />
          <Stat label="Unknown" value={c.unknown_product_share_pct != null ? `${c.products_unknown} (${c.unknown_product_share_pct}%)` : String(c.products_unknown)} />
          <Stat label="Out of scope" value={String(c.products_out_of_scope)} />
        </div>

        <div className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
          <Stat label="Rule-classified" value={String(c.products_rule_classified)} />
          <Stat label="AI-classified" value={c.ai_classified_share_pct != null ? `${c.products_ai_classified} (${c.ai_classified_share_pct}%)` : String(c.products_ai_classified)} />
          <Stat label="Manual review" value={String(c.products_manual_classified)} />
          <Stat label="Sent to review" value={c.manual_review_share_pct != null ? `${c.products_sent_to_review} (${c.manual_review_share_pct}%)` : String(c.products_sent_to_review)} />
        </div>

        <div className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
          <Stat label="Missing weight" value={c.missing_weight_share_pct != null ? `${c.products_with_missing_weight} (${c.missing_weight_share_pct}%)` : String(c.products_with_missing_weight)} />
          {c.products_with_missing_protein != null && (
            <Stat label="Missing protein %" value={c.missing_protein_share_pct != null ? `${c.products_with_missing_protein} (${c.missing_protein_share_pct}%)` : String(c.products_with_missing_protein)} />
          )}
          <Stat label="Missing category" value={String(c.products_with_missing_category)} />
          {c.products_with_missing_ingredients != null && (
            <Stat label="Missing ingredients" value={String(c.products_with_missing_ingredients)} />
          )}
        </div>

        <div className="mt-4 text-sm text-gray-500">{c.review_completion_note}</div>
      </Card>

      {c.caveats.length > 0 && (
        <Card>
          <CardHeader title="Methodology caveats" />
          <ul className="mt-3 space-y-2">
            {c.caveats.map((caveat, i) => (
              <li key={i} className="flex gap-2 text-sm text-gray-700">
                <span className="mt-0.5 text-gray-400">·</span>
                <span>{caveat}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}

function PTSection({ section: s }: { section: PTReportSection }) {
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader
          title="Protein Tracker results"
          subtitle={`Methodology v${s.methodology_version} · ${s.methodology_source_edition}`}
        />
        <div className="mt-4 grid grid-cols-2 gap-6 sm:grid-cols-4">
          <Stat label="Plant protein (kg)" value={s.plant_protein_kg} />
          <Stat label="Animal protein (kg)" value={s.animal_protein_kg} />
          <Stat label="Plant share %" value={s.plant_share_pct ?? "—"} />
          <Stat label="Animal share %" value={s.animal_share_pct ?? "—"} />
        </div>
        <div className="mt-4 text-xs text-gray-500">{s.composite_note}</div>
      </Card>
      <Card>
        <CardHeader title="Four-group breakdown" />
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
            {s.groups.map((g) => (
              <tr key={g.pt_group}>
                <td className="py-2 font-medium">{g.pt_group.replace(/_/g, " ")}</td>
                <td className="py-2 text-right">{g.item_count}</td>
                <td className="py-2 text-right">{g.volume_kg}</td>
                <td className="py-2 text-right">{g.protein_kg}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      <Card>
        <CardHeader title="Data quality" />
        <div className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-4 text-sm">
          <Stat label="Out of scope" value={String(s.out_of_scope_count)} />
          <Stat label="Unknown" value={String(s.unknown_count)} />
          <Stat label="Source label rows" value={String(s.rows_protein_source_label)} />
          <Stat label="Reference DB rows" value={String(s.rows_protein_source_reference_db)} />
        </div>
      </Card>
    </div>
  );
}

function WWFSection({ section: s }: { section: WWFReportSection }) {
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader
          title="WWF Planet-Based Diets results"
          subtitle={`Methodology v${s.methodology_version} · ${s.methodology_source_edition}`}
        />
        <div className="mt-4 grid grid-cols-2 gap-6 sm:grid-cols-3">
          <Stat label="Total in-scope (kg)" value={s.total_in_scope_weight_kg} />
          <Stat label="Whole-diet plant (kg)" value={s.whole_diet_plant_weight_kg} />
          <Stat label="Whole-diet animal (kg)" value={s.whole_diet_animal_weight_kg} />
        </div>
      </Card>
      <Card>
        <CardHeader title="Food group breakdown (FG1–FG7)" />
        <table className="mt-4 w-full text-left text-sm">
          <thead className="text-xs uppercase tracking-wider text-gray-500">
            <tr>
              <th className="py-2">Food group</th>
              <th className="py-2 text-right">Weight (kg)</th>
              <th className="py-2 text-right">Share %</th>
              <th className="py-2 text-right">PHD reference %</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {s.per_food_group.map((g) => (
              <tr key={g.food_group}>
                <td className="py-2 font-medium">{g.food_group}</td>
                <td className="py-2 text-right">{g.weight_kg}</td>
                <td className="py-2 text-right">{g.share_pct}</td>
                <td className="py-2 text-right">{g.phd_reference_share_pct ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      <Card>
        <CardHeader title="Composites (Step 1)" />
        <div className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-5 text-sm">
          <Stat label="Meat-based (kg)" value={s.composites_meat_based_kg} />
          <Stat label="Seafood-based (kg)" value={s.composites_seafood_based_kg} />
          <Stat label="Vegetarian (kg)" value={s.composites_vegetarian_kg} />
          <Stat label="Vegan (kg)" value={s.composites_vegan_kg} />
          <Stat label="Total (kg)" value={s.composites_total_weight_kg} />
        </div>
      </Card>
      <Card>
        <CardHeader title="Data quality" />
        <div className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-2 text-sm">
          <Stat label="Out of scope" value={String(s.out_of_scope_count)} />
          <Stat label="Unknown" value={String(s.unknown_count)} />
        </div>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Scenarios placeholder (Phase 26A)
// ---------------------------------------------------------------------------

function ScenariosPlaceholderCard({
  projectId,
  runId,
}: {
  projectId: string;
  runId: string;
}) {
  return (
    <Card>
      <CardHeader
        title="Scenario modelling"
        subtitle="What-if projections for Protein Tracker runs. Deterministic, read-only."
      />
      <div className="mt-4 rounded-lg border border-blue-100 bg-blue-50 px-4 py-3 text-sm text-blue-800">
        <p className="font-medium">Phase 26A — available via API</p>
        <p className="mt-1 text-xs text-blue-700">
          Scenario modelling is available through the REST API. Use
          <code className="mx-1 rounded bg-blue-100 px-1 font-mono text-xs">
            POST /api/v1/projects/{projectId}/scenarios
          </code>
          to create a scenario against run
          <code className="ml-1 rounded bg-blue-100 px-1 font-mono text-xs">{runId}</code>,
          add operations, then call
          <code className="mx-1 rounded bg-blue-100 px-1 font-mono text-xs">
            POST /api/v1/scenarios/:id/run
          </code>
          to compute the projection.
        </p>
        <p className="mt-2 text-xs text-blue-600">
          Supported operations: shift protein between groups, increase plant core,
          reduce animal core, improve composite split. WWF scenarios are not yet implemented.
          A full UI for scenario authoring is planned for a future phase.
        </p>
      </div>
    </Card>
  );
}

