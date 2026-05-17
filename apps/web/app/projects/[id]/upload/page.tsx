"use client";

import { useMemo, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { Button, Card, CardHeader, Field, Pill } from "@/components/ui";
import { useAuth } from "@/lib/auth-context";
import { isSupabaseConfigured } from "@/lib/supabase";
import {
  createApi,
  type Job,
  type JobResult,
  type Methodology,
  type UploadResult,
} from "@/lib/api";

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

async function uploadViaStorage(
  file: File,
  signedUrl: string,
): Promise<void> {
  const res = await fetch(signedUrl, {
    method: "PUT",
    body: file,
    headers: { "Content-Type": file.type || "text/csv" },
  });
  if (!res.ok) {
    throw new Error(`Storage upload failed: ${res.status} ${res.statusText}`);
  }
}

function ClassifyResultSummary({ result }: { result: JobResult }) {
  const queued = (result.queued_for_review as number) ?? 0;
  const aiAttempted = (result.ai_attempted as number) ?? 0;
  const aiAccepted = (result.ai_accepted as number) ?? 0;
  const aiReview = (result.ai_review as number) ?? 0;
  const hasAi = aiAttempted > 0;
  return (
    <>
      <div className="mt-4 grid grid-cols-2 gap-4 text-sm sm:grid-cols-4">
        <div>
          <div className="text-xs uppercase tracking-wider text-gray-500">Rules matched</div>
          <div className="mt-1 text-lg font-semibold">{result.matched ?? 0}</div>
        </div>
        {hasAi && (
          <div>
            <div className="text-xs uppercase tracking-wider text-gray-500">AI accepted</div>
            <div className="mt-1 text-lg font-semibold">{aiAccepted}</div>
          </div>
        )}
        {!hasAi && (
          <div>
            <div className="text-xs uppercase tracking-wider text-gray-500">Pass-through</div>
            <div className="mt-1 text-lg font-semibold">{result.pass_through ?? 0}</div>
          </div>
        )}
        <div>
          <div className="text-xs uppercase tracking-wider text-gray-500">Collisions</div>
          <div className="mt-1 text-lg font-semibold">{result.rule_collision ?? 0}</div>
        </div>
        <div>
          <div className="text-xs uppercase tracking-wider text-gray-500">Sent to review</div>
          <div className="mt-1 text-lg font-semibold">{queued}</div>
        </div>
      </div>
      {hasAi && (
        <div className="mt-3 rounded-md border border-blue-100 bg-blue-50 px-3 py-2 text-xs text-blue-700">
          AI classifier: {aiAttempted} attempted · {aiAccepted} accepted · {aiReview} sent to Altera review
        </div>
      )}
      {queued > 0 && (
        <p className="mt-3 text-sm text-gray-600">
          {queued} product{queued !== 1 ? "s" : ""} will be reviewed by the Altera team before the
          report is generated.
        </p>
      )}
    </>
  );
}

export default function UploadPage() {
  const router = useRouter();
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const { accessToken } = useAuth();
  const api = useMemo(() => createApi(accessToken), [accessToken]);
  const useStorageFlow = isSupabaseConfigured();

  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<UploadResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [busyLabel, setBusyLabel] = useState("Uploading…");
  const [classifyBusy, setClassifyBusy] = useState<Methodology | null>(null);
  const [classifyJob, setClassifyJob] = useState<Job | null>(null);

  async function onUpload(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
    setError(null);
    setBusy(true);

    try {
      if (useStorageFlow) {
        // Two-step: reserve upload ID → PUT to signed URL → ingest from storage
        setBusyLabel("Preparing…");
        const prep = await api.prepareUpload(projectId, file.name);

        setBusyLabel("Uploading to storage…");
        await uploadViaStorage(file, prep.signed_url);

        setBusyLabel("Processing…");
        const r = await api.ingestUpload(
          projectId,
          prep.upload_id,
          prep.storage_path,
          file.name,
        );
        setResult(r);
      } else {
        // Dev fallback: POST multipart directly to the API
        setBusyLabel("Uploading…");
        const r = await api.uploadCsv(projectId, file);
        setResult(r);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload failed");
    } finally {
      setBusy(false);
      setBusyLabel("Uploading…");
    }
  }

  async function onClassify(m: Methodology) {
    if (!result) return;
    setClassifyBusy(m);
    setError(null);
    try {
      // Enqueue classify job; SyncDevRunner completes it synchronously.
      // For future async workers, pollJob() will wait for completion.
      const job = await api.enqueueClassify(projectId, result.id, m);
      const finalJob =
        job.status === "queued" || job.status === "running"
          ? await api.pollJob(job.job_id)
          : job;
      setClassifyJob(finalJob);
      if (finalJob.status === "failed") {
        setError(finalJob.error_message ?? "Classification failed");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Classification failed");
    } finally {
      setClassifyBusy(null);
    }
  }

  return (
    <div className="mx-auto max-w-3xl">
      <h1 className="text-2xl font-semibold tracking-tight">Upload data</h1>
      <p className="mt-1 text-sm text-gray-600">
        Upload a CSV. The pipeline drops commercial columns at the boundary,
        normalises units, and validates per methodology.
      </p>

      <div className="mt-8">
        <Card>
          <CardHeader title="1. Pick a CSV file" />
          <form onSubmit={onUpload} className="mt-4 space-y-4">
            <Field label="CSV / TSV / TXT file">
              <input
                type="file"
                accept=".csv,.tsv,.txt,text/csv,text/plain,text/tab-separated-values"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                className="block w-full text-sm"
              />
              {file && (
                <p className="mt-1 text-xs text-gray-500">
                  {file.name} &mdash; {formatBytes(file.size)}
                </p>
              )}
            </Field>
            <Button type="submit" disabled={!file || busy}>
              {busy ? busyLabel : "Upload"}
            </Button>
          </form>
        </Card>
      </div>

      {result && (
        <div className="mt-6">
          <Card>
            <CardHeader
              title="2. Ingestion report"
              action={
                <Pill
                  tone={
                    result.status === "ready_for_classification" || result.status === "valid"
                      ? "ok"
                      : result.status === "validation_failed" || result.status === "invalid"
                      ? "error"
                      : "warn"
                  }
                >
                  {result.status}
                </Pill>
              }
            />
            {result.duplicate_of && (
              <div className="mt-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
                This file appears to be a duplicate of a previous upload (same content). Processing
                continued, but you may want to verify this is intentional.
              </div>
            )}
            <dl className="mt-4 grid grid-cols-2 gap-4 text-sm">
              <div>
                <dt className="text-xs uppercase tracking-wider text-gray-500">Rows</dt>
                <dd className="mt-1 font-medium">{result.row_count}</dd>
              </div>
              <div>
                <dt className="text-xs uppercase tracking-wider text-gray-500">Products</dt>
                <dd className="mt-1 font-medium">{result.products_count}</dd>
              </div>
              <div>
                <dt className="text-xs uppercase tracking-wider text-gray-500">Errors</dt>
                <dd className="mt-1 font-medium">{result.errors.length}</dd>
              </div>
              <div>
                <dt className="text-xs uppercase tracking-wider text-gray-500">Warnings</dt>
                <dd className="mt-1 font-medium">{result.warnings.length}</dd>
              </div>
              {result.file_size_bytes != null && (
                <div>
                  <dt className="text-xs uppercase tracking-wider text-gray-500">File size</dt>
                  <dd className="mt-1 font-medium">{formatBytes(result.file_size_bytes)}</dd>
                </div>
              )}
            </dl>
            {result.dropped_columns.length > 0 && (
              <div className="mt-4">
                <div className="text-xs font-medium uppercase tracking-wider text-gray-500">
                  Dropped commercial columns
                </div>
                <div className="mt-1 flex flex-wrap gap-1">
                  {result.dropped_columns.map((c) => (
                    <Pill key={c} tone="neutral">{c}</Pill>
                  ))}
                </div>
              </div>
            )}
            {result.errors.length > 0 && (
              <details className="mt-4">
                <summary className="cursor-pointer text-sm font-medium text-rose-700">
                  Errors ({result.errors.length})
                </summary>
                <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-rose-800">
                  {result.errors.slice(0, 20).map((e, i) => (
                    <li key={i}>
                      row {e.row_number}: {e.code} — {e.message}
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </Card>
        </div>
      )}

      {result && (result.status === "ready_for_classification" || result.status === "valid") && (
        <div className="mt-6">
          <Card>
            <CardHeader
              title="3. Classify"
              subtitle="Runs the deterministic rules engine. Unmatched products are queued for Altera review."
            />
            <div className="mt-4 flex gap-2">
              <Button onClick={() => onClassify("protein_tracker")} disabled={classifyBusy !== null}>
                {classifyBusy === "protein_tracker" ? "Classifying…" : "Classify as Protein Tracker"}
              </Button>
              <Button variant="secondary" onClick={() => onClassify("wwf")} disabled={classifyBusy !== null}>
                {classifyBusy === "wwf" ? "Classifying…" : "Classify as WWF"}
              </Button>
            </div>
            {classifyJob && (
              <>
                <div className="mt-3 flex items-center gap-2">
                  <Pill
                    tone={
                      classifyJob.status === "succeeded"
                        ? "ok"
                        : classifyJob.status === "failed"
                        ? "error"
                        : "warn"
                    }
                  >
                    {classifyJob.status}
                  </Pill>
                  <span className="text-xs text-gray-500">job {classifyJob.job_id.slice(0, 8)}</span>
                </div>
                {classifyJob.status === "succeeded" && classifyJob.result && (
                  <ClassifyResultSummary result={classifyJob.result} />
                )}
              </>
            )}
          </Card>
        </div>
      )}

      {error && (
        <div className="mt-4 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">
          {error}
        </div>
      )}

      <div className="mt-8 flex gap-2">
        <Button variant="ghost" onClick={() => router.push(`/projects/${projectId}`)}>
          ← Back to project
        </Button>
        {classifyJob?.status === "succeeded" &&
          (classifyJob.result?.queued_for_review ?? 0) > 0 && (
            <Button onClick={() => router.push(`/projects/${projectId}/review`)}>
              Review queue ({classifyJob.result!.queued_for_review}) →
            </Button>
          )}
        {classifyJob?.status === "succeeded" &&
          (classifyJob.result?.queued_for_review ?? 1) === 0 && (
            <Button onClick={() => router.push(`/projects/${projectId}/runs`)}>
              Calculate →
            </Button>
          )}
      </div>
    </div>
  );
}
