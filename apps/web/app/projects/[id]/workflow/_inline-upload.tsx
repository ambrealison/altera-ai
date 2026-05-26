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

import { useState } from "react";

import { Button, Card, Pill } from "@/components/ui";
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

const CANONICAL_FIELD_LABELS_FR: Record<string, string> = {
  external_product_id: "Identifiant produit / SKU",
  product_name: "Nom du produit",
  brand: "Marque",
  retailer_category: "Catégorie retailer",
  retailer_subcategory: "Sous-catégorie retailer",
  weight_per_item_kg: "Poids unitaire (kg)",
  weight_per_item_g: "Poids unitaire (g)",
  items_purchased: "Volume / nombre d’unités (achats)",
  protein_pct: "Protéines totales (%)",
  plant_protein_pct: "Protéines végétales (%)",
  animal_protein_pct: "Protéines animales (%)",
  ingredients_text: "Ingrédients",
  is_own_brand: "Marque propre ?",
  ean: "EAN / code-barres",
  labels: "Labels",
  country: "Pays",
  language: "Langue",
  reporting_period: "Période de reporting",
  items_sold: "Volume / nombre d’unités (ventes)",
  retail_channel: "Canal de distribution",
};

function labelFor(field: string): string {
  return CANONICAL_FIELD_LABELS_FR[field] ?? field;
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
    reader.onerror = () => reject(new Error("Lecture des en-têtes impossible"));
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
  if (confidence === "exact") return <Pill tone="ok">exact</Pill>;
  if (confidence === "synonym") return <Pill tone="warn">synonyme</Pill>;
  return <Pill tone="neutral">à mapper</Pill>;
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
    <div className="mt-3 overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-gray-200 text-left text-gray-500 uppercase tracking-wider">
            <th className="pb-2 pr-3 font-medium">Colonne CSV</th>
            <th className="pb-2 pr-3 font-medium">Mapper vers</th>
            <th className="pb-2 font-medium">Détection</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {entries.map((entry) => {
            const current =
              overrides[entry.normalised_header] ??
              entry.canonical_field ??
              "__none__";
            return (
              <tr key={entry.normalised_header}>
                <td className="py-2 pr-3 font-mono text-gray-800 align-middle">
                  {entry.raw_header}
                </td>
                <td className="py-2 pr-3 align-middle">
                  <select
                    value={current}
                    onChange={(e) =>
                      onChange(entry.normalised_header, e.target.value)
                    }
                    className="rounded border border-gray-300 bg-white px-2 py-1 text-xs text-gray-800 focus:border-brand-500 focus:outline-none"
                  >
                    <option value="__none__">— Ignorer / tel quel —</option>
                    <option value="ignore">Ignorer cette colonne</option>
                    {CANONICAL_FIELDS.map((f) => (
                      <option key={f} value={f}>
                        {labelFor(f)}
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
    } catch (e) {
      setError(
        e instanceof Error
          ? e.message
          : "Impossible de lire le fichier ou de calculer le mapping.",
      );
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
        setTransientError(
          "Connexion temporairement interrompue. Nouvelle tentative…",
        );
        if (consecutiveFailures >= 5) {
          setError(
            "Trop d'échecs réseau consécutifs. Cliquez sur Réessayer pour reprendre l'import.",
          );
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
        // Phase 34Y — map known ingestion error codes to friendly French.
        const friendly =
          d.error_code === "invalid_csv"
            ? `Fichier CSV invalide : ${d.message ?? "vérifier l'encodage / le format"}`
            : d.error_code === "invalid_mapping"
            ? `Mapping invalide : ${d.message ?? "vérifier les correspondances"}`
            : d.error_code === "ingestion_create_failed"
            ? "Le serveur n'a pas pu créer la tâche d'import. Réessayez."
            : d.error_code === "ingestion_advance_failed"
            ? "Le serveur a rencontré une erreur pendant l'import. Réessayez."
            : d.error_code === "ingestion_job_not_found"
            ? "Tâche d'import introuvable — le serveur a peut-être redémarré. Re-cliquez Importer."
            : d.message;
        setError(friendly ?? `${e.status} ${e}`);
      } else if (e instanceof Error && e.message.includes("Failed to fetch")) {
        setError(
          "Impossible de joindre le serveur. Vérifiez votre connexion puis réessayez.",
        );
      } else {
        setError(e instanceof Error ? e.message : "Échec de l’import.");
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
  const weightMapped =
    mappedFields.has("weight_per_item_kg") ||
    mappedFields.has("weight_per_item_g");

  return (
    <div className="space-y-4">
      {/* Already-imported file summary */}
      {latestUpload && !file && (
        <Card>
          <div className="flex items-start justify-between">
            <div>
              <p className="text-sm font-medium text-gray-800">
                {latestUpload.original_filename}
              </p>
              <p className="mt-0.5 text-xs text-gray-500">
                {latestUpload.products_count} produit(s) ·{" "}
                {latestUpload.row_count ?? "?"} ligne(s)
              </p>
              {latestUpload.warnings.length > 0 && (
                <p className="mt-1 text-xs text-amber-700">
                  {latestUpload.warnings.length} avertissement(s) à l’import.
                </p>
              )}
            </div>
            <Pill tone="ok">Importé</Pill>
          </div>
        </Card>
      )}

      <Card>
        <label className="block">
          <span className="text-sm font-medium text-gray-700">
            {latestUpload ? "Remplacer le fichier" : "Choisir un fichier CSV"}
          </span>
          <span className="mt-0.5 block text-xs text-gray-500">
            Format CSV UTF-8 ; la première ligne doit contenir les en-têtes.
            Les CSV éparses (nom + poids + volume) sont supportées —
            l’identifiant produit est généré si absent.
          </span>
          <input
            type="file"
            accept=".csv,text/csv"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) void pickFile(f);
            }}
            className="mt-2 block w-full text-sm text-gray-600 file:mr-3 file:rounded-md file:border-0 file:bg-brand-50 file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-brand-700 hover:file:bg-brand-100"
          />
        </label>

        {previewing && (
          <p className="mt-3 text-xs text-gray-500">Analyse du fichier…</p>
        )}

        {error && (
          <div className="mt-3 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">
            {error}
          </div>
        )}

        {preview && file && (
          <div className="mt-4 space-y-3">
            <div className="rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-xs text-gray-700">
              <p>
                <span className="font-medium">{file.name}</span> ·{" "}
                {preview.entries.length} colonne(s) détectée(s) ·{" "}
                {
                  preview.entries.filter((e) => e.confidence === "exact").length
                }{" "}
                auto-mappée(s)
              </p>
              <ul className="mt-1 space-y-0.5">
                <li
                  className={
                    productNameMapped ? "text-emerald-700" : "text-rose-700"
                  }
                >
                  {productNameMapped ? "✓" : "✗"} Nom du produit mappé
                </li>
                <li
                  className={
                    weightMapped ? "text-emerald-700" : "text-amber-700"
                  }
                >
                  {weightMapped ? "✓" : "○"} Poids unitaire mappé
                  {!weightMapped && " (optionnel pour Protein Tracker)"}
                </li>
              </ul>
            </div>

            {preview.missing_required_pt.length > 0 && (
              <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                <div className="font-medium">
                  Champs Protein Tracker requis encore manquants :{" "}
                  {preview.missing_required_pt.join(", ")}
                </div>
                <div className="mt-1 text-amber-700">
                  Sans ces champs, les lignes seront importées mais sans
                  bloc Protein Tracker (avertissements par ligne).
                </div>
              </div>
            )}
            {preview.missing_required_wwf.length > 0 && (
              <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                <div className="font-medium">
                  Champs WWF requis encore manquants :{" "}
                  {preview.missing_required_wwf.join(", ")}
                </div>
                <div className="mt-1 text-amber-700">
                  Ces champs sont nécessaires pour calculer les volumes
                  WWF par groupe alimentaire. Sans eux, les lignes
                  seront importées mais sans bloc WWF.
                </div>
              </div>
            )}

            {!showMapping ? (
              <button
                type="button"
                onClick={() => setShowMapping(true)}
                className="text-xs text-brand-600 hover:underline"
              >
                Voir / modifier le mapping détaillé →
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
                  ? `Import en cours… (${job.processed_rows}/${job.total_rows})`
                  : submitting
                  ? "Import en cours…"
                  : "Importer le fichier"}
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
                Annuler
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
  const pct = Math.max(0, Math.min(100, Math.round(job.progress_pct)));
  const tone =
    job.status === "completed"
      ? "border-emerald-200 bg-emerald-50 text-emerald-900"
      : job.status === "completed_with_errors"
      ? "border-amber-200 bg-amber-50 text-amber-900"
      : job.status === "failed"
      ? "border-rose-200 bg-rose-50 text-rose-900"
      : job.status === "cancelled"
      ? "border-gray-200 bg-gray-50 text-gray-700"
      : "border-brand-200 bg-brand-50 text-brand-900";
  const badge =
    job.status === "queued"
      ? "En file d'attente"
      : job.status === "running"
      ? "Import en cours…"
      : job.status === "completed"
      ? "Import terminé"
      : job.status === "completed_with_errors"
      ? "Terminé avec erreurs"
      : job.status === "failed"
      ? "Échec"
      : "Annulé";
  return (
    <div className={`rounded-md border px-3 py-2 text-sm ${tone}`}>
      <div className="flex items-center justify-between">
        <div className="font-medium">{badge}</div>
        <div className="text-xs opacity-70">
          {job.processed_rows}/{job.total_rows} · {pct.toFixed(0)}%
        </div>
      </div>
      <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-white/60">
        <div
          className="h-full rounded-full bg-current opacity-60 transition-all"
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
        {job.inserted_products} produit(s) insérés
        {job.errors_total > 0 && <> · {job.errors_total} erreur(s)</>}
      </div>
      {(job.status === "running" || job.status === "queued") && (
        <div className="mt-2 text-xs opacity-80">
          {"Vous pouvez laisser cette page ouverte — la progression est sauvegardée côté serveur."}
        </div>
      )}
      {transient && (
        <div className="mt-2 text-xs opacity-90">{transient}</div>
      )}
      {job.error_message && (
        <div className="mt-2 text-xs">
          <strong>{job.error_code ?? "Erreur"} :</strong> {job.error_message}
        </div>
      )}
      {job.sample_errors.length > 0 && (
        <details className="mt-1">
          <summary className="cursor-pointer text-xs opacity-80 hover:underline">
            Voir un échantillon des erreurs ({job.sample_errors.length})
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
