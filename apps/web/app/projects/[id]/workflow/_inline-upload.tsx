"use client";

/**
 * Phase 34E — Inline upload + column mapping for wizard Step 1.
 *
 * Replaces the standalone /upload page in the normal workflow. Designed
 * for sparse retailer CSVs (product name + unit weight + volume) — the
 * preview auto-maps recognised columns and only forces user input on
 * columns the server marked confidence="none" or duplicates.
 *
 * Out of scope (kept on the legacy /upload page for admin/debug):
 * - WWF Step 2 ingredient JSON upload
 * - Direct-to-Supabase signed-URL upload for >10 MB files
 * - Detailed validation report (errors + dropped columns expansion)
 */

import Link from "next/link";
import { useState } from "react";

import { Button, Card, Pill } from "@/components/ui";
import { useT } from "@/lib/i18n";
import type {
  ColumnMappingEntry,
  IngestionJob,
  MappingPreviewResult,
  UploadResult,
} from "@/lib/api";
import {
  ApiError,
  createApi,
  INGESTION_JOB_TERMINAL_STATUSES,
} from "@/lib/api";

// Canonical fields the user can map a CSV column to. Kept in sync with
// the legacy upload page; "ignore" is added as a sentinel for columns
// the user wants dropped explicitly.
const CANONICAL_FIELDS = [
  "external_product_id",
  "product_name",
  "brand",
  "retailer_category",
  "retailer_subcategory",
  "weight_per_item_kg",
  "weight_per_item_g",
  "items_purchased",
  "protein_pct",
  "plant_protein_pct",
  "animal_protein_pct",
  "ingredients_text",
  "is_own_brand",
  "ean",
  "labels",
  "country",
  "language",
  "reporting_period",
  "items_sold",
  "retail_channel",
] as const;

// Sentinel error message thrown by ``parseHeadersFromFile`` (a
// module-level helper that has no access to ``useT``). The component
// catch detects it and surfaces a translated message.
const UPLOAD_PARSE_HEADERS_ERROR_FR = "Lecture des en-têtes impossible";

// Maps canonical field → i18n key. The KEYS are canonical (submitted to
// the API unchanged); only the resolved display labels are translated.
const CANONICAL_FIELD_LABEL_KEYS: Record<string, string> = {
  external_product_id: "upload.field.external_product_id",
  product_name: "upload.field.product_name",
  brand: "upload.field.brand",
  retailer_category: "upload.field.retailer_category",
  retailer_subcategory: "upload.field.retailer_subcategory",
  weight_per_item_kg: "upload.field.weight_per_item_kg",
  weight_per_item_g: "upload.field.weight_per_item_g",
  items_purchased: "upload.field.items_purchased",
  protein_pct: "upload.field.protein_pct",
  plant_protein_pct: "upload.field.plant_protein_pct",
  animal_protein_pct: "upload.field.animal_protein_pct",
  ingredients_text: "upload.field.ingredients_text",
  is_own_brand: "upload.field.is_own_brand",
  ean: "upload.field.ean",
  labels: "upload.field.labels",
  country: "upload.field.country",
  language: "upload.field.language",
  reporting_period: "upload.field.reporting_period",
  items_sold: "upload.field.items_sold",
  retail_channel: "upload.field.retail_channel",
};

function labelFor(field: string, t: (key: string) => string): string {
  const key = CANONICAL_FIELD_LABEL_KEYS[field];
  return key ? t(key) : field;
}

async function parseHeadersFromFile(file: File): Promise<string[]> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = (e) => {
      const text = (e.target?.result as string) ?? "";
      const firstLine = text.split(/\r?\n/).find((l) => l.trim()) ?? "";
      const sep = firstLine.includes("\t") ? "\t" : ",";
      resolve(
        firstLine
          .split(sep)
          .map((h) => h.replace(/^"|"$/g, "").trim())
          .filter(Boolean),
      );
    };
    reader.onerror = () =>
      reject(new Error(UPLOAD_PARSE_HEADERS_ERROR_FR));
    // Only the first 8 KB are needed — that always contains the header
    // row even for files with very long product names.
    reader.readAsText(file.slice(0, 8192));
  });
}

