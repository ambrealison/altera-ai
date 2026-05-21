"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { Button, Card, CardHeader, EmptyState, Pill, Stat } from "@/components/ui";
import { useAuth } from "@/lib/auth-context";
import { createApi, type ApplyReferencesSummary, type ClassifySummary, type Methodology, type Project, type Run, type UploadResult } from "@/lib/api";

export default function ProjectDetail() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const { accessToken, loading: authLoading, isAltera } = useAuth();
  const api = useMemo(() => createApi(accessToken), [accessToken]);

  const [project, setProject] = useState<Project | null>(null);
  const [uploads, setUploads] = useState<UploadResult[] | null>(null);
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [classifyBusy, setClassifyBusy] = useState(false);
  const [classifyResult, setClassifyResult] = useState<ClassifySummary | null>(null);
  const [classifyError, setClassifyError] = useState<string | null>(null);
  const [deleteBusy, setDeleteBusy] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [enrichBusy, setEnrichBusy] = useState(false);
  const [enrichResult, setEnrichResult] = useState<ApplyReferencesSummary | null>(null);
  const [enrichError, setEnrichError] = useState<string | null>(null);

  useEffect(() => {
    if (authLoading || !id) return;
    let active = true;
    Promise.all([api.getProject(id), api.listUploads(id), api.listRuns(id)])
      .then(([p, u, r]) => {
        if (!active) return;
        setProject(p);
        setUploads(u.items);
        setRuns(r.items);
      })
      .catch((e: Error) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [api, authLoading, id]);

  async function onDeleteUpload(uploadId: string, filename: string) {
    if (!confirm(`Delete upload "${filename}" and all its products?\nThis also removes related classifications and review items.`)) {
      return;
    }
    setDeleteBusy(uploadId);
    setDeleteError(null);
    try {
      await api.deleteUpload(id, uploadId);
      // Refresh project + uploads + runs.
      const [p, u, r] = await Promise.all([
        api.getProject(id),
        api.listUploads(id),
        api.listRuns(id),
      ]);
      setProject(p);
      setUploads(u.items);
      setRuns(r.items);
      // Clear any stale classify summary so the card updates correctly.
      setClassifyResult(null);
    } catch (e) {
      setDeleteError(e instanceof Error ? e.message : "Delete failed");
    } finally {
      setDeleteBusy(null);
    }
  }

  async function onClassifyAll(methodology: Methodology) {
    if (!uploads) return;
    const targets = uploads.filter((u) => u.products_count > 0);
    if (targets.length === 0) return;
    setClassifyBusy(true);
    setClassifyError(null);
    setClassifyResult(null);
    try {
      // Classify every upload that has ingested products. The deadlock fix
      // (Phase 33E): one upload can leave others unclassified, so we iterate.
      const summaries = await Promise.all(
        targets.map((u) => api.classify(id, u.id, methodology)),
      );
      const merged: ClassifySummary = {
        methodology,
        matched: summaries.reduce((s, x) => s + x.matched, 0),
        pass_through: summaries.reduce((s, x) => s + x.pass_through, 0),
        rule_collision: summaries.reduce((s, x) => s + x.rule_collision, 0),
        queued_for_review: summaries.reduce((s, x) => s + x.queued_for_review, 0),
      };
      setClassifyResult(merged);
      const updated = await api.getProject(id);
      setProject(updated);
    } catch (e) {
      setClassifyError(e instanceof Error ? e.message : "Classification failed");
    } finally {
      setClassifyBusy(false);
    }
  }

  async function onApplyEnrichment() {
    setEnrichBusy(true);
    setEnrichError(null);
    setEnrichResult(null);
    try {
      const summary = await api.applyNutritionReferences(id);
      setEnrichResult(summary);
    } catch (e) {
      setEnrichError(e instanceof Error ? e.message : "Enrichment failed");
    } finally {
      setEnrichBusy(false);
    }
  }

  if (error)
    return (
      <div className="mx-auto max-w-3xl">
        <div className="rounded-md border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
          {error}
        </div>
        <Link href="/projects" className="mt-4 inline-block text-sm text-brand-700 hover:underline">
          ← All projects
        </Link>
      </div>
    );
  if (!project || uploads === null || runs === null) {
    return <div className="text-sm text-gray-500">Loading…</div>;
  }

  return (
    <div className="mx-auto max-w-5xl">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{project.name}</h1>
          <div className="mt-1 flex items-center gap-2">
            {project.methodologies_enabled.map((m) => (
              <Pill key={m} tone="brand">{m}</Pill>
            ))}
            <span className="text-sm text-gray-500">
              {project.reporting_period_label}
            </span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Link href={`/projects/${id}/workflow`}>
            <Button variant="primary">Parcours guidé →</Button>
          </Link>
          <Link href="/projects">
            <Button variant="ghost">← All projects</Button>
          </Link>
        </div>
      </div>

      <section className="mt-8 grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Card><Stat label="Uploads" value={uploads.length} /></Card>
        <Card><Stat label="Unclassified" value={project.unclassified_pt_count} /></Card>
        <Card><Stat label="In review" value={project.review_queue_count} /></Card>
        <Card><Stat label="Runs" value={runs.length} /></Card>
      </section>

      <section className="mt-10">
        <Card>
          <CardHeader
            title="Uploads"
            subtitle="Ingestion runs the CSV through header normalisation, commercial-column drop, unit conversion, and methodology-aware validation."
            action={
              <Link href={`/projects/${id}/upload`}>
                <Button variant="primary">+ Upload CSV</Button>
              </Link>
            }
          />
          {deleteError && (
            <div className="mt-3 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">
              {deleteError}
            </div>
          )}
          {uploads.length === 0 ? (
            <div className="mt-4">
              <EmptyState title="No uploads yet" description="Upload a CSV to start the pipeline." />
            </div>
          ) : (
            <ul className="mt-4 divide-y divide-gray-100">
              {uploads.map((u) => (
                <li key={u.id} className="flex items-center justify-between py-3 text-sm">
                  <div>
                    <span className="font-medium">{u.original_filename}</span>
                    <span className="ml-2 text-gray-500">
                      {u.row_count ?? "—"} rows · {u.products_count} products
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    <Pill tone={u.status === "valid" ? "ok" : "warn"}>{u.status}</Pill>
                    <button
                      type="button"
                      onClick={() => onDeleteUpload(u.id, u.original_filename)}
                      disabled={deleteBusy !== null}
                      className="text-xs text-rose-600 hover:text-rose-800 hover:underline disabled:opacity-50"
                      aria-label={`Delete upload ${u.original_filename}`}
                    >
                      {deleteBusy === u.id ? "Deleting…" : "Delete"}
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </section>

      {/* Classification workflow step — shown when products need classifying */}
      {project.unclassified_pt_count > 0 && uploads.length > 0 && (() => {
        const classifiableUploads = uploads.filter((u) => u.products_count > 0);
        if (classifiableUploads.length === 0) return null;
        const ptEnabled = project.methodologies_enabled.includes("protein_tracker");
        return ptEnabled ? (
          <section className="mt-6">
            <Card>
              <CardHeader
                title="Classify products"
                subtitle={`${project.unclassified_pt_count} product${project.unclassified_pt_count === 1 ? "" : "s"} need classification before a calculation can run.`}
              />
              <p className="mt-2 text-xs text-gray-500">
                The rules engine will classify products from all {classifiableUploads.length}{" "}
                upload{classifiableUploads.length === 1 ? "" : "s"} that have ingested data.
                Ambiguous products are queued for manual review.
              </p>
              {classifyError && (
                <div className="mt-3 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">
                  {classifyError}
                </div>
              )}
              {classifyResult ? (
                <div className="mt-4 grid grid-cols-2 gap-4 text-sm sm:grid-cols-4">
                  <div>
                    <div className="text-xs uppercase tracking-wider text-gray-500">Matched</div>
                    <div className="mt-1 text-lg font-semibold">{classifyResult.matched}</div>
                  </div>
                  <div>
                    <div className="text-xs uppercase tracking-wider text-gray-500">Pass-through</div>
                    <div className="mt-1 text-lg font-semibold">{classifyResult.pass_through}</div>
                  </div>
                  <div>
                    <div className="text-xs uppercase tracking-wider text-gray-500">Collisions</div>
                    <div className="mt-1 text-lg font-semibold">{classifyResult.rule_collision}</div>
                  </div>
                  <div>
                    <div className="text-xs uppercase tracking-wider text-gray-500">Sent to review</div>
                    <div className="mt-1 text-lg font-semibold">{classifyResult.queued_for_review}</div>
                  </div>
                </div>
              ) : (
                <div className="mt-4">
                  <Button
                    onClick={() => onClassifyAll("protein_tracker")}
                    disabled={classifyBusy}
                  >
                    {classifyBusy ? "Classifying…" : "Classify as Protein Tracker"}
                  </Button>
                </div>
              )}
            </Card>
          </section>
        ) : null;
      })()}

      {/* Phase 33H/33I-AI — Altera-only nutrition enrichment CTA. */}
      {isAltera && project.methodologies_enabled.includes("protein_tracker") && (
        <section className="mt-6">
          <Card>
            <CardHeader
              title="Apply nutrition enrichment"
              subtitle="Fill missing protein values using reference tables. NEVO (RIVM 2025 v9.0) is tried first and supplies plant/animal split when available; CIQUAL (Anses 2025) is the total-only fallback. Retailer-provided values are never overwritten."
            />
            <p className="mt-2 text-xs text-gray-500">
              AI may assist matching product names to reference databases. Nutrition
              values come only from retailer data, NEVO, CIQUAL, or manual review —
              AI does not generate nutrition values.
            </p>
            {enrichError && (
              <div className="mt-3 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">
                {enrichError}
              </div>
            )}
            {enrichResult ? (
              <>
                <div className="mt-4 flex items-center gap-2">
                  <span className="text-xs uppercase tracking-wider text-gray-500">
                    AI matching
                  </span>
                  {enrichResult.ai_enabled ? (
                    <Pill tone="ok">
                      enabled{enrichResult.ai_model ? ` · ${enrichResult.ai_model}` : ""}
                    </Pill>
                  ) : (
                    <Pill tone="neutral">disabled</Pill>
                  )}
                </div>
                <div className="mt-3 grid grid-cols-2 gap-4 text-sm sm:grid-cols-4">
                  <div>
                    <div className="text-xs uppercase tracking-wider text-gray-500">
                      NEVO — deterministic
                    </div>
                    <div className="mt-1 text-lg font-semibold">{enrichResult.nevo_matched}</div>
                    <div className="text-xs text-gray-500">
                      {enrichResult.nevo_with_split} with plant/animal split
                    </div>
                  </div>
                  <div>
                    <div className="text-xs uppercase tracking-wider text-gray-500">
                      NEVO — AI-assisted
                    </div>
                    <div className="mt-1 text-lg font-semibold">
                      {enrichResult.ai_enabled ? enrichResult.nevo_ai_assisted_matched : "—"}
                    </div>
                    <div className="text-xs text-gray-500">
                      {enrichResult.ai_enabled
                        ? `${enrichResult.nevo_ai_assisted_with_split} with split`
                        : "AI matching disabled"}
                    </div>
                  </div>
                  <div>
                    <div className="text-xs uppercase tracking-wider text-gray-500">
                      CIQUAL — deterministic
                    </div>
                    <div className="mt-1 text-lg font-semibold">{enrichResult.ciqual_matched}</div>
                    <div className="text-xs text-gray-500">total only</div>
                  </div>
                  <div>
                    <div className="text-xs uppercase tracking-wider text-gray-500">
                      CIQUAL — AI-assisted
                    </div>
                    <div className="mt-1 text-lg font-semibold">
                      {enrichResult.ai_enabled ? enrichResult.ciqual_ai_assisted_matched : "—"}
                    </div>
                    <div className="text-xs text-gray-500">
                      {enrichResult.ai_enabled ? "total only" : "AI matching disabled"}
                    </div>
                  </div>
                  <div>
                    <div className="text-xs uppercase tracking-wider text-gray-500">
                      Needs review
                    </div>
                    <div className="mt-1 text-lg font-semibold">
                      {enrichResult.ai_enabled ? enrichResult.ai_needs_review : "—"}
                    </div>
                    <div className="text-xs text-gray-500">
                      {enrichResult.ai_enabled ? "medium-confidence AI" : "—"}
                    </div>
                  </div>
                  <div>
                    <div className="text-xs uppercase tracking-wider text-gray-500">No match</div>
                    <div className="mt-1 text-lg font-semibold">{enrichResult.no_match}</div>
                    <div className="text-xs text-gray-500">needs manual</div>
                  </div>
                  <div>
                    <div className="text-xs uppercase tracking-wider text-gray-500">
                      Retailer values kept
                    </div>
                    <div className="mt-1 text-lg font-semibold">
                      {enrichResult.skipped_has_retailer_value}
                    </div>
                  </div>
                  <div>
                    <div className="text-xs uppercase tracking-wider text-gray-500">
                      Skipped (non-PT)
                    </div>
                    <div className="mt-1 text-lg font-semibold">
                      {enrichResult.skipped_no_pt_fields}
                    </div>
                  </div>
                </div>
              </>
            ) : (
              <p className="mt-2 text-xs text-gray-500">
                One-click match against the reference tables. Only products with no
                retailer-provided protein_pct are touched. Run the calculation with
                &ldquo;Use enriched nutrition&rdquo; to consume the resulting records.
              </p>
            )}
            <div className="mt-4">
              <Button onClick={onApplyEnrichment} disabled={enrichBusy} variant="secondary">
                {enrichBusy
                  ? "Matching…"
                  : enrichResult
                    ? "Re-run enrichment"
                    : "Apply NEVO + CIQUAL"}
              </Button>
            </div>
          </Card>
        </section>
      )}

      <section className="mt-6">
        <Card>
          <CardHeader
            title="Review queue"
            subtitle={
              isAltera
                ? "Products the rules engine could not classify confidently."
                : "Ambiguous products are reviewed by the Altera methodology team."
            }
            action={
              isAltera ? (
                <Link href={`/projects/${id}/review`}>
                  <Button variant="secondary">Open queue</Button>
                </Link>
              ) : undefined
            }
          />
          <div className="mt-3 text-sm text-gray-600">
            {isAltera ? (
              project.review_queue_count > 0
                ? `${project.review_queue_count} item${project.review_queue_count === 1 ? "" : "s"} need a decision.`
                : "Nothing in the queue right now."
            ) : (
              project.review_queue_count > 0
                ? `${project.review_queue_count} item${project.review_queue_count === 1 ? "" : "s"} in review by Altera.`
                : "No items currently in review."
            )}
          </div>
        </Card>
      </section>

      <section className="mt-6">
        <Card>
          <CardHeader
            title="Runs"
            subtitle="Each run produces a per-row breakdown and a methodology summary."
            action={
              <Link href={`/projects/${id}/runs`}>
                <Button variant="primary">Calculate / view runs</Button>
              </Link>
            }
          />
          {runs.length === 0 ? (
            <div className="mt-4">
              <EmptyState title="No runs yet" description="Trigger a run once your data is classified." />
            </div>
          ) : (
            <ul className="mt-4 divide-y divide-gray-100">
              {runs.map((r) => (
                <li key={r.id} className="flex items-center justify-between py-3 text-sm">
                  <div>
                    <Link href={`/projects/${id}/runs/${r.id}`} className="font-medium text-brand-700 hover:underline">
                      {r.methodology} · {r.id.slice(0, 8)}
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
      </section>
    </div>
  );
}
