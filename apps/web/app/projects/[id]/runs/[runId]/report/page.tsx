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
  type ScenarioOperationResponse,
  type ScenarioOperationType,
  type ScenarioResponse,
  type ScenarioResultResponse,
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
  const [scenarioPrefill, setScenarioPrefill] = useState<ScenarioPrefill | null>(null);

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
        onCreateScenario={isAltera && meta.methodology === "protein_tracker"
          ? (prefill) => setScenarioPrefill(prefill)
          : undefined}
      />

      {/* Data coverage and uncertainty */}
      <CoverageSectionCard coverage={coverage} />

      {/* Scenarios — Altera only, PT only */}
      {isAltera && meta.methodology === "protein_tracker" && (
        <ScenariosCard
          projectId={id}
          runId={runId}
          api={api}
          prefill={scenarioPrefill}
          onPrefillConsumed={() => setScenarioPrefill(null)}
        />
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

// Recommendation action types that can seed a scenario
const REC_TO_OP: Record<string, ScenarioOperationType> = {
  increase_plant_core_share: "increase_plant_core_protein",
  reduce_animal_core_dependency: "reduce_animal_core_protein",
  improve_composite_breakdown: "improve_composite_split",
};

interface ScenarioPrefill {
  name: string;
  description: string;
  opType: ScenarioOperationType;
}

function RecommendationsCard({
  recommendations: initialRecs,
  isAltera,
  projectId,
  runId,
  api,
  onCreateScenario,
}: {
  recommendations: RecommendationItem[];
  isAltera: boolean;
  projectId: string;
  runId: string;
  api: ReturnType<typeof createApi>;
  onCreateScenario?: (prefill: ScenarioPrefill) => void;
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
                  {onCreateScenario && REC_TO_OP[r.action_type] && (
                    <Button
                      variant="ghost"
                      onClick={() =>
                        onCreateScenario({
                          name: r.title,
                          description: r.rationale,
                          opType: REC_TO_OP[r.action_type],
                        })
                      }
                    >
                      Simulate ↓
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
// Scenarios card (Phase 26B)
// ---------------------------------------------------------------------------

const PT_GROUPS = [
  "plant_based_core",
  "plant_based_non_core",
  "composite_products",
  "animal_core",
] as const;

const OP_LABELS: Record<ScenarioOperationType, string> = {
  shift_protein_between_groups: "Shift protein between groups",
  increase_plant_core_protein: "Increase plant core protein",
  reduce_animal_core_protein: "Reduce animal core protein",
  improve_composite_split: "Improve composite split",
};

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
  return n > 0 ? "text-green-600" : "text-rose-600";
}

type OpFormState = {
  operation_type: ScenarioOperationType;
  // shift_protein_between_groups
  from_group: string;
  to_group: string;
  // amount_kg used by shift, increase, reduce
  amount_kg: string;
  // improve_composite_split
  plant_pct: string;
  rationale: string;
};

function defaultOpForm(opType?: ScenarioOperationType): OpFormState {
  return {
    operation_type: opType ?? "increase_plant_core_protein",
    from_group: "animal_core",
    to_group: "plant_based_core",
    amount_kg: "",
    plant_pct: "60",
    rationale: "",
  };
}

function opToRequest(form: OpFormState): { operation_type: ScenarioOperationType; parameters: Record<string, string | number>; rationale: string } {
  const params: Record<string, string | number> = {};
  if (form.operation_type === "shift_protein_between_groups") {
    params.from_group = form.from_group;
    params.to_group = form.to_group;
    params.amount_kg = form.amount_kg;
  } else if (form.operation_type === "increase_plant_core_protein") {
    params.amount_kg = form.amount_kg;
  } else if (form.operation_type === "reduce_animal_core_protein") {
    params.amount_kg = form.amount_kg;
  } else if (form.operation_type === "improve_composite_split") {
    const plant = parseFloat(form.plant_pct);
    params.plant_pct = form.plant_pct;
    params.animal_pct = String(isNaN(plant) ? 40 : 100 - plant);
  }
  return { operation_type: form.operation_type, parameters: params, rationale: form.rationale };
}

function OpParamFields({
  form,
  onChange,
}: {
  form: OpFormState;
  onChange: (patch: Partial<OpFormState>) => void;
}) {
  if (form.operation_type === "shift_protein_between_groups") {
    return (
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <label className="flex flex-col gap-1 text-xs text-gray-600">
          From group
          <select
            className="rounded border border-gray-200 px-2 py-1.5 text-sm"
            value={form.from_group}
            onChange={(e) => onChange({ from_group: e.target.value })}
          >
            {PT_GROUPS.map((g) => (
              <option key={g} value={g}>{g.replace(/_/g, " ")}</option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs text-gray-600">
          To group
          <select
            className="rounded border border-gray-200 px-2 py-1.5 text-sm"
            value={form.to_group}
            onChange={(e) => onChange({ to_group: e.target.value })}
          >
            {PT_GROUPS.map((g) => (
              <option key={g} value={g}>{g.replace(/_/g, " ")}</option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs text-gray-600">
          Amount (kg)
          <input
            type="number"
            min={0}
            step="any"
            placeholder="e.g. 500"
            className="rounded border border-gray-200 px-2 py-1.5 text-sm"
            value={form.amount_kg}
            onChange={(e) => onChange({ amount_kg: e.target.value })}
          />
        </label>
      </div>
    );
  }
  if (form.operation_type === "increase_plant_core_protein" || form.operation_type === "reduce_animal_core_protein") {
    const label = form.operation_type === "increase_plant_core_protein"
      ? "Protein to add (kg)"
      : "Protein to reduce (kg)";
    return (
      <label className="flex flex-col gap-1 text-xs text-gray-600">
        {label}
        <input
          type="number"
          min={0}
          step="any"
          placeholder="e.g. 1000"
          className="w-48 rounded border border-gray-200 px-2 py-1.5 text-sm"
          value={form.amount_kg}
          onChange={(e) => onChange({ amount_kg: e.target.value })}
        />
      </label>
    );
  }
  if (form.operation_type === "improve_composite_split") {
    const plant = parseFloat(form.plant_pct);
    const animal = isNaN(plant) ? "—" : String(100 - plant);
    return (
      <div className="flex items-end gap-4">
        <label className="flex flex-col gap-1 text-xs text-gray-600">
          Plant % (of composite protein)
          <input
            type="number"
            min={0}
            max={100}
            step="any"
            placeholder="e.g. 60"
            className="w-32 rounded border border-gray-200 px-2 py-1.5 text-sm"
            value={form.plant_pct}
            onChange={(e) => onChange({ plant_pct: e.target.value })}
          />
        </label>
        <div className="pb-2 text-xs text-gray-500">Animal: {animal}%</div>
      </div>
    );
  }
  return null;
}

function ScenarioResultTable({ result }: { result: ScenarioResultResponse }) {
  const p = result.pt_projected;
  if (!p) return null;
  return (
    <div className="mt-4 space-y-3">
      <div className="text-xs font-medium uppercase tracking-wide text-gray-500">Projection result</div>
      <table className="w-full text-left text-sm">
        <thead className="text-xs uppercase tracking-wider text-gray-400">
          <tr>
            <th className="py-1.5">Metric</th>
            <th className="py-1.5 text-right">Base</th>
            <th className="py-1.5 text-right">Projected</th>
            <th className="py-1.5 text-right">Δ</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          <tr>
            <td className="py-1.5 text-gray-700">Plant protein (kg)</td>
            <td className="py-1.5 text-right font-mono">{parseFloat(p.base_plant_protein_kg).toFixed(2)}</td>
            <td className="py-1.5 text-right font-mono">{parseFloat(p.projected_plant_protein_kg).toFixed(2)}</td>
            <td className={`py-1.5 text-right font-mono ${deltaClass(p.delta_plant_protein_kg)}`}>{fmtDelta(p.delta_plant_protein_kg)}</td>
          </tr>
          <tr>
            <td className="py-1.5 text-gray-700">Animal protein (kg)</td>
            <td className="py-1.5 text-right font-mono">{parseFloat(p.base_animal_protein_kg).toFixed(2)}</td>
            <td className="py-1.5 text-right font-mono">{parseFloat(p.projected_animal_protein_kg).toFixed(2)}</td>
            <td className={`py-1.5 text-right font-mono ${deltaClass(p.delta_animal_protein_kg)}`}>{fmtDelta(p.delta_animal_protein_kg)}</td>
          </tr>
          {p.base_plant_share_pct != null && (
            <tr>
              <td className="py-1.5 text-gray-700">Plant share (%)</td>
              <td className="py-1.5 text-right font-mono">{parseFloat(p.base_plant_share_pct).toFixed(1)}</td>
              <td className="py-1.5 text-right font-mono">{p.projected_plant_share_pct != null ? parseFloat(p.projected_plant_share_pct).toFixed(1) : "—"}</td>
              <td className={`py-1.5 text-right font-mono ${deltaClass(p.delta_plant_share_pct)}`}>{fmtDelta(p.delta_plant_share_pct)}</td>
            </tr>
          )}
          {p.projected_animal_share_pct != null && (
            <tr>
              <td className="py-1.5 text-gray-700">Animal share (%)</td>
              <td className="py-1.5 text-right font-mono">{p.base_plant_share_pct != null ? (100 - parseFloat(p.base_plant_share_pct)).toFixed(1) : "—"}</td>
              <td className="py-1.5 text-right font-mono">{parseFloat(p.projected_animal_share_pct).toFixed(1)}</td>
              <td className={`py-1.5 text-right font-mono ${deltaClass(p.delta_plant_share_pct ? String(-parseFloat(p.delta_plant_share_pct)) : null)}`}>
                {p.delta_plant_share_pct != null ? fmtDelta(String(-parseFloat(p.delta_plant_share_pct))) : "—"}
              </td>
            </tr>
          )}
        </tbody>
      </table>
      {result.warnings.length > 0 && (
        <div className="rounded border border-amber-100 bg-amber-50 px-3 py-2">
          <p className="text-xs font-medium text-amber-700 mb-1">Projection warnings</p>
          <ul className="space-y-0.5">
            {result.warnings.map((w, i) => (
              <li key={i} className="text-xs text-amber-700">· {w}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function ScenarioItem({
  scenario,
  api,
  onUpdate,
}: {
  scenario: ScenarioResponse;
  api: ReturnType<typeof createApi>;
  onUpdate: (updated: ScenarioResponse) => void;
}) {
  const [ops, setOps] = useState<ScenarioOperationResponse[] | null>(null);
  const [result, setResult] = useState<ScenarioResultResponse | null>(null);
  const [showOpForm, setShowOpForm] = useState(false);
  const [opForm, setOpForm] = useState<OpFormState>(defaultOpForm());
  const [addingOp, setAddingOp] = useState(false);
  const [running, setRunning] = useState(false);
  const [opError, setOpError] = useState<string | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(true);

  useEffect(() => {
    api.listScenarioOperations(scenario.id).then(setOps).catch(() => setOps([]));
    api.getScenarioResult(scenario.id)
      .then(setResult)
      .catch(() => setResult(null));
  }, [api, scenario.id]);

  async function handleAddOp() {
    setAddingOp(true);
    setOpError(null);
    try {
      const req = opToRequest(opForm);
      const newOp = await api.addScenarioOperation(scenario.id, req);
      setOps((prev) => [...(prev ?? []), newOp]);
      setShowOpForm(false);
      setOpForm(defaultOpForm());
    } catch (e: unknown) {
      setOpError(e instanceof Error ? e.message : "Failed to add operation");
    } finally {
      setAddingOp(false);
    }
  }

  async function handleRun() {
    setRunning(true);
    setRunError(null);
    try {
      const res = await api.runScenario(scenario.id);
      setResult(res);
      if (scenario.status === "draft") {
        onUpdate({ ...scenario, status: "active" });
      }
    } catch (e: unknown) {
      setRunError(e instanceof Error ? e.message : "Failed to run scenario");
    } finally {
      setRunning(false);
    }
  }

  const statusTone: "neutral" | "ok" | "warn" =
    scenario.status === "active" ? "ok" : scenario.status === "archived" ? "neutral" : "warn";

  return (
    <div className="rounded-lg border border-gray-100 bg-gray-50 p-4">
      <div className="flex items-start justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <button
            className="text-sm font-medium text-gray-900 hover:underline"
            onClick={() => setExpanded((v) => !v)}
          >
            {scenario.name}
          </button>
          <Pill tone={statusTone}>{scenario.status}</Pill>
          <span className="text-xs text-gray-400">{ops?.length ?? scenario.operation_count} op{(ops?.length ?? scenario.operation_count) !== 1 ? "s" : ""}</span>
        </div>
        <Button
          variant="ghost"
          onClick={handleRun}
          disabled={running || !ops || ops.length === 0}
        >
          {running ? "Running…" : "Run"}
        </Button>
      </div>
      {scenario.description && (
        <p className="mt-1 text-xs text-gray-500">{scenario.description}</p>
      )}
      {runError && <p className="mt-2 text-xs text-rose-600">{runError}</p>}

      {expanded && (
        <div className="mt-3 space-y-3">
          {/* Operations list */}
          {ops && ops.length > 0 && (
            <div className="space-y-1.5">
              {ops.map((op, i) => (
                <div key={op.id} className="flex items-start gap-2 text-xs text-gray-600">
                  <span className="text-gray-300">{i + 1}.</span>
                  <div>
                    <span className="font-medium">{OP_LABELS[op.operation_type as ScenarioOperationType]}</span>
                    <span className="ml-2 font-mono text-gray-400">
                      {Object.entries(op.parameters)
                        .map(([k, v]) => `${k}=${v}`)
                        .join(", ")}
                    </span>
                    {op.rationale && <span className="ml-2 italic text-gray-400">— {op.rationale}</span>}
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Add operation form */}
          {showOpForm ? (
            <div className="rounded border border-gray-200 bg-white p-3 space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-xs font-medium text-gray-700">Add operation</span>
                <button className="text-xs text-gray-400 hover:text-gray-600" onClick={() => setShowOpForm(false)}>✕</button>
              </div>
              <label className="flex flex-col gap-1 text-xs text-gray-600">
                Type
                <select
                  className="rounded border border-gray-200 px-2 py-1.5 text-sm"
                  value={opForm.operation_type}
                  onChange={(e) => setOpForm({ ...defaultOpForm(e.target.value as ScenarioOperationType), rationale: opForm.rationale })}
                >
                  {(Object.keys(OP_LABELS) as ScenarioOperationType[]).map((k) => (
                    <option key={k} value={k}>{OP_LABELS[k]}</option>
                  ))}
                </select>
              </label>
              <OpParamFields form={opForm} onChange={(patch) => setOpForm((f) => ({ ...f, ...patch }))} />
              <label className="flex flex-col gap-1 text-xs text-gray-600">
                Rationale (optional)
                <input
                  type="text"
                  placeholder="Why this operation?"
                  className="rounded border border-gray-200 px-2 py-1.5 text-sm"
                  value={opForm.rationale}
                  onChange={(e) => setOpForm((f) => ({ ...f, rationale: e.target.value }))}
                />
              </label>
              {opError && <p className="text-xs text-rose-600">{opError}</p>}
              <div className="flex gap-2">
                <Button variant="ghost" onClick={handleAddOp} disabled={addingOp}>
                  {addingOp ? "Adding…" : "Add"}
                </Button>
                <Button variant="ghost" onClick={() => setShowOpForm(false)}>Cancel</Button>
              </div>
            </div>
          ) : (
            <button
              className="text-xs text-blue-600 hover:underline"
              onClick={() => setShowOpForm(true)}
            >
              + Add operation
            </button>
          )}

          {/* Result table */}
          {result && <ScenarioResultTable result={result} />}
        </div>
      )}
    </div>
  );
}

function ScenariosCard({
  projectId,
  runId,
  api,
  prefill,
  onPrefillConsumed,
}: {
  projectId: string;
  runId: string;
  api: ReturnType<typeof createApi>;
  prefill: ScenarioPrefill | null;
  onPrefillConsumed: () => void;
}) {
  const [scenarios, setScenarios] = useState<ScenarioResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [createName, setCreateName] = useState("");
  const [createDesc, setCreateDesc] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  // Suggested op type from prefill — stored for when create form opens
  const [pendingOpType, setPendingOpType] = useState<ScenarioOperationType | null>(null);

  useEffect(() => {
    api.listScenarios(projectId)
      .then((list) => setScenarios(list.items))
      .catch(() => setScenarios([]))
      .finally(() => setLoading(false));
  }, [api, projectId]);

  // When prefill arrives, open create form with pre-populated values
  useEffect(() => {
    if (!prefill) return;
    setCreateName(prefill.name);
    setCreateDesc(prefill.description);
    setPendingOpType(prefill.opType);
    setShowCreate(true);
    onPrefillConsumed();
    // Scroll to scenarios card
    document.getElementById("scenarios-card")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [prefill, onPrefillConsumed]);

  async function handleCreate() {
    if (!createName.trim()) return;
    setCreating(true);
    setCreateError(null);
    try {
      const created = await api.createScenario(projectId, {
        name: createName.trim(),
        description: createDesc.trim(),
        base_run_id: runId,
      });
      setScenarios((prev) => [created, ...prev]);
      setShowCreate(false);
      setCreateName("");
      setCreateDesc("");
      setPendingOpType(null);
    } catch (e: unknown) {
      setCreateError(e instanceof Error ? e.message : "Failed to create scenario");
    } finally {
      setCreating(false);
    }
  }

  return (
    <div id="scenarios-card">
      <Card>
        <div className="flex items-start justify-between">
          <CardHeader
            title="Scenario modelling"
            subtitle="What-if projections against this PT run. Deterministic — actual results are never changed."
          />
          {!showCreate && (
            <Button variant="ghost" onClick={() => setShowCreate(true)}>
              + New scenario
            </Button>
          )}
        </div>

        {/* Create form */}
        {showCreate && (
          <div className="mt-4 rounded-lg border border-blue-100 bg-blue-50 p-4 space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium text-blue-900">New scenario</span>
              <button className="text-xs text-blue-400 hover:text-blue-600" onClick={() => { setShowCreate(false); setPendingOpType(null); }}>✕</button>
            </div>
            <label className="flex flex-col gap-1 text-xs text-gray-600">
              Name
              <input
                type="text"
                placeholder="e.g. Increase plant core by 10%"
                className="rounded border border-gray-200 px-2 py-1.5 text-sm bg-white"
                value={createName}
                onChange={(e) => setCreateName(e.target.value)}
                autoFocus
              />
            </label>
            <label className="flex flex-col gap-1 text-xs text-gray-600">
              Description (optional)
              <input
                type="text"
                placeholder="Why this scenario?"
                className="rounded border border-gray-200 px-2 py-1.5 text-sm bg-white"
                value={createDesc}
                onChange={(e) => setCreateDesc(e.target.value)}
              />
            </label>
            {pendingOpType && (
              <p className="text-xs text-blue-700">
                Suggested first operation: <span className="font-medium">{OP_LABELS[pendingOpType]}</span>. Add it after creating the scenario.
              </p>
            )}
            {createError && <p className="text-xs text-rose-600">{createError}</p>}
            <div className="flex gap-2">
              <Button variant="ghost" onClick={handleCreate} disabled={creating || !createName.trim()}>
                {creating ? "Creating…" : "Create"}
              </Button>
              <Button variant="ghost" onClick={() => { setShowCreate(false); setPendingOpType(null); }}>Cancel</Button>
            </div>
          </div>
        )}

        {/* Scenario list */}
        {loading ? (
          <p className="mt-4 text-sm text-gray-400">Loading scenarios…</p>
        ) : scenarios.length === 0 && !showCreate ? (
          <p className="mt-4 text-sm text-gray-400">
            No scenarios yet. Create one to model what-if changes to this run.
          </p>
        ) : (
          <div className="mt-4 space-y-3">
            {scenarios.map((s) => (
              <ScenarioItem
                key={s.id}
                scenario={s}
                api={api}
                onUpdate={(updated) =>
                  setScenarios((prev) => prev.map((x) => (x.id === updated.id ? updated : x)))
                }
              />
            ))}
          </div>
        )}
        <p className="mt-4 text-xs text-gray-400">
          PT only · Projections do not affect actual run results · WWF scenarios not yet available
        </p>
      </Card>
    </div>
  );
}

