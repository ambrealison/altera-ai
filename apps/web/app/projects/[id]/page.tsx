"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { Button, Card, CardHeader, EmptyState, Pill, Stat } from "@/components/ui";
import { useAuth } from "@/lib/auth-context";
import { createApi, type ClassifySummary, type Methodology, type Project, type Run, type UploadResult } from "@/lib/api";

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

  async function onClassify(uploadId: string, methodology: Methodology) {
    setClassifyBusy(true);
    setClassifyError(null);
    setClassifyResult(null);
    try {
      const summary = await api.classify(id, uploadId, methodology);
      setClassifyResult(summary);
      // Reload project so unclassified_pt_count updates
      const updated = await api.getProject(id);
      setProject(updated);
    } catch (e) {
      setClassifyError(e instanceof Error ? e.message : "Classification failed");
    } finally {
      setClassifyBusy(false);
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
        <Link href="/projects">
          <Button variant="ghost">← All projects</Button>
        </Link>
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
                  <Pill tone={u.status === "valid" ? "ok" : "warn"}>{u.status}</Pill>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </section>

      {/* Classification workflow step — shown when products need classifying */}
      {project.unclassified_pt_count > 0 && uploads.length > 0 && (() => {
        const latestUpload = [...uploads].reverse().find((u) => u.products_count > 0);
        if (!latestUpload) return null;
        const ptEnabled = project.methodologies_enabled.includes("protein_tracker");
        return ptEnabled ? (
          <section className="mt-6">
            <Card>
              <CardHeader
                title="Classify products"
                subtitle={`${project.unclassified_pt_count} product${project.unclassified_pt_count === 1 ? "" : "s"} need classification before a calculation can run.`}
              />
              <p className="mt-2 text-xs text-gray-500">
                The rules engine will classify each product. Ambiguous products are queued for manual
                review. Re-run classification after uploading new data.
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
                    onClick={() => onClassify(latestUpload.id, "protein_tracker")}
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
