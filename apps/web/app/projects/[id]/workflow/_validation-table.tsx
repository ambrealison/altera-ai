"use client";

/**
 * Phase 34F — Inline category validation table for wizard Step 5.
 *
 * Shows every product's assigned Protein Tracker (and optionally WWF)
 * category, the source that decided it (deterministic / AI / manual),
 * confidence, and current review status. Scales to 10k–15k rows by:
 *
 * - server-side pagination (50 rows per page by default);
 * - server-side filtering (source, pt_group, confidence range,
 *   review_status, product_search);
 * - aggregate counters from the server so the wizard does not have to
 *   fetch every page just to render the by-source / by-group summary.
 *
 * Privacy: only non-commercial fields are surfaced (product name,
 * brand, retailer_category/subcategory). Volume / weight / prices /
 * margins are NOT in the row payload by API design.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";

import { Button, Card, Pill } from "@/components/ui";
import type {
  ClassificationRow,
  ClassificationsFilters,
  ClassificationsResponse,
  Methodology,
  ProteinTrackerGroup,
} from "@/lib/api";
import { ApiError, createApi } from "@/lib/api";

import { WwfCorrectionModal } from "./_wwf-correction-modal";

// Phase WWF-I — French labels for the WWF subgroups + composite buckets
// shown in the WWF validation view. Falls back to the raw enum value
// when a label isn't defined (keeps the table readable even if the
// backend adds a new subgroup before the frontend ships the label).

const WWF_FOOD_GROUP_LABELS_FR: Record<string, string> = {
  FG1: "FG1 · Aliments protéiques",
  FG2: "FG2 · Lait & alternatives",
  FG3: "FG3 · Matières grasses",
  FG4: "FG4 · Fruits & légumes",
  FG5: "FG5 · Céréales",
  FG6: "FG6 · Tubercules / féculents",
  FG7: "FG7 · Snacks (sucre/sel/gras)",
  out_of_scope: "Hors périmètre",
  unknown: "Inconnu",
};

// Phase WWF-J — French labels for the WWF subgroups so the validation
// table shows readable names instead of the raw enum value (the brief
// section E explicitly lists this mapping).
const WWF_SUBGROUP_LABELS_FR: Record<string, string> = {
  // FG1
  red_meat: "Viande rouge",
  poultry: "Volaille",
  processed_meats_alternatives: "Viandes transformées / alternatives",
  seafood: "Poisson & fruits de mer",
  eggs: "Œufs",
  legumes: "Légumineuses",
  nuts_seeds: "Noix & graines",
  alternative_protein_sources: "Sources protéiques alternatives",
  meat_egg_seafood_alternatives: "Alternatives viande/œuf/poisson",
  // FG2
  cheese: "Fromage",
  other_dairy_animal: "Autres produits laitiers",
  dairy_alternative_plant: "Alternatives végétales aux produits laitiers",
  // FG3
  plant_based_fat: "Matières grasses végétales",
  animal_based_fat: "Matières grasses animales",
  // FG5
  whole_grain: "Céréales complètes",
  refined_grain: "Céréales raffinées",
  // FG7
  plant_based_snack: "Snack végétal",
  animal_based_snack: "Snack animal",
};

function wwfSubgroupLabel(v: string | null): string {
  if (!v) return "—";
  return WWF_SUBGROUP_LABELS_FR[v] ?? v;
}

const WWF_FOOD_GROUP_TONE: Record<string, "ok" | "warn" | "neutral" | "brand"> = {
  FG1: "brand",
  FG2: "ok",
  FG3: "ok",
  FG4: "ok",
  FG5: "ok",
  FG6: "ok",
  FG7: "warn",
  out_of_scope: "neutral",
  unknown: "neutral",
};

const WWF_BUCKET_LABELS_FR: Record<string, string> = {
  meat_based: "À base de viande",
  seafood_based: "À base de poisson/fruits de mer",
  vegetarian: "Végétarien",
  vegan: "Végane",
};

function wwfFoodGroupLabel(fg: string | null | undefined): string {
  if (!fg) return "—";
  return WWF_FOOD_GROUP_LABELS_FR[fg] ?? fg;
}

function wwfBucketLabel(b: string | null | undefined): string {
  if (!b) return "—";
  return WWF_BUCKET_LABELS_FR[b] ?? b;
}

function wwfSubgroupOf(row: ClassificationRow): string | null {
  // Pick whichever subgroup matches the current food group; only one
  // ever applies per row (Pydantic invariant on the backend).
  return (
    row.wwf_fg1_subgroup ??
    row.wwf_fg2_subgroup ??
    row.wwf_fg3_subgroup ??
    row.wwf_fg5_grain_kind ??
    row.wwf_fg7_snack_kind ??
    null
  );
}

const PAGE_SIZE = 50;

const PT_GROUP_LABELS_FR: Record<ProteinTrackerGroup, string> = {
  plant_based_core: "Végétal — cœur",
  plant_based_non_core: "Végétal — hors cœur",
  composite_products: "Composite",
  animal_core: "Animal — cœur",
  out_of_scope: "Hors périmètre",
  unknown: "Inconnu",
};

const PT_GROUP_TONE: Record<
  ProteinTrackerGroup,
  "ok" | "warn" | "neutral" | "brand"
> = {
  plant_based_core: "ok",
  plant_based_non_core: "ok",
  composite_products: "warn",
  animal_core: "brand",
  out_of_scope: "neutral",
  unknown: "neutral",
};

const PT_GROUP_OPTIONS: ProteinTrackerGroup[] = [
  "plant_based_core",
  "plant_based_non_core",
  "composite_products",
  "animal_core",
  "out_of_scope",
];

const SOURCE_LABELS_FR: Record<string, string> = {
  deterministic: "Déterministe",
  ai: "IA",
  manual_review: "Manuel",
  unknown: "Aucune",
};

function ptGroupLabel(g: ProteinTrackerGroup | null): string {
  if (!g) return "Aucune";
  return PT_GROUP_LABELS_FR[g] ?? g;
}

function sourceLabel(s: string | null): string {
  if (!s) return "Aucune";
  return SOURCE_LABELS_FR[s] ?? s;
}

function confidenceText(c: number | null): string {
  if (c == null) return "—";
  return `${Math.round(c * 100)} %`;
}

export function ValidationTable({
  projectId,
  accessToken,
  wwfEnabled,
  ptEnabled = true,
  onChanged,
}: {
  projectId: string;
  accessToken: string | null;
  wwfEnabled: boolean;
  /** Phase WWF-I — when false (WWF-only project), the table hides the
   *  PT-only filters/columns and defaults the view to WWF. */
  ptEnabled?: boolean;
  onChanged?: () => void | Promise<void>;
}) {
  const api = useMemo(() => createApi(accessToken), [accessToken]);

  // Phase WWF-I — read ?methodology=wwf | protein_tracker from the
  // URL to preselect the validation view. The WWF dual-classification
  // panel (Phase WWF-H) stamps this query param when the user clicks
  // "Voir la validation WWF" / "Voir la validation Protein Tracker".
  const searchParams = useSearchParams();
  const queryMethodology = searchParams?.get("methodology") ?? null;

  /** "wwf" → render the WWF view (food group / subgroup / composite /
   *  bucket columns). "protein_tracker" → render the existing PT view.
   *  Defaults: WWF-only projects open in WWF; PT-only and PT+WWF open
   *  in PT unless the query param overrides. */
  const initialView: Methodology = (() => {
    if (queryMethodology === "wwf" && wwfEnabled) return "wwf";
    if (queryMethodology === "protein_tracker" && ptEnabled) {
      return "protein_tracker";
    }
    if (wwfEnabled && !ptEnabled) return "wwf";
    return "protein_tracker";
  })();
  const [methodologyView, setMethodologyView] = useState<Methodology>(
    initialView,
  );
  // Keep the URL in sync when the user manually switches view so a page
  // reload / link copy preserves intent.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const url = new URL(window.location.href);
    if (url.searchParams.get("methodology") !== methodologyView) {
      url.searchParams.set("methodology", methodologyView);
      window.history.replaceState({}, "", url.toString());
    }
  }, [methodologyView]);

  // Phase WWF-P — top-level view toggle. ``products`` is the default;
  // ``review`` opens "À valider" — one row per (product, methodology)
  // review item. URL param ``?view=review`` preselects the toggle.
  const queryView = searchParams?.get("view") ?? null;
  const initialTableView: "products" | "review" =
    queryView === "review" ? "review" : "products";
  const [tableView, setTableView] =
    useState<"products" | "review">(initialTableView);
  useEffect(() => {
    if (typeof window === "undefined") return;
    const url = new URL(window.location.href);
    if (url.searchParams.get("view") !== tableView) {
      url.searchParams.set("view", tableView);
      window.history.replaceState({}, "", url.toString());
    }
  }, [tableView]);

  const [filters, setFilters] = useState<ClassificationsFilters>({});
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<ClassificationsResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [submittingId, setSubmittingId] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [overrides, setOverrides] = useState<
    Record<string, ProteinTrackerGroup>
  >({});
  // Phase WWF-P — WWF correction modal state.
  const [wwfModalRow, setWwfModalRow] = useState<ClassificationRow | null>(
    null,
  );

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      const r = await api.listClassifications(projectId, {
        ...filters,
        limit: PAGE_SIZE,
        offset,
        // Phase WWF-P — wire view + methodology filters into the
        // /classifications request.
        view: tableView,
        methodology:
          // In review mode, the methodologyView toggle filters the
          // review queue (PT / WWF). In products mode we don't apply
          // the toggle as a server-side filter — the user uses it to
          // swap the in-table column layout.
          tableView === "review" && ptEnabled && wwfEnabled
            ? methodologyView
            : undefined,
      });
      setData(r);
    } catch (e) {
      setLoadError(
        e instanceof Error ? e.message : "Échec du chargement du tableau.",
      );
    }
  }, [
    api,
    projectId,
    filters,
    offset,
    tableView,
    methodologyView,
    ptEnabled,
    wwfEnabled,
  ]);

  useEffect(() => {
    void load();
  }, [load]);

  async function submit(
    row: ClassificationRow,
    decision: "accepted" | "changed",
  ) {
    setSubmittingId(row.product_id);
    setSubmitError(null);
    try {
      const to = decision === "changed" ? overrides[row.product_id] : undefined;
      if (decision === "changed" && !to) {
        setSubmitError(
          "Choisissez une catégorie avant de changer la classification.",
        );
        setSubmittingId(null);
        return;
      }
      // Phase WWF-N — the decision MUST target the active methodology
      // view, not a hardcoded "protein_tracker". For WWF rows the
      // decision goes through the WWF review queue / classifier.
      const targetMethodology: Methodology = row.methodology
        ? row.methodology
        : methodologyView;
      await api.submitDecision(projectId, row.product_id, targetMethodology, {
        decision,
        to_category: to,
      });
      await load();
      await onChanged?.();
    } catch (e) {
      if (e instanceof ApiError && typeof e.detail === "object" && e.detail) {
        const d = e.detail as { message?: string };
        setSubmitError(d.message ?? String(e));
      } else {
        setSubmitError(
          e instanceof Error ? e.message : "Erreur lors de la décision.",
        );
      }
    } finally {
      setSubmittingId(null);
    }
  }

  function patchFilter(p: Partial<ClassificationsFilters>): void {
    setOffset(0);
    setFilters((prev) => {
      const next: ClassificationsFilters = { ...prev };
      for (const [k, v] of Object.entries(p)) {
        if (v === undefined || v === "") {
          delete next[k as keyof ClassificationsFilters];
        } else {
          (next as Record<string, unknown>)[k] = v;
        }
      }
      return next;
    });
  }

  if (loadError) {
    return (
      <Card>
        <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">
          {loadError}
        </div>
      </Card>
    );
  }
  if (!data) {
    return (
      <Card>
        <p className="text-sm text-gray-500">Chargement du tableau…</p>
      </Card>
    );
  }

  const pageCount = Math.max(1, Math.ceil(data.total / PAGE_SIZE));
  const pageIdx = Math.floor(offset / PAGE_SIZE);

  const isWwfView = methodologyView === "wwf";
  const canToggle = ptEnabled && wwfEnabled;

  return (
    <Card>
      {/* Phase WWF-P — top-level view toggle. Switches between
          "Tous les produits" (one row per product, both methodology
          summaries when available) and "À valider" (one row per
          ``(product, methodology)`` review item — a product needing
          review for both methodologies appears twice). */}
      <div className="mb-3 flex items-center gap-2">
        <span className="text-xs font-medium uppercase tracking-wide text-gray-500">
          Vue :
        </span>
        <div className="inline-flex rounded-md border border-gray-200 bg-white p-0.5">
          <button
            type="button"
            onClick={() => {
              setTableView("products");
              setOffset(0);
            }}
            className={
              "rounded px-2 py-0.5 text-xs font-medium transition " +
              (tableView === "products"
                ? "bg-brand-600 text-white"
                : "text-gray-600 hover:bg-gray-50")
            }
          >
            Tous les produits
          </button>
          <button
            type="button"
            onClick={() => {
              setTableView("review");
              setOffset(0);
            }}
            className={
              "rounded px-2 py-0.5 text-xs font-medium transition " +
              (tableView === "review"
                ? "bg-brand-600 text-white"
                : "text-gray-600 hover:bg-gray-50")
            }
          >
            À valider
          </button>
        </div>
      </div>

      {/* Phase WWF-I — methodology selector (only visible on PT+WWF
          projects). Single-methodology projects skip the toggle. */}
      {canToggle && (
        <div className="mb-3 flex items-center gap-2">
          <span className="text-xs font-medium uppercase tracking-wide text-gray-500">
            Méthodologie :
          </span>
          <div className="inline-flex rounded-md border border-gray-200 bg-white p-0.5">
            <button
              type="button"
              onClick={() => setMethodologyView("protein_tracker")}
              className={
                "rounded px-2 py-0.5 text-xs font-medium transition " +
                (methodologyView === "protein_tracker"
                  ? "bg-brand-600 text-white"
                  : "text-gray-600 hover:bg-gray-50")
              }
            >
              Protein Tracker
            </button>
            <button
              type="button"
              onClick={() => setMethodologyView("wwf")}
              className={
                "rounded px-2 py-0.5 text-xs font-medium transition " +
                (methodologyView === "wwf"
                  ? "bg-brand-600 text-white"
                  : "text-gray-600 hover:bg-gray-50")
              }
            >
              WWF
            </button>
          </div>
        </div>
      )}

      {/* Phase WWF-I — title + subtitle adapt to the active view. */}
      <div className="mb-2">
        <h3 className="text-sm font-semibold text-gray-800">
          {isWwfView ? "Validation WWF" : "Validation Protein Tracker"}
        </h3>
        <p className="mt-0.5 text-xs text-gray-500">
          {isWwfView
            ? "Vérifiez les groupes alimentaires WWF, sous-groupes et produits composites."
            : "Vérifiez les catégories Protein Tracker assignées par les règles déterministes et l'IA."}
        </p>
      </div>

      {/* Aggregate counters */}
      <div className="flex flex-wrap gap-2 text-xs">
        <span className="text-gray-600">
          {data.total} produit(s) affiché(s)
          {!isWwfView && (
            <>
              {" "}· {data.pt_eligible_total} dans le périmètre Protein Tracker
            </>
          )}
        </span>
        {Object.entries(data.counts_by_source).map(([k, v]) => (
          <Pill
            key={k}
            tone={k === "ai" ? "brand" : k === "deterministic" ? "ok" : "neutral"}
          >
            {sourceLabel(k)} : {v}
          </Pill>
        ))}
      </div>

      {/* Filters */}
      <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-4">
        <input
          type="text"
          placeholder="Rechercher (nom / marque)"
          value={filters.product_search ?? ""}
          onChange={(e) => patchFilter({ product_search: e.target.value })}
          className="rounded border border-gray-300 bg-white px-2 py-1 text-xs text-gray-800 focus:border-brand-500 focus:outline-none"
        />
        <select
          value={filters.source ?? ""}
          onChange={(e) =>
            patchFilter({
              source: (e.target.value || undefined) as
                | ClassificationsFilters["source"]
                | undefined,
            })
          }
          className="rounded border border-gray-300 bg-white px-2 py-1 text-xs text-gray-800 focus:border-brand-500 focus:outline-none"
        >
          <option value="">Toutes sources</option>
          <option value="deterministic">Déterministe</option>
          <option value="ai">IA</option>
          <option value="manual_review">Manuel</option>
          <option value="unknown">Non classé</option>
        </select>
        <select
          value={filters.pt_group ?? ""}
          onChange={(e) =>
            patchFilter({
              pt_group: (e.target.value || undefined) as
                | ProteinTrackerGroup
                | undefined,
            })
          }
          className="rounded border border-gray-300 bg-white px-2 py-1 text-xs text-gray-800 focus:border-brand-500 focus:outline-none"
        >
          <option value="">Toutes catégories PT</option>
          {PT_GROUP_OPTIONS.map((g) => (
            <option key={g} value={g}>
              {PT_GROUP_LABELS_FR[g]}
            </option>
          ))}
        </select>
        <select
          value={filters.review_status ?? ""}
          onChange={(e) =>
            patchFilter({
              review_status: (e.target.value || undefined) as
                | ClassificationsFilters["review_status"]
                | undefined,
            })
          }
          className="rounded border border-gray-300 bg-white px-2 py-1 text-xs text-gray-800 focus:border-brand-500 focus:outline-none"
        >
          <option value="">Tous statuts review</option>
          <option value="in_queue">En attente</option>
          <option value="accepted">Acceptée</option>
          <option value="changed">Modifiée</option>
          <option value="deferred">Différée</option>
        </select>
      </div>

      {/* Phase 34I — confidence range filter. Lets the user focus on
          borderline AI classifications (0.60–0.80) or audit anything
          above a high-confidence threshold. */}
      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-gray-600">
        <span>Confiance :</span>
        <label className="flex items-center gap-1">
          min
          <input
            type="number"
            min={0}
            max={1}
            step={0.05}
            value={filters.min_confidence ?? ""}
            onChange={(e) =>
              patchFilter({
                min_confidence:
                  e.target.value === ""
                    ? undefined
                    : Number(e.target.value),
              })
            }
            className="w-16 rounded border border-gray-300 bg-white px-1 py-0.5 text-xs text-gray-800 focus:border-brand-500 focus:outline-none"
          />
        </label>
        <label className="flex items-center gap-1">
          max
          <input
            type="number"
            min={0}
            max={1}
            step={0.05}
            value={filters.max_confidence ?? ""}
            onChange={(e) =>
              patchFilter({
                max_confidence:
                  e.target.value === ""
                    ? undefined
                    : Number(e.target.value),
              })
            }
            className="w-16 rounded border border-gray-300 bg-white px-1 py-0.5 text-xs text-gray-800 focus:border-brand-500 focus:outline-none"
          />
        </label>
        <span className="text-gray-400">·</span>
        {/* One-click presets so the analyst does not have to think in
            numbers. */}
        <button
          type="button"
          onClick={() =>
            patchFilter({ min_confidence: undefined, max_confidence: 0.6 })
          }
          className="rounded border border-rose-200 bg-rose-50 px-1.5 py-0.5 text-rose-700 hover:bg-rose-100"
        >
          &lt; 0.60 (à examiner)
        </button>
        <button
          type="button"
          onClick={() =>
            patchFilter({ min_confidence: 0.6, max_confidence: 0.8 })
          }
          className="rounded border border-amber-200 bg-amber-50 px-1.5 py-0.5 text-amber-700 hover:bg-amber-100"
        >
          0.60–0.80 (à vérifier)
        </button>
        <button
          type="button"
          onClick={() =>
            patchFilter({ min_confidence: 0.8, max_confidence: undefined })
          }
          className="rounded border border-emerald-200 bg-emerald-50 px-1.5 py-0.5 text-emerald-700 hover:bg-emerald-100"
        >
          ≥ 0.80 (auto-accept)
        </button>
        <button
          type="button"
          onClick={() =>
            patchFilter({ min_confidence: undefined, max_confidence: undefined })
          }
          className="rounded border border-gray-200 bg-white px-1.5 py-0.5 text-gray-600 hover:bg-gray-50"
        >
          Tous les produits
        </button>
      </div>

      {submitError && (
        <div className="mt-3 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">
          {submitError}
        </div>
      )}

      {/* Table */}
      <div className="mt-3 overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-gray-200 text-left text-gray-500 uppercase tracking-wider">
              <th className="py-2 pr-3 font-medium">Produit</th>
              <th className="py-2 pr-3 font-medium">Catégorie retailer</th>
              {isWwfView ? (
                <>
                  <th className="py-2 pr-3 font-medium">Groupe WWF</th>
                  <th className="py-2 pr-3 font-medium">Sous-groupe</th>
                  <th className="py-2 pr-3 font-medium">Composite</th>
                </>
              ) : (
                <>
                  <th className="py-2 pr-3 font-medium">PT</th>
                  {wwfEnabled && (
                    <th className="py-2 pr-3 font-medium">WWF</th>
                  )}
                </>
              )}
              <th className="py-2 pr-3 font-medium">Source</th>
              <th className="py-2 pr-3 font-medium">Confiance</th>
              <th className="py-2 pr-3 font-medium">Statut</th>
              <th className="py-2 font-medium">Action</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {data.items.length === 0 && (
              <tr>
                <td
                  colSpan={isWwfView ? 8 : wwfEnabled ? 8 : 7}
                  className="py-4 text-center text-gray-500"
                >
                  Aucun produit ne correspond aux filtres.
                </td>
              </tr>
            )}
            {data.items.map((row) => {
              const busy = submittingId === row.product_id;
              const inQueue = row.review_status === "in_queue";
              const chosen = overrides[row.product_id];
              return (
                <tr
                  key={`${row.product_id}-${row.methodology ?? "all"}`}
                  className="align-top"
                >
                  <td className="py-2 pr-3">
                    <div className="font-medium text-gray-800">
                      {row.product_name}
                    </div>
                    {row.brand && (
                      <div className="text-gray-500">{row.brand}</div>
                    )}
                    {/* Phase WWF-P — methodology badge in review mode
                        so the user sees which methodology this row is
                        about (same product can appear twice). */}
                    {row.methodology && (
                      <div className="mt-1">
                        <Pill
                          tone={
                            row.methodology === "wwf" ? "warn" : "brand"
                          }
                        >
                          {row.methodology === "wwf"
                            ? "WWF"
                            : "Protein Tracker"}
                        </Pill>
                      </div>
                    )}
                  </td>
                  <td className="py-2 pr-3 text-gray-600">
                    {row.retailer_category ?? "—"}
                    {row.retailer_subcategory && (
                      <div className="text-gray-400">
                        {row.retailer_subcategory}
                      </div>
                    )}
                  </td>
                  {isWwfView ? (
                    <>
                      <td className="py-2 pr-3">
                        {row.wwf_food_group ? (
                          <Pill
                            tone={
                              WWF_FOOD_GROUP_TONE[row.wwf_food_group] ??
                              "neutral"
                            }
                          >
                            {wwfFoodGroupLabel(row.wwf_food_group)}
                          </Pill>
                        ) : (
                          <span className="text-gray-400">—</span>
                        )}
                      </td>
                      <td className="py-2 pr-3 text-gray-600">
                        {wwfSubgroupLabel(wwfSubgroupOf(row))}
                      </td>
                      <td className="py-2 pr-3 text-gray-600">
                        {row.wwf_is_composite ? (
                          <span title={row.wwf_composite_step1_bucket ?? ""}>
                            {wwfBucketLabel(
                              row.wwf_composite_step1_bucket ?? null,
                            )}
                          </span>
                        ) : (
                          <span className="text-gray-400">—</span>
                        )}
                      </td>
                    </>
                  ) : (
                    <>
                      <td className="py-2 pr-3">
                        {row.pt_group ? (
                          <Pill tone={PT_GROUP_TONE[row.pt_group]}>
                            {ptGroupLabel(row.pt_group)}
                          </Pill>
                        ) : (
                          <span className="text-gray-400">—</span>
                        )}
                      </td>
                      {wwfEnabled && (
                        <td className="py-2 pr-3 text-gray-600">
                          {row.wwf_food_group ?? "—"}
                        </td>
                      )}
                    </>
                  )}
                  <td className="py-2 pr-3 text-gray-600">
                    {sourceLabel(
                      isWwfView ? row.wwf_source : row.pt_source,
                    )}
                  </td>
                  <td className="py-2 pr-3 text-gray-600">
                    {confidenceText(
                      isWwfView ? row.wwf_confidence : row.pt_confidence,
                    )}
                  </td>
                  <td className="py-2 pr-3">
                    {row.review_status ? (
                      <Pill
                        tone={
                          row.review_status === "accepted" ||
                          row.review_status === "changed"
                            ? "ok"
                            : row.review_status === "in_queue"
                              ? "warn"
                              : "neutral"
                        }
                      >
                        {row.review_status}
                      </Pill>
                    ) : (
                      <span className="text-gray-400">—</span>
                    )}
                  </td>
                  <td className="py-2">
                    {isWwfView ? (
                      /* Phase WWF-P — WWF rows are now editable.
                         "Corriger" opens the WWF correction modal
                         (food group + subgroup + composite + bucket).
                         The accept button confirms the current WWF
                         classification. */
                      <div className="flex flex-wrap items-center gap-1">
                        <Button
                          variant="ghost"
                          onClick={() => setWwfModalRow(row)}
                          disabled={busy}
                        >
                          Corriger
                        </Button>
                        {(row.wwf_review_status === "in_queue" ||
                          row.review_status === "in_queue") &&
                          row.wwf_food_group && (
                            <Button
                              variant="secondary"
                              onClick={() => void submit(row, "accepted")}
                              disabled={busy}
                            >
                              {busy ? "…" : "✓"}
                            </Button>
                          )}
                      </div>
                    ) : (
                      <div className="flex flex-wrap items-center gap-1">
                        <select
                          value={chosen ?? ""}
                          onChange={(e) =>
                            setOverrides((prev) => ({
                              ...prev,
                              [row.product_id]: e.target
                                .value as ProteinTrackerGroup,
                            }))
                          }
                          disabled={busy}
                          className="rounded border border-gray-300 bg-white px-1.5 py-0.5 text-xs text-gray-800 focus:border-brand-500 focus:outline-none"
                        >
                          <option value="">Changer…</option>
                          {PT_GROUP_OPTIONS.map((g) => (
                            <option key={g} value={g}>
                              {PT_GROUP_LABELS_FR[g]}
                            </option>
                          ))}
                        </select>
                        <Button
                          variant="ghost"
                          onClick={() => void submit(row, "changed")}
                          disabled={busy || !chosen}
                        >
                          {busy ? "…" : "✎"}
                        </Button>
                        {inQueue && row.pt_group && (
                          <Button
                            variant="secondary"
                            onClick={() => void submit(row, "accepted")}
                            disabled={busy}
                          >
                            {busy ? "…" : "✓"}
                          </Button>
                        )}
                      </div>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      <div className="mt-3 flex items-center justify-between text-xs text-gray-600">
        <span>
          Page {pageIdx + 1} / {pageCount}
        </span>
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            disabled={offset === 0}
          >
            ←
          </Button>
          <Button
            variant="ghost"
            onClick={() =>
              setOffset(Math.min((pageCount - 1) * PAGE_SIZE, offset + PAGE_SIZE))
            }
            disabled={pageIdx >= pageCount - 1}
          >
            →
          </Button>
        </div>
      </div>

      {/* Phase WWF-P — WWF correction modal. Opens on "Corriger"
          for WWF rows. Submits the full WWF payload via
          submitDecision with the explicit ``wwf`` field
          (Phase WWF-O backend). */}
      {wwfModalRow && (
        <WwfCorrectionModal
          row={wwfModalRow}
          onClose={() => setWwfModalRow(null)}
          onSubmit={async (payload) => {
            try {
              await api.submitDecision(
                projectId,
                wwfModalRow.product_id,
                "wwf",
                {
                  decision: "changed",
                  wwf: payload,
                  reason: "Correction manuelle",
                },
              );
              await load();
              await onChanged?.();
            } catch (e) {
              if (e instanceof ApiError && typeof e.detail === "object" && e.detail) {
                const d = e.detail as { message?: string; detail?: string };
                throw new Error(d.message ?? d.detail ?? String(e));
              }
              throw e;
            }
          }}
        />
      )}
    </Card>
  );
}
