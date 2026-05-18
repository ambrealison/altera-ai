"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { Button, Card, CardHeader, Pill, Stat } from "@/components/ui";
import { useAuth } from "@/lib/auth-context";
import {
  createApi,
  type PTReportSection,
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

  const { meta, executive_summary, pt_section, wwf_section, review_summary } = report;
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
