"use client";

import { useEffect, useMemo, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { Button, Card, CardHeader, Field, Pill } from "@/components/ui";
import { useAuth } from "@/lib/auth-context";
import { isSupabaseConfigured } from "@/lib/supabase";
import {
  createApi,
  type ColumnMappingEntry,
  type Job,
  type JobResult,
  type MappingPreviewResult,
  type Methodology,
  type Project,
  type UploadResult,
  type WWFStep2UploadResult,
} from "@/lib/api";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const CANONICAL_FIELDS = [
  "external_product_id",
  "product_name",
  "weight_per_item_kg",
  "brand",
  "retailer_category",
  "retailer_subcategory",
  "ingredients_text",
  "is_own_brand",
  "ean",
  "labels",
  "country",
  "language",
  "reporting_period",
  "items_purchased",
  "protein_pct",
  "items_sold",
  "retail_channel",
] as const;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

async function uploadViaStorage(file: File, signedUrl: string): Promise<void> {
  const res = await fetch(signedUrl, {
    method: "PUT",
    body: file,
    headers: { "Content-Type": file.type || "text/csv" },
  });
  if (!res.ok) throw new Error(`Storage upload failed: ${res.status} ${res.statusText}`);
}

async function parseHeadersFromFile(file: File): Promise<string[]> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = (e) => {
      const text = (e.target?.result as string) ?? "";
      const firstLine = text.split(/\r?\n/).find((l) => l.trim()) ?? "";
      const sep = firstLine.includes("\t") ? "\t" : ",";
      resolve(firstLine.split(sep).map((h) => h.replace(/^"|"$/g, "").trim()).filter(Boolean));
    };
    reader.onerror = () => reject(new Error("Could not read file headers"));
    reader.readAsText(file.slice(0, 8192));
  });
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function ConfidenceBadge({ confidence }: { confidence: ColumnMappingEntry["confidence"] }) {
  if (confidence === "exact")
    return <Pill tone="ok">exact</Pill>;
  if (confidence === "synonym")
    return <Pill tone="warn">synonym</Pill>;
  return <Pill tone="neutral">unmatched</Pill>;
}