function ConfidenceBadge({
  confidence,
}: {
  confidence: ColumnMappingEntry["confidence"];
}) {
  const t = useT();
  if (confidence === "exact") return <Pill tone="ok">exact</Pill>;
  if (confidence === "synonym")
    return <Pill tone="warn">{t("upload.confidence.synonym")}</Pill>;
  return <Pill tone="neutral">{t("upload.confidence.unmatched")}</Pill>;
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
  const t = useT();
  return (
    <div className="scroll-soft mt-3 overflow-x-auto rounded-2xl border border-line">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-line bg-mint-50/70 text-left text-[11px] uppercase tracking-wider text-ink-soft">
            <th className="py-2.5 pl-4 pr-3 font-semibold">{t("upload.table.csvColumn")}</th>
            <th className="py-2.5 pr-3 font-semibold">{t("upload.table.mapTo")}</th>
            <th className="py-2.5 pr-3 font-semibold">{t("upload.table.detection")}</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line-soft">
          {entries.map((entry) => {
            const current =
              overrides[entry.normalised_header] ??
              entry.canonical_field ??
              "__none__";
            return (
              <tr
                key={entry.normalised_header}
                className="transition-colors hover:bg-mint-50/50"
              >
                <td className="py-2 pl-4 pr-3 font-mono text-forest-900 align-middle">
                  {entry.raw_header}
                </td>
                <td className="py-2 pr-3 align-middle">
                  <select
                    value={current}
                    onChange={(e) =>
                      onChange(entry.normalised_header, e.target.value)
                    }
                    className="rounded-lg border border-line bg-white px-2 py-1 text-xs text-forest-900 focus:border-brand-400 focus:outline-none"
                  >
                    <option value="__none__">{t("upload.table.optionNone")}</option>
                    <option value="ignore">{t("upload.table.optionIgnore")}</option>
                    {CANONICAL_FIELDS.map((f) => (
                      <option key={f} value={f}>
                        {labelFor(f, t)}
                      </option>
                    ))}
                  </select>
                </td>
                <td className="py-2 pr-3 align-middle">
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

export function InlineUpload({
  projectId,
  accessToken,
  methodologies,
  latestUpload,
  onUploaded,
}: {
  projectId: string;
  accessToken: string | null;
  methodologies: string[];
  latestUpload: UploadResult | null;
  onUploaded: () => void | Promise<void>;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<MappingPreviewResult | null>(null);
  const [overrides, setOverrides] = useState<Record<string, string>>({});
  const [previewing, setPreviewing] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showMapping, setShowMapping] = useState(false);
  // Phase 34Y — chunked ingestion job state. When non-null, the
  // widget renders a progress bar instead of the submit button.
  // ``transientError`` carries a temporary network-blip message that
  // does NOT wipe job state (Phase 34W resilience pattern).
  const [job, setJob] = useState<IngestionJob | null>(null);
  const [transientError, setTransientError] = useState<string | null>(null);

  const t = useT();
  const api = createApi(accessToken);

  async function pickFile(f: File) {
    setFile(f);
    setError(null);
    setPreview(null);
    setOverrides({});
    setShowMapping(false);
    setPreviewing(true);
    try {
      const headers = await parseHeadersFromFile(f);
      const result = await api.previewMapping(headers, methodologies);
      setPreview(result);
      // Seed overrides from server detection so the user only edits
      // the ones the server could not auto-map.
      const initial: Record<string, string> = {};
      for (const e of result.entries) {
        if (e.confidence !== "none" && e.canonical_field) {
          initial[e.normalised_header] = e.canonical_field;
        }
      }
      setOverrides(initial);
      // Demo polish — open the column-mapping panel automatically once the
      // file is selected and the preview succeeded, so the user reaches
      // mapping without an extra click. Only runs on success (a failed
      // preview falls into the catch below and leaves mapping closed).
      setShowMapping(true);
    } catch (e) {
      if (
        e instanceof Error &&
        e.message === UPLOAD_PARSE_HEADERS_ERROR_FR
      ) {
        setError(t("upload.parse.headersUnreadable"));
      } else {
        setError(
          e instanceof Error ? e.message : t("upload.previewError"),
        );
      }
    } finally {
      setPreviewing(false);
    }
  }

  /**
   * Phase 34Y — chunked ingestion job flow.
   *
   * Replaces the single-request ``api.uploadCsv`` path that failed
   * with "Failed to fetch" on 1050+ row CSVs because the synchronous
   * route blocked Render's worker for ~60s. The new flow:
   *
   *   1. Mint a client-side upload UUID.
   *   2. POST /uploads/{uid}/ingestion-jobs — returns within 1s.
   *      Parses the CSV server-side but defers product inserts to
   *      the advance loop.
   *   3. Loop POST /ingestion-jobs/{jid}/advance — each call
   *      processes one ``chunk_size`` (default 500) batch of
   *      products in ~500ms.
   *   4. When status is terminal, refresh workflow state.
   *
   * Transient 5xx / network errors do NOT wipe job state — they
   * surface a "reconnecting" banner and the loop retries up to 5
   * times before giving up.
   */
  async function pollJob(jobId: string) {
    let consecutiveFailures = 0;
    // eslint-disable-next-line no-constant-condition
    while (true) {
      try {
        const updated = await api.advanceIngestionJob(projectId, jobId);
        setJob(updated);
        setTransientError(null);
        consecutiveFailures = 0;
        if (INGESTION_JOB_TERMINAL_STATUSES.includes(updated.status)) {
          await onUploaded();
          return;
        }
      } catch (e) {
        consecutiveFailures += 1;
        setTransientError(t("upload.error.transient"));
        if (consecutiveFailures >= 5) {
          setError(t("upload.error.tooManyFailures"));
          setTransientError(null);
          return;
        }
        await new Promise((r) => setTimeout(r, 3000));
        continue;
      }
      // Brief pause between successful advances so the wizard's
      // progress bar feels responsive without hammering the API.
      await new Promise((r) => setTimeout(r, 800));
    }
  }

  async function submit() {
    if (!file) return;
    setSubmitting(true);
    setError(null);
    setTransientError(null);
    try {
      const columnMapping: Record<string, string> = {};
      for (const [k, v] of Object.entries(overrides)) {
        if (v && v !== "__none__") columnMapping[k] = v;
      }
      // Mint the upload id client-side. The route param ties the
      // ingestion job to a specific upload record from creation.
      const uploadId = crypto.randomUUID();
      const created = await api.createIngestionJob(
        projectId,
        uploadId,
        file,
        {
          columnMapping:
            Object.keys(columnMapping).length > 0 ? columnMapping : undefined,
          chunkSize: 500,
        },
      );
      setJob(created);
      await pollJob(created.job_id);
      // Cleanup is only done on success — failed/cancelled jobs keep
      // the file picker populated so the user can retry.
      setFile(null);
      setPreview(null);
      setOverrides({});
    } catch (e) {
      if (e instanceof ApiError && typeof e.detail === "object" && e.detail) {
        const d = e.detail as { message?: string; error_code?: string };
        // Phase 34Y — map known ingestion error codes to friendly,
        // translated wrappers. The server ``d.message`` interpolation
        // is preserved; only the static wrapper text is translated.
        const friendly =
          d.error_code === "invalid_csv"
            ? t("upload.error.invalidCsv").replace(
                "{message}",
                d.message ?? t("upload.error.invalidCsvFallback"),
              )
            : d.error_code === "invalid_mapping"
            ? t("upload.error.invalidMapping").replace(
                "{message}",
                d.message ?? t("upload.error.invalidMappingFallback"),
              )
            : d.error_code === "ingestion_create_failed"
            ? t("upload.error.createFailed")
            : d.error_code === "ingestion_advance_failed"
            ? t("upload.error.advanceFailed")
            : d.error_code === "ingestion_job_not_found"
            ? t("upload.error.jobNotFound")
            : d.message;
        setError(friendly ?? `${e.status} ${e}`);
      } else if (e instanceof Error && e.message.includes("Failed to fetch")) {
        setError(t("upload.error.failedToFetch"));
      } else {
        setError(e instanceof Error ? e.message : t("upload.error.generic"));
      }
    } finally {
      setSubmitting(false);
    }
  }

  // Required-field gating: PT needs product_name. The wizard already
  // handles the "no methodology" / "no products" case downstream, so
  // here we just refuse to submit if product_name is unmapped.
  const mappedFields = new Set<string>();
  if (preview) {
    for (const e of preview.entries) {
      const v =
        overrides[e.normalised_header] ?? e.canonical_field ?? "__none__";
      if (v && v !== "__none__" && v !== "ignore") mappedFields.add(v);
    }
  }
  const productNameMapped = mappedFields.has("product_name");

  return (
    <div className="space-y-4">
      {/* Already-imported file summary */}
      {latestUpload && !file && (
        <Card>
          <div className="flex items-start justify-between">
            <div>
              <p className="text-sm font-semibold text-forest-900">
                {latestUpload.original_filename}
              </p>
              <p className="mt-0.5 text-xs text-ink-muted">
                {t("upload.summary.productsRows")
                  .replace("{p}", String(latestUpload.products_count))
                  .replace("{r}", String(latestUpload.row_count ?? "?"))}
              </p>
              {latestUpload.warnings.length > 0 && (
                <p className="mt-1 text-xs text-warn-700">
                  {t("upload.summary.warnings").replace(
                    "{n}",
                    String(latestUpload.warnings.length),
                  )}
                </p>
              )}
            </div>
            <Pill tone="ok">{t("upload.summary.imported")}</Pill>
          </div>
        </Card>
      )}

      <Card>
        {/* Template CTA — download a ready-to-use catalog template before
            importing (demo polish). Routes to the existing /templates page. */}
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <p className="text-xs text-ink-muted">{t("upload.template.hint")}</p>
          <Link
            href="/templates"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 rounded-lg border border-brand-200 bg-white px-3 py-1.5 text-xs font-semibold text-brand-700 transition-colors hover:border-brand-300 hover:bg-mint-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-400 focus-visible:ring-offset-1"
          >
            <span aria-hidden>↓</span>
            {t("upload.template.button")}
          </Link>
        </div>

        {/* Phase Step1-UX — the file picker (and its "Replace file" label) is
            only offered until a catalog has been imported. Once a file exists
            we keep it as the project's catalog and route the user on to
            mapping / AI classification rather than offering a replace. */}
        {!latestUpload && (
          <label className="block rounded-2xl border border-dashed border-line bg-mint-50/40 p-5 transition-colors hover:border-brand-200">
            <span className="text-sm font-semibold text-forest-900">
              {t("upload.picker.choose")}
            </span>
            <input
              type="file"
              accept=".csv,text/csv"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void pickFile(f);
              }}
              className="mt-3 block w-full text-sm text-ink-muted file:mr-3 file:rounded-lg file:border-0 file:bg-brand-600 file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-white hover:file:bg-brand-700"
            />
          </label>
        )}

        {previewing && (
          <div className="mt-3 flex items-center gap-2 text-xs text-ink-muted">
            <span className="h-3 w-3 animate-spin rounded-full border-2 border-brand-200 border-t-brand-600" />
            {t("upload.analysing")}
          </div>
        )}

        {error && (
          <div className="mt-3 rounded-xl border border-danger-100 bg-danger-50 px-3 py-2 text-sm text-danger-700">
            {error}
          </div>
        )}

        {preview && file && (
          <div className="mt-4 space-y-3">
            <div className="rounded-xl border border-line bg-mint-50/60 px-3 py-2.5 text-xs text-forest-700">
              <p>
                <span className="font-semibold text-forest-900">{file.name}</span> ·{" "}
                {t("upload.preview.columnsDetected").replace(
                  "{n}",
                  String(preview.entries.length),
                )}{" "}
                ·{" "}
                {t("upload.preview.autoMapped").replace(
                  "{n}",
                  String(
                    preview.entries.filter((e) => e.confidence === "exact")
                      .length,
                  ),
                )}
              </p>
            </div>

            {preview.missing_required_pt.length > 0 && (
              <div className="rounded-xl border border-warn-100 bg-warn-50 px-3 py-2 text-xs text-warn-700">
                <div className="font-semibold">
                  {t("upload.missing.ptTitle").replace(
                    "{fields}",
                    preview.missing_required_pt.join(", "),
                  )}
                </div>
                <div className="mt-1 text-warn-700/90">
                  {t("upload.missing.ptBody")}
                </div>
              </div>
            )}
            {preview.missing_required_wwf.length > 0 && (
              <div className="rounded-xl border border-warn-100 bg-warn-50 px-3 py-2 text-xs text-warn-700">
                <div className="font-semibold">
                  {t("upload.missing.wwfTitle").replace(
                    "{fields}",
                    preview.missing_required_wwf.join(", "),
                  )}
                </div>
                <div className="mt-1 text-warn-700/90">
                  {t("upload.missing.wwfBody")}
                </div>
              </div>
            )}

            {!showMapping ? (
              <button
                type="button"
                onClick={() => setShowMapping(true)}
                className="text-xs font-medium text-brand-700 hover:underline"
              >
                {t("upload.showMapping")}
              </button>
            ) : (
              <MappingTable
                entries={preview.entries}
                overrides={overrides}
                onChange={(k, v) =>
                  setOverrides((prev) => ({ ...prev, [k]: v }))
                }
              />
            )}

            {/* Phase 34Y — chunked ingestion progress bar. Renders
                while a job is queued/running and remains visible
                after a terminal status for a moment so the user
                sees the final counts. */}
            {job && (
              <div className="mt-3 space-y-2">
                <IngestionJobProgress job={job} transient={transientError} />
              </div>
            )}

            <div className="flex flex-wrap gap-2 pt-1">
              <Button
                onClick={() => void submit()}
                disabled={
                  submitting ||
                  !productNameMapped ||
                  (job !== null &&
                    !INGESTION_JOB_TERMINAL_STATUSES.includes(job.status))
                }
              >
                {submitting && job
                  ? t("upload.submit.importingProgress")
                      .replace("{done}", String(job.processed_rows))
                      .replace("{total}", String(job.total_rows))
                  : submitting
                  ? t("upload.submit.importing")
                  : t("upload.submit.importFile")}
              </Button>
              <Button
                variant="ghost"
                onClick={() => {
                  setFile(null);
                  setPreview(null);
                  setOverrides({});
                  setShowMapping(false);
                  setJob(null);
                  setTransientError(null);
                }}
                disabled={
                  submitting ||
                  (job !== null &&
                    !INGESTION_JOB_TERMINAL_STATUSES.includes(job.status))
                }
              >
                {t("common.cancel")}
              </Button>
            </div>
          </div>
        )}
      </Card>
    </div>
  );
}


/**
 * Phase 34Y — presentational progress component for the chunked
 * ingestion job. Pure: all state comes from the ``job`` prop. The
 * widget never fetches; the parent owns the polling loop.
 */
function IngestionJobProgress({
  job,
  transient,
}: {
  job: IngestionJob;
  transient: string | null;
}) {
  const t = useT();
  const pct = Math.max(0, Math.min(100, Math.round(job.progress_pct)));
  const tone =
    job.status === "completed"
      ? "border-brand-200 bg-mint-100 text-brand-700"
      : job.status === "completed_with_errors"
      ? "border-warn-100 bg-warn-50 text-warn-700"
      : job.status === "failed"
      ? "border-danger-100 bg-danger-50 text-danger-700"
      : job.status === "cancelled"
      ? "border-line bg-line-soft text-ink-muted"
      : "border-brand-200 bg-mint-50 text-brand-700";
  const badge =
    job.status === "queued"
      ? t("upload.job.queued")
      : job.status === "running"
      ? t("upload.job.running")
      : job.status === "completed"
      ? t("upload.job.completed")
      : job.status === "completed_with_errors"
      ? t("upload.job.completedWithErrors")
      : job.status === "failed"
      ? t("upload.job.failed")
      : t("upload.job.cancelled");
  return (
    <div className={`rounded-xl border px-3 py-2.5 text-sm ${tone}`}>
      <div className="flex items-center justify-between">
        <div className="font-semibold">{badge}</div>
        <div className="text-xs opacity-70">
          {job.processed_rows}/{job.total_rows} · {pct.toFixed(0)}%
        </div>
      </div>
      <div className="mt-1.5 h-2 w-full overflow-hidden rounded-full bg-white/60">
        <div
          className="h-full rounded-full bg-current opacity-60 transition-all duration-500 ease-out"
          style={{ width: `${pct}%` }}
        />
      </div>
      <div className="mt-2 text-xs font-medium">
        {/* Phase 36H — hide warnings_total from the primary
            progress summary. On a 10K-row upload the count routinely
            reaches the "20 000 avertissement(s)" range (~2 warnings
            per row for optional mapping fields), which scared
            non-technical users into thinking the import had failed.
            Blocking errors are still surfaced via ``errors_total``. */}
        {t("upload.job.insertedProducts").replace(
          "{n}",
          String(job.inserted_products),
        )}
        {job.errors_total > 0 && (
          <>
            {t("upload.job.errorsSuffix").replace(
              "{n}",
              String(job.errors_total),
            )}
          </>
        )}
      </div>
      {(job.status === "running" || job.status === "queued") && (
        <div className="mt-2 text-xs opacity-80">
          {t("upload.job.keepOpen")}
        </div>
      )}
      {transient && (
        <div className="mt-2 text-xs opacity-90">{transient}</div>
      )}
      {job.error_message && (
        <div className="mt-2 text-xs">
          <strong>{job.error_code ?? t("upload.job.errorLabel")} :</strong>{" "}
          {job.error_message}
        </div>
      )}
      {job.sample_errors.length > 0 && (
        <details className="mt-1">
          <summary className="cursor-pointer text-xs opacity-80 hover:underline">
            {t("upload.job.sampleErrors").replace(
              "{n}",
              String(job.sample_errors.length),
            )}
          </summary>
          <ul className="mt-1 list-disc pl-4 text-xs opacity-80">
            {job.sample_errors.slice(0, 10).map((m, i) => (
              <li key={i} className="break-all">
                {m}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
