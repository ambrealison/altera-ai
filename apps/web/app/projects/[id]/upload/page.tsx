"use client";

import { useEffect, useMemo, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { Button, Card, CardHeader, Field, Pill } from "@/components/ui";
import { useAuth } from "@/lib/auth-context";
import { useT } from "@/lib/i18n";
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

// Phase 33J — French-first canonical-field labels. The API still uses
// snake_case internally; this map is purely for display in the mapping
// dropdown so retailers can recognise the fields. Order matches the
// natural reading flow (identity → product info → quantities →
// nutrition → metadata).
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

const REQUIRED_PT_CANONICAL = new Set([
  "product_name",
  "items_purchased",
]);
const WEIGHT_VARIANTS = new Set(["weight_per_item_kg", "weight_per_item_g"]);
const REQUIRED_WWF_CANONICAL = new Set([
  "external_product_id",
  "product_name",
  "items_sold",
  "is_own_brand",
  "retail_channel",
]);

function labelFor(field: string, t: (key: string) => string): string {
  const key = CANONICAL_FIELD_LABEL_KEYS[field];
  return key ? t(key) : field;
}

// Sentinel error message thrown by ``parseHeadersFromFile`` (a
// module-level helper with no access to ``useT``). The page catch
// detects it and surfaces a translated message.
const UPLOAD_PARSE_HEADERS_ERROR = "Could not read file headers";

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
    reader.onerror = () => reject(new Error(UPLOAD_PARSE_HEADERS_ERROR));
    reader.readAsText(file.slice(0, 8192));
  });
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function ConfidenceBadge({ confidence }: { confidence: ColumnMappingEntry["confidence"] }) {
  const t = useT();
  if (confidence === "exact")
    return <Pill tone="ok">exact</Pill>;
  if (confidence === "synonym")
    return <Pill tone="warn">{t("upload.confidence.synonymEn")}</Pill>;
  return <Pill tone="neutral">{t("upload.confidence.unmatchedEn")}</Pill>;
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
    <div className="mt-4 overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-gray-200 text-left text-ink-soft uppercase tracking-wider">
            <th className="pb-2 pr-4 font-medium">{t("upload.tableStd.csvHeader")}</th>
            <th className="pb-2 pr-4 font-medium">{t("upload.tableStd.mapToField")}</th>
            <th className="pb-2 font-medium">{t("upload.tableStd.confidence")}</th>
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
                    <span className="ml-1.5 text-blue-600 text-xs">{t("upload.tableStd.enrichable")}</span>
                  )}
                </td>
                <td className="py-2 pr-4 align-middle">
                  <select
                    value={current}
                    onChange={(e) => onChange(entry.normalised_header, e.target.value)}
                    className="rounded border border-gray-300 bg-white px-2 py-1 text-xs text-gray-800 focus:border-brand-500 focus:outline-none"
                  >
                    <option value="__none__">{t("upload.tableStd.optionNone")}</option>
                    <option value="ignore">{t("upload.tableStd.optionIgnore")}</option>
                    {CANONICAL_FIELDS.map((f) => (
                      <option key={f} value={f}>
                        {labelFor(f, t)}
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
  const t = useT();
  const queued = (result.queued_for_review as number) ?? 0;
  const aiAttempted = (result.ai_attempted as number) ?? 0;
  const aiAccepted = (result.ai_accepted as number) ?? 0;
  const aiReview = (result.ai_review as number) ?? 0;
  const hasAi = aiAttempted > 0;
  return (
    <>
      <div className="mt-4 grid grid-cols-2 gap-4 text-sm sm:grid-cols-4">
        <div>
          <div className="text-xs uppercase tracking-wider text-ink-soft">{t("upload.page.rulesMatched")}</div>
          <div className="mt-1 text-lg font-semibold">{result.matched ?? 0}</div>
        </div>
        {hasAi ? (
          <div>
            <div className="text-xs uppercase tracking-wider text-ink-soft">{t("upload.page.aiAccepted")}</div>
            <div className="mt-1 text-lg font-semibold">{aiAccepted}</div>
          </div>
        ) : (
          <div>
            <div className="text-xs uppercase tracking-wider text-ink-soft">{t("upload.page.passThrough")}</div>
            <div className="mt-1 text-lg font-semibold">{result.pass_through ?? 0}</div>
          </div>
        )}
        <div>
          <div className="text-xs uppercase tracking-wider text-ink-soft">{t("upload.page.collisions")}</div>
          <div className="mt-1 text-lg font-semibold">{result.rule_collision ?? 0}</div>
        </div>
        <div>
          <div className="text-xs uppercase tracking-wider text-ink-soft">{t("upload.page.sentToReview")}</div>
          <div className="mt-1 text-lg font-semibold">{queued}</div>
        </div>
      </div>
      {hasAi && (
        <div className="mt-3 rounded-md border border-blue-100 bg-blue-50 px-3 py-2 text-xs text-blue-700">
          {t("upload.page.aiClassifierSummary")
            .replace("{attempted}", String(aiAttempted))
            .replace("{accepted}", String(aiAccepted))
            .replace("{review}", String(aiReview))}
        </div>
      )}
      {queued > 0 && (
        <p className="mt-3 text-sm text-ink-muted">
          {t("upload.page.queuedReview")
            .replace("{n}", String(queued))
            .replace("{s}", queued !== 1 ? "s" : "")}
        </p>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function UploadPage() {
  const t = useT();
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
  const [busyLabel, setBusyLabel] = useState(() => t("upload.page.busy.uploading"));

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
      if (err instanceof Error && err.message === UPLOAD_PARSE_HEADERS_ERROR) {
        setMappingError(t("upload.parse.headersUnreadableEn"));
      } else {
        setMappingError(
          err instanceof Error ? err.message : t("upload.page.previewError"),
        );
      }
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
        setBusyLabel(t("upload.page.busy.preparing"));
        const prep = await api.prepareUpload(projectId, file.name);
        setBusyLabel(t("upload.page.busy.uploadingStorage"));
        await uploadViaStorage(file, prep.signed_url);
        setBusyLabel(t("upload.page.busy.processing"));
        const r = await api.ingestUpload(
          projectId,
          prep.upload_id,
          prep.storage_path,
          file.name,
          columnMapping,
        );
        setResult(r);
      } else {
        setBusyLabel(t("upload.page.busy.uploading"));
        const r = await api.uploadCsv(projectId, file, columnMapping);
        setResult(r);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : t("upload.page.uploadFailed"));
    } finally {
      setBusy(false);
      setBusyLabel(t("upload.page.busy.uploading"));
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
      if (finalJob.status === "failed")
        setError(finalJob.error_message ?? t("upload.page.classificationFailed"));
    } catch (err) {
      setError(err instanceof Error ? err.message : t("upload.page.classificationFailed"));
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
      setWwfStep2Error(err instanceof Error ? err.message : t("upload.page.uploadFailed"));
    } finally {
      setWwfStep2Busy(false);
    }
  }

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------

  const projectMethodologies = project?.methodologies_enabled ?? [];

  // Phase 33J — recompute the missing-required-fields banner from the
  // CURRENT dropdown selections (not the server's initial inference)
  // so the banner updates immediately when the user fixes a mapping.
  // For each entry, the user's override wins; otherwise the inferred
  // canonical_field stands. "ignore" / "__none__" are not selections.
  function effectiveCanonical(entry: ColumnMappingEntry): string | null {
    const chosen = mappingOverrides[entry.normalised_header];
    if (chosen === "ignore" || chosen === "__none__") return null;
    if (chosen) return chosen;
    return entry.canonical_field;
  }
  const mappedCanonicalFields = new Set<string>();
  if (mappingPreview) {
    for (const entry of mappingPreview.entries) {
      const c = effectiveCanonical(entry);
      if (c) mappedCanonicalFields.add(c);
    }
  }
  const weightSatisfied = Array.from(WEIGHT_VARIANTS).some((v) =>
    mappedCanonicalFields.has(v),
  );
  const liveMissingPt = mappingPreview
    ? Array.from(REQUIRED_PT_CANONICAL)
        .filter((f) => !mappedCanonicalFields.has(f))
        .concat(weightSatisfied ? [] : ["weight_per_item_kg"])
    : [];
  const liveMissingWwf = mappingPreview
    ? Array.from(REQUIRED_WWF_CANONICAL)
        .filter((f) => !mappedCanonicalFields.has(f))
        .concat(weightSatisfied ? [] : ["weight_per_item_kg"])
    : [];
  const hasMissingPt =
    mappingPreview &&
    liveMissingPt.length > 0 &&
    projectMethodologies.includes("protein_tracker");
  const hasMissingWwf =
    mappingPreview &&
    liveMissingWwf.length > 0 &&
    projectMethodologies.includes("wwf");
  const hasDuplicates = mappingPreview && mappingPreview.duplicate_normalised.length > 0;
  // Phase 33J — when no source column maps to external_product_id,
  // surface the auto-generation notice so retailers know Altera will
  // assign internal IDs.
  const noExternalIdMapped =
    mappingPreview && !mappedCanonicalFields.has("external_product_id");
  // Heuristic hint: any header normalised to "poids_unitaire_produit"
  // typically carries grammes; we suggest the (g) variant.
  const gramsHint =
    mappingPreview &&
    mappingPreview.entries.some(
      (e) =>
        e.normalised_header.startsWith("poids_unitaire") &&
        effectiveCanonical(e) === "weight_per_item_kg",
    );

  return (
    <div className="mx-auto max-w-3xl">
      <h1 className="text-2xl font-semibold tracking-tight">{t("upload.page.title")}</h1>
      <p className="mt-1 text-sm text-ink-muted">
        {t("upload.page.subtitle")}
      </p>

      {/* Step 1 — file selection */}
      <div className="mt-8">
        <Card>
          <CardHeader title={t("upload.page.step1Title")} />
          <div className="mt-4 space-y-4">
            <Field label={t("upload.page.fileField")}>
              <input
                type="file"
                accept=".csv,.tsv,.txt,text/csv,text/plain,text/tab-separated-values"
                onChange={onFileChange}
                className="block w-full text-sm"
              />
              {file && (
                <p className="mt-1 text-xs text-ink-soft">
                  {file.name} &mdash; {formatBytes(file.size)}
                </p>
              )}
            </Field>
            {mappingBusy && (
              <p className="text-sm text-ink-soft">{t("upload.page.parsingHeaders")}</p>
            )}
            {mappingError && (
              <div className="rounded-md border border-danger-100 bg-danger-50 px-3 py-2 text-sm text-danger-700">
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
              title={t("upload.page.step1bTitle")}
              subtitle={t("upload.page.step1bSubtitle")}
            />

            {hasDuplicates && (
              <div className="mt-3 rounded-md border border-warn-100 bg-warn-50 px-3 py-2 text-xs text-warn-700">
                {t("upload.page.duplicates").replace(
                  "{headers}",
                  mappingPreview.duplicate_normalised.join(", "),
                )}
              </div>
            )}

            {(hasMissingPt || hasMissingWwf) && (
              <div className="mt-3 space-y-2">
                {hasMissingPt && (
                  <div className="rounded-md border border-rose-100 bg-rose-50 px-3 py-2 text-xs text-danger-700">
                    <div className="font-medium">
                      {t("upload.page.missingPtTitle").replace(
                        "{fields}",
                        liveMissingPt.map((f) => labelFor(f, t)).join(", "),
                      )}
                    </div>
                    <div className="mt-1 text-rose-600">
                      {t("upload.page.missingPtBody")}
                    </div>
                  </div>
                )}
                {hasMissingWwf && (
                  <div className="rounded-md border border-rose-100 bg-rose-50 px-3 py-2 text-xs text-danger-700">
                    <div className="font-medium">
                      {t("upload.page.missingWwfTitle").replace(
                        "{fields}",
                        liveMissingWwf.map((f) => labelFor(f, t)).join(", "),
                      )}
                    </div>
                    <div className="mt-1 text-rose-600">
                      {t("upload.page.missingWwfBody")}
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* Phase 33J — auto-ID + scale hint notices (info, not blocking). */}
            {noExternalIdMapped && (
              <div className="mt-3 rounded-md border border-blue-100 bg-blue-50 px-3 py-2 text-xs text-blue-800">
                {t("upload.page.noExternalId")}
                <code>AUTO-</code>
                {t("upload.page.noExternalIdSuffix")}
              </div>
            )}
            {gramsHint && (
              <div className="mt-3 rounded-md border border-warn-100 bg-warn-50 px-3 py-2 text-xs text-warn-700">
                {t("upload.page.gramsHintPrefix")}
                <strong> {labelFor("weight_per_item_g", t)}</strong>
                {t("upload.page.gramsHintInsteadOf")}
                <strong> {labelFor("weight_per_item_kg", t)}</strong>
                {t("upload.page.gramsHintSuffix")}
              </div>
            )}

            <MappingTable
              entries={mappingPreview.entries}
              overrides={mappingOverrides}
              onChange={onMappingChange}
            />

            <div className="mt-4">
              <Button onClick={onUpload} disabled={busy}>
                {busy ? busyLabel : t("upload.page.uploadWithMapping")}
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
              title={t("upload.page.step2Title")}
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
              <div className="mt-3 rounded-md border border-warn-100 bg-warn-50 px-3 py-2 text-sm text-warn-700">
                {t("upload.page.duplicateOf")}
              </div>
            )}
            <dl className="mt-4 grid grid-cols-2 gap-4 text-sm">
              <div>
                <dt className="text-xs uppercase tracking-wider text-ink-soft">{t("upload.page.rows")}</dt>
                <dd className="mt-1 font-medium">{result.row_count}</dd>
              </div>
              <div>
                <dt className="text-xs uppercase tracking-wider text-ink-soft">{t("upload.page.products")}</dt>
                <dd className="mt-1 font-medium">{result.products_count}</dd>
              </div>
              <div>
                <dt className="text-xs uppercase tracking-wider text-ink-soft">{t("upload.page.errors")}</dt>
                <dd className="mt-1 font-medium">{result.errors.length}</dd>
              </div>
              <div>
                <dt className="text-xs uppercase tracking-wider text-ink-soft">{t("upload.page.warnings")}</dt>
                <dd className="mt-1 font-medium">{result.warnings.length}</dd>
              </div>
              {result.file_size_bytes != null && (
                <div>
                  <dt className="text-xs uppercase tracking-wider text-ink-soft">{t("upload.page.fileSize")}</dt>
                  <dd className="mt-1 font-medium">{formatBytes(result.file_size_bytes)}</dd>
                </div>
              )}
            </dl>
            {result.dropped_columns.length > 0 && (
              <div className="mt-4">
                <div className="text-xs font-medium uppercase tracking-wider text-ink-soft">
                  {t("upload.page.droppedColumns")}
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
                <summary className="cursor-pointer text-sm font-medium text-danger-700">
                  {t("upload.page.errorsCount").replace(
                    "{n}",
                    String(result.errors.length),
                  )}
                </summary>
                <p className="mt-2 text-xs text-danger-700">
                  {t("upload.page.errorsHint")}
                </p>
                <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-danger-700">
                  {result.errors.slice(0, 20).map((e, i) => (
                    <li key={i}>
                      {t("upload.page.errorRow")
                        .replace("{row}", String(e.row_number))
                        .replace("{code}", e.code)
                        .replace("{message}", e.message)}
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
              title={t("upload.page.step3Title")}
              subtitle={t("upload.page.step3Subtitle")}
            />
            <div className="mt-4 flex gap-2">
              <Button
                onClick={() => onClassify("protein_tracker")}
                disabled={classifyBusy !== null}
              >
                {classifyBusy === "protein_tracker" ? t("upload.page.classifying") : t("upload.page.classifyPt")}
              </Button>
              <Button
                variant="secondary"
                onClick={() => onClassify("wwf")}
                disabled={classifyBusy !== null}
              >
                {classifyBusy === "wwf" ? t("upload.page.classifying") : t("upload.page.classifyWwf")}
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
                  <span className="text-xs text-ink-soft">{t("upload.page.jobLabel").replace("{id}", classifyJob.job_id.slice(0, 8))}</span>
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
              title={t("upload.page.step4Title")}
              subtitle={t("upload.page.step4Subtitle")}
            />
            <p className="mt-3 text-sm text-ink-muted">
              {t("upload.page.step4Body")}
            </p>
            <form onSubmit={onWwfStep2Upload} className="mt-4 space-y-4">
              <Field label={t("upload.page.step4Field")}>
                <input
                  type="file"
                  accept=".json,application/json"
                  onChange={(e) => setWwfStep2File(e.target.files?.[0] ?? null)}
                  className="block w-full text-sm"
                />
                {wwfStep2File && (
                  <p className="mt-1 text-xs text-ink-soft">
                    {wwfStep2File.name} &mdash; {formatBytes(wwfStep2File.size)}
                  </p>
                )}
              </Field>
              <Button type="submit" disabled={!wwfStep2File || wwfStep2Busy} variant="secondary">
                {wwfStep2Busy ? t("upload.page.busy.uploading") : t("upload.page.uploadIngredients")}
              </Button>
            </form>
            {wwfStep2Error && (
              <div className="mt-3 rounded-md border border-danger-100 bg-danger-50 px-3 py-2 text-sm text-danger-700">
                {wwfStep2Error}
              </div>
            )}
            {wwfStep2Result && (
              <div className="mt-4 space-y-3">
                <div className="flex items-center gap-2">
                  <Pill tone={wwfStep2Result.stored ? "ok" : wwfStep2Result.error_count > 0 ? "error" : "warn"}>
                    {wwfStep2Result.stored ? t("upload.page.stored") : t("upload.page.notStored")}
                  </Pill>
                  {wwfStep2Result.stored && (
                    <span className="text-xs text-ink-soft">
                      {(wwfStep2Result.replaced
                        ? t("upload.page.ingredientsSavedReplaced")
                        : t("upload.page.ingredientsSaved")
                      )
                        .replace("{n}", String(wwfStep2Result.valid_product_count))
                        .replace("{s}", wwfStep2Result.valid_product_count !== 1 ? "s" : "")}
                    </span>
                  )}
                </div>
                {wwfStep2Result.stored && (
                  <div className="rounded-md border border-blue-100 bg-blue-50 px-3 py-2 text-xs text-blue-700">
                    {t("upload.page.rerunCalculation")}
                  </div>
                )}
                <div className="grid grid-cols-2 gap-4 text-sm sm:grid-cols-4">
                  <div>
                    <div className="text-xs uppercase tracking-wider text-ink-soft">{t("upload.page.productsInFile")}</div>
                    <div className="mt-1 text-lg font-semibold">{wwfStep2Result.total_products_in_file}</div>
                  </div>
                  <div>
                    <div className="text-xs uppercase tracking-wider text-ink-soft">{t("upload.page.ownBrandStored")}</div>
                    <div className="mt-1 text-lg font-semibold">{wwfStep2Result.valid_product_count}</div>
                  </div>
                  <div>
                    <div className="text-xs uppercase tracking-wider text-ink-soft">{t("upload.page.errors")}</div>
                    <div className="mt-1 text-lg font-semibold">{wwfStep2Result.error_count}</div>
                  </div>
                  <div>
                    <div className="text-xs uppercase tracking-wider text-ink-soft">{t("upload.page.warnings")}</div>
                    <div className="mt-1 text-lg font-semibold">{wwfStep2Result.warning_count}</div>
                  </div>
                </div>
                {(wwfStep2Result.unknown_product_count > 0 || wwfStep2Result.branded_composite_count > 0) && (
                  <div className="rounded-md border border-warn-100 bg-warn-50 px-3 py-2 text-xs text-warn-700">
                    {wwfStep2Result.unknown_product_count > 0 && (
                      <div>{t("upload.page.unknownProducts").replace("{n}", String(wwfStep2Result.unknown_product_count))}</div>
                    )}
                    {wwfStep2Result.branded_composite_count > 0 && (
                      <div>
                        {t("upload.page.brandedComposites").replace("{n}", String(wwfStep2Result.branded_composite_count))}
                      </div>
                    )}
                  </div>
                )}
                {wwfStep2Result.product_results.some((r) => r.errors.length > 0) && (
                  <details>
                    <summary className="cursor-pointer text-sm font-medium text-danger-700">
                      {t("upload.page.validationErrors")}
                    </summary>
                    <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-danger-700">
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
        <div className="mt-4 rounded-md border border-danger-100 bg-danger-50 px-3 py-2 text-sm text-danger-700">
          {error}
        </div>
      )}

      <div className="mt-8 flex gap-2">
        <Button variant="ghost" onClick={() => router.push(`/projects/${projectId}`)}>
          {t("upload.page.backToProject")}
        </Button>
        {classifyJob?.status === "succeeded" && (classifyJob.result?.queued_for_review ?? 0) > 0 && (
          <Button onClick={() => router.push(`/projects/${projectId}/review`)}>
            {t("upload.page.reviewQueue").replace("{n}", String(classifyJob.result!.queued_for_review))}
          </Button>
        )}
        {classifyJob?.status === "succeeded" && (classifyJob.result?.queued_for_review ?? 1) === 0 && (
          <Button onClick={() => router.push(`/projects/${projectId}/runs`)}>
            {t("upload.page.calculate")}
          </Button>
        )}
      </div>
    </div>
  );
}
