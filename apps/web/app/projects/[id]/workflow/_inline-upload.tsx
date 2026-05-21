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
  MappingPreviewResult,
  UploadResult,
} from "@/lib/api";
import { ApiError, createApi } from "@/lib/api";

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

  async function submit() {
    if (!file) return;
    setSubmitting(true);
    setError(null);
    try {
      // The server treats values that match canonical_field as no-ops; we
      // only need to send entries where the user picked something
      // different (or chose to ignore).
      const columnMapping: Record<string, string> = {};
      for (const [k, v] of Object.entries(overrides)) {
        if (v && v !== "__none__") columnMapping[k] = v;
      }
      await api.uploadCsv(
        projectId,
        file,
        Object.keys(columnMapping).length > 0 ? columnMapping : undefined,
      );
      await onUploaded();
      setFile(null);
      setPreview(null);
      setOverrides({});
    } catch (e) {
      if (e instanceof ApiError && typeof e.detail === "object" && e.detail) {
        const d = e.detail as { message?: string };
        setError(d.message ?? `${e.status} ${e}`);
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
                Champs Protein Tracker requis encore manquants :{" "}
                {preview.missing_required_pt.join(", ")}
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

            <div className="flex flex-wrap gap-2 pt-1">
              <Button
                onClick={() => void submit()}
                disabled={submitting || !productNameMapped}
              >
                {submitting ? "Import en cours…" : "Importer le fichier"}
              </Button>
              <Button
                variant="ghost"
                onClick={() => {
                  setFile(null);
                  setPreview(null);
                  setOverrides({});
                  setShowMapping(false);
                }}
                disabled={submitting}
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