function MappingTable({
  entries,
  overrides,
  onChange,
}: {
  entries: ColumnMappingEntry[];
  overrides: Record<string, string>;
  onChange: (normHeader: string, value: string) => void;
}) {
  return (
    <div className="mt-4 overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-gray-200 text-left text-gray-500 uppercase tracking-wider">
            <th className="pb-2 pr-4 font-medium">CSV header</th>
            <th className="pb-2 pr-4 font-medium">Map to field</th>
            <th className="pb-2 font-medium">Confidence</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {entries.map((entry) => {
            const current = overrides[entry.normalised_header] ?? entry.canonical_field ?? "__none__";
            return (
              <tr key={entry.normalised_header} className="py-1">
                <td className="py-2 pr-4 font-mono text-gray-800 align-middle">
                  {entry.raw_header}
                  {entry.enrichment_needed && (
                    <span className="ml-1.5 text-blue-600 text-xs">(enrichable)</span>
                  )}
                </td>
                <td className="py-2 pr-4 align-middle">
                  <select
                    value={current}
                    onChange={(e) => onChange(entry.normalised_header, e.target.value)}
                    className="rounded border border-gray-300 bg-white px-2 py-1 text-xs text-gray-800 focus:border-brand-500 focus:outline-none"
                  >
                    <option value="__none__">— skip / use as-is —</option>
                    <option value="ignore">ignore (drop column)</option>
                    {CANONICAL_FIELDS.map((f) => (
                      <option key={f} value={f}>
                        {f}
                      </option>
                    ))}
                  </select>
                </td>
                <td className="py-2 align-middle">
                  <ConfidenceBadge confidence={entry.confidence} />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
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
        {hasAi ? (
          <div>
            <div className="text-xs uppercase tracking-wider text-gray-500">AI accepted</div>
            <div className="mt-1 text-lg font-semibold">{aiAccepted}</div>
          </div>
        ) : (
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

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function UploadPage() {
  const router = useRouter();
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const { accessToken } = useAuth();
  const api = useMemo(() => createApi(accessToken), [accessToken]);
  const useStorageFlow = isSupabaseConfigured();

  // Project context (needed to pass methodologies to preview-mapping)
  const [project, setProject] = useState<Project | null>(null);
  useEffect(() => {
    api.getProject(projectId).then(setProject).catch(() => null);
  }, [api, projectId]);

  // Step 1 — file selection + header preview
  const [file, setFile] = useState<File | null>(null);
  const [mappingPreview, setMappingPreview] = useState<MappingPreviewResult | null>(null);
  const [mappingOverrides, setMappingOverrides] = useState<Record<string, string>>({});
  const [mappingBusy, setMappingBusy] = useState(false);
  const [mappingError, setMappingError] = useState<string | null>(null);

  // Step 2 — upload + ingestion
  const [result, setResult] = useState<UploadResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [busyLabel, setBusyLabel] = useState("Uploading…");

  // Step 3 — classify
  const [classifyBusy, setClassifyBusy] = useState<Methodology | null>(null);
  const [classifyJob, setClassifyJob] = useState<Job | null>(null);
  const [lastClassifiedMethodology, setLastClassifiedMethodology] = useState<Methodology | null>(null);

  // Step 4 — WWF Step 2
  const [wwfStep2File, setWwfStep2File] = useState<File | null>(null);
  const [wwfStep2Result, setWwfStep2Result] = useState<WWFStep2UploadResult | null>(null);
  const [wwfStep2Busy, setWwfStep2Busy] = useState(false);
  const [wwfStep2Error, setWwfStep2Error] = useState<string | null>(null);

  // -------------------------------------------------------------------------
  // Handlers
  // -------------------------------------------------------------------------

  async function onFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const picked = e.target.files?.[0] ?? null;
    setFile(picked);
    setMappingPreview(null);
    setMappingOverrides({});
    setMappingError(null);
    setResult(null);
    setError(null);

    if (!picked) return;
    setMappingBusy(true);
    try {
      const rawHeaders = await parseHeadersFromFile(picked);
      const preview = await api.previewMapping(rawHeaders, project?.methodologies_enabled);
      setMappingPreview(preview);
      // Seed overrides from inferred mapping; auto_ignore columns are pre-set to "ignore"
      const initial: Record<string, string> = {};
      for (const entry of preview.entries) {
        if (entry.auto_ignore) {
          initial[entry.normalised_header] = "ignore";
        } else if (entry.canonical_field) {
          initial[entry.normalised_header] = entry.canonical_field;
        }
      }
      setMappingOverrides(initial);
    } catch (err) {
      setMappingError(err instanceof Error ? err.message : "Could not preview column mapping");
    } finally {
      setMappingBusy(false);
    }
  }

  function onMappingChange(normHeader: string, value: string) {
    setMappingOverrides((prev) => ({ ...prev, [normHeader]: value }));
  }

  // Build the effective column_mapping dict to pass to ingest:
  // - only entries that differ from or fill a canonical field
  // - "__none__" = skip (don't include, let pipeline use original key)
  function buildColumnMapping(): Record<string, string> | undefined {
    if (!mappingPreview) return undefined;
    const mapping: Record<string, string> = {};
    for (const entry of mappingPreview.entries) {
      const chosen = mappingOverrides[entry.normalised_header];
      if (!chosen || chosen === "__none__") continue;
      // Only include if it differs from the raw normalised key (or is "ignore")
      if (chosen !== entry.normalised_header) {
        mapping[entry.normalised_header] = chosen;
      }
    }
    return Object.keys(mapping).length > 0 ? mapping : undefined;
  }

  async function onUpload() {
    if (!file) return;
    setError(null);
    setBusy(true);
    const columnMapping = buildColumnMapping();

    try {
      if (useStorageFlow) {
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
          columnMapping,
        );
        setResult(r);
      } else {
        setBusyLabel("Uploading…");
        const r = await api.uploadCsv(projectId, file, columnMapping);
        setResult(r);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
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
      const job = await api.enqueueClassify(projectId, result.id, m);
      const finalJob =
        job.status === "queued" || job.status === "running"
          ? await api.pollJob(job.job_id)
          : job;
      setClassifyJob(finalJob);
      setLastClassifiedMethodology(m);
      if (finalJob.status === "failed") setError(finalJob.error_message ?? "Classification failed");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Classification failed");
    } finally {
      setClassifyBusy(null);
    }
  }

  async function onWwfStep2Upload(e: React.FormEvent) {
    e.preventDefault();
    if (!wwfStep2File) return;
    setWwfStep2Busy(true);
    setWwfStep2Error(null);
    setWwfStep2Result(null);
    try {
      const r = await api.uploadWwfStep2(projectId, wwfStep2File);
      setWwfStep2Result(r);
    } catch (err) {
      setWwfStep2Error(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setWwfStep2Busy(false);
    }
  }

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------

  const projectMethodologies = project?.methodologies_enabled ?? [];
  const hasMissingPt =
    mappingPreview &&
    mappingPreview.missing_required_pt.length > 0 &&
    projectMethodologies.includes("protein_tracker");
  const hasMissingWwf =
    mappingPreview &&
    mappingPreview.missing_required_wwf.length > 0 &&
    projectMethodologies.includes("wwf");
  const hasDuplicates = mappingPreview && mappingPreview.duplicate_normalised.length > 0;

  return (
    <div className="mx-auto max-w-3xl">
      <h1 className="text-2xl font-semibold tracking-tight">Upload data</h1>
      <p className="mt-1 text-sm text-gray-600">
        Upload a CSV. The pipeline drops commercial columns at the boundary, normalises units, and
        validates per methodology.
      </p>

      {/* Step 1 — file selection */}
      <div className="mt-8">
        <Card>
          <CardHeader title="1. Pick a CSV file" />
          <div className="mt-4 space-y-4">
            <Field label="CSV / TSV / TXT file">
              <input
                type="file"
                accept=".csv,.tsv,.txt,text/csv,text/plain,text/tab-separated-values"
                onChange={onFileChange}
                className="block w-full text-sm"
              />
              {file && (
                <p className="mt-1 text-xs text-gray-500">
                  {file.name} &mdash; {formatBytes(file.size)}
                </p>
              )}
            </Field>
            {mappingBusy && (
              <p className="text-sm text-gray-500">Parsing column headers…</p>
            )}
            {mappingError && (
              <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">
                {mappingError}
              </div>
            )}
          </div>
        </Card>
      </div>

      {/* Step 1b — column mapping */}
      {mappingPreview && !mappingBusy && (
        <div className="mt-6">
          <Card>
            <CardHeader
              title="1b. Column mapping"
              subtitle="Review suggested field mappings. Adjust any that look wrong before uploading."
            />

            {hasDuplicates && (
              <div className="mt-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                Duplicate column headers detected: {mappingPreview.duplicate_normalised.join(", ")}.
                Only the last value will be kept per row.
              </div>
            )}

            {(hasMissingPt || hasMissingWwf) && (
              <div className="mt-3 space-y-1">
                {hasMissingPt && (
                  <div className="rounded-md border border-rose-100 bg-rose-50 px-3 py-2 text-xs text-rose-700">
                    Missing required PT fields: {mappingPreview.missing_required_pt.join(", ")}
                  </div>
                )}
                {hasMissingWwf && (
                  <div className="rounded-md border border-rose-100 bg-rose-50 px-3 py-2 text-xs text-rose-700">
                    Missing required WWF fields: {mappingPreview.missing_required_wwf.join(", ")}
                  </div>
                )}
              </div>
            )}

            <MappingTable
              entries={mappingPreview.entries}
              overrides={mappingOverrides}
              onChange={onMappingChange}
            />

            <div className="mt-4">
              <Button onClick={onUpload} disabled={busy}>
                {busy ? busyLabel : "Upload with this mapping"}
              </Button>
            </div>
          </Card>
        </div>
      )}

      {/* Step 2 — ingestion report */}
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
                <p className="mt-2 text-xs text-rose-700">
                  Rows with errors were not ingested. Fix the CSV and re-upload.
                </p>
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

      {/* Step 3 — classify */}
      {result && (result.status === "ready_for_classification" || result.status === "valid") && (
        <div className="mt-6">
          <Card>
            <CardHeader
              title="3. Classify"
              subtitle="Runs the deterministic rules engine. Unmatched products are queued for Altera review."
            />
            <div className="mt-4 flex gap-2">
              <Button
                onClick={() => onClassify("protein_tracker")}
                disabled={classifyBusy !== null}
              >
                {classifyBusy === "protein_tracker" ? "Classifying…" : "Classify as Protein Tracker"}
              </Button>
              <Button
                variant="secondary"
                onClick={() => onClassify("wwf")}
                disabled={classifyBusy !== null}
              >
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

      {/* Step 4 — WWF Step 2 */}
      {classifyJob?.status === "succeeded" && lastClassifiedMethodology === "wwf" && (
        <div className="mt-6">
          <Card>
            <CardHeader
              title="4. Step 2 ingredient attribution (WWF)"
              subtitle="Optional: upload a JSON file mapping own-brand composite products to their ingredients."
            />
            <p className="mt-3 text-sm text-gray-600">
              Step 2 applies to <strong>own-brand composite products only</strong>. Branded composites
              are always reported at Step 1 (whole product weight) and are unaffected by this file.
              Uploading a new file replaces any previously stored Step 2 data for this project.
            </p>
            <form onSubmit={onWwfStep2Upload} className="mt-4 space-y-4">
              <Field label="Ingredient JSON file (.json, max 50 MB)">
                <input
                  type="file"
                  accept=".json,application/json"
                  onChange={(e) => setWwfStep2File(e.target.files?.[0] ?? null)}
                  className="block w-full text-sm"
                />
                {wwfStep2File && (
                  <p className="mt-1 text-xs text-gray-500">
                    {wwfStep2File.name} &mdash; {formatBytes(wwfStep2File.size)}
                  </p>
                )}
              </Field>
              <Button type="submit" disabled={!wwfStep2File || wwfStep2Busy} variant="secondary">
                {wwfStep2Busy ? "Uploading…" : "Upload ingredients"}
              </Button>
            </form>
            {wwfStep2Error && (
              <div className="mt-3 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">
                {wwfStep2Error}
              </div>
            )}
            {wwfStep2Result && (
              <div className="mt-4 space-y-3">
                <div className="flex items-center gap-2">
                  <Pill tone={wwfStep2Result.stored ? "ok" : wwfStep2Result.error_count > 0 ? "error" : "warn"}>
                    {wwfStep2Result.stored ? "stored" : "not stored"}
                  </Pill>
                  {wwfStep2Result.stored && (
                    <span className="text-xs text-gray-500">
                      {wwfStep2Result.replaced ? "Replaced previous data — i" : "I"}
                      ngredients saved for {wwfStep2Result.valid_product_count} product
                      {wwfStep2Result.valid_product_count !== 1 ? "s" : ""}
                    </span>
                  )}
                </div>
                {wwfStep2Result.stored && (
                  <div className="rounded-md border border-blue-100 bg-blue-50 px-3 py-2 text-xs text-blue-700">
                    Re-run the calculation to apply these ingredients to the report.
                  </div>
                )}
                <div className="grid grid-cols-2 gap-4 text-sm sm:grid-cols-4">
                  <div>
                    <div className="text-xs uppercase tracking-wider text-gray-500">Products in file</div>
                    <div className="mt-1 text-lg font-semibold">{wwfStep2Result.total_products_in_file}</div>
                  </div>
                  <div>
                    <div className="text-xs uppercase tracking-wider text-gray-500">Own-brand stored</div>
                    <div className="mt-1 text-lg font-semibold">{wwfStep2Result.valid_product_count}</div>
                  </div>
                  <div>
                    <div className="text-xs uppercase tracking-wider text-gray-500">Errors</div>
                    <div className="mt-1 text-lg font-semibold">{wwfStep2Result.error_count}</div>
                  </div>
                  <div>
                    <div className="text-xs uppercase tracking-wider text-gray-500">Warnings</div>
                    <div className="mt-1 text-lg font-semibold">{wwfStep2Result.warning_count}</div>
                  </div>
                </div>
                {(wwfStep2Result.unknown_product_count > 0 || wwfStep2Result.branded_composite_count > 0) && (
                  <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                    {wwfStep2Result.unknown_product_count > 0 && (
                      <div>{wwfStep2Result.unknown_product_count} product(s) not found in project — check external IDs.</div>
                    )}
                    {wwfStep2Result.branded_composite_count > 0 && (
                      <div>
                        {wwfStep2Result.branded_composite_count} branded composite(s): ingredients not stored. These
                        products remain at Step 1 (whole product weight) only.
                      </div>
                    )}
                  </div>
                )}
                {wwfStep2Result.product_results.some((r) => r.errors.length > 0) && (
                  <details>
                    <summary className="cursor-pointer text-sm font-medium text-rose-700">
                      Validation errors
                    </summary>
                    <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-rose-800">
                      {wwfStep2Result.product_results
                        .flatMap((r) =>
                          r.errors.map((e) => ({
                            key: `${r.external_product_id}-${e.ingredient_index}-${e.field}`,
                            text: `${r.external_product_id} [${e.field}]: ${e.message}`,
                          })),
                        )
                        .slice(0, 20)
                        .map((e) => <li key={e.key}>{e.text}</li>)}
                    </ul>
                  </details>
                )}
              </div>
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
        {classifyJob?.status === "succeeded" && (classifyJob.result?.queued_for_review ?? 0) > 0 && (
          <Button onClick={() => router.push(`/projects/${projectId}/review`)}>
            Review queue ({classifyJob.result!.queued_for_review}) →
          </Button>
        )}
        {classifyJob?.status === "succeeded" && (classifyJob.result?.queued_for_review ?? 1) === 0 && (
          <Button onClick={() => router.push(`/projects/${projectId}/runs`)}>
            Calculate →
          </Button>
        )}
      </div>
    </div>
  );
}
