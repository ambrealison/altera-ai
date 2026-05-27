"use client";

/**
 * Phase 34F — Inline category validation table for wizard Step 5.
 *
 * Phase WWF-R — Product-mode side-by-side PT + WWF layout. In
 * ``view=products`` (the default), each row is one product and shows
 * both the Protein Tracker and the WWF classification summary + their
 * methodology-specific review status + independent action menus.
 * ``view=review`` keeps the Phase WWF-N behaviour of one row per
 * ``(product, methodology)`` review item.
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

import type { ReactElement } from "react";
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

// ---------------------------------------------------------------------------
// Label / tone helpers (pure functions — easy to read + unit-test if a
// frontend test runner lands later).
// ---------------------------------------------------------------------------

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

/**
 * Phase WWF-R — review status badge helper. Renders an explicit
 * methodology-tagged label ("PT à vérifier" / "WWF Accepté") so the
 * analyst never confuses which methodology the status applies to.
 */
type StatusTone = "ok" | "warn" | "neutral" | "brand";
function reviewStatusTone(status: string | null): StatusTone {
  if (status === "accepted" || status === "changed") return "ok";
  if (status === "in_queue" || status === "reviewing") return "warn";
  return "neutral";
}
const REVIEW_STATUS_LABELS_FR: Record<string, string> = {
  in_queue: "À vérifier",
  reviewing: "En cours",
  accepted: "Accepté",
  changed: "Modifié",
  deferred: "Différé",
};
function reviewStatusLabel(status: string | null): string {
  if (!status) return "—";
  return REVIEW_STATUS_LABELS_FR[status] ?? status;
}

/** Phase WWF-R — PT cell summary ("Végétal — cœur · 92% · IA"). */
function ptSummary(row: ClassificationRow): {
  label: string;
  tone: StatusTone;
  meta: string;
} | null {
  if (!row.pt_group) return null;
  const conf = confidenceText(row.pt_confidence);
  const src = sourceLabel(row.pt_source);
  return {
    label: ptGroupLabel(row.pt_group),
    tone: PT_GROUP_TONE[row.pt_group] ?? "neutral",
    meta: `${conf} · ${src}`,
  };
}

/** Phase WWF-R — WWF cell summary ("FG1 — Légumineuses · 100% · IA",
 *  "Composite · Végétarien · 69%"). */
function wwfSummary(row: ClassificationRow): {
  label: string;
  tone: StatusTone;
  sub: string | null;
  meta: string;
} | null {
  if (!row.wwf_food_group) return null;
  const sub = wwfSubgroupOf(row);
  const subLabel = sub ? wwfSubgroupLabel(sub) : null;
  const bucket = row.wwf_is_composite
    ? wwfBucketLabel(row.wwf_composite_step1_bucket ?? null)
    : null;
  const label = row.wwf_is_composite
    ? `Composite · ${bucket}`
    : wwfFoodGroupLabel(row.wwf_food_group);
  const meta = `${confidenceText(row.wwf_confidence)} · ${sourceLabel(
    row.wwf_source,
  )}`;
  return {
    label,
    tone: row.wwf_is_composite
      ? "warn"
      : WWF_FOOD_GROUP_TONE[row.wwf_food_group] ?? "neutral",
    sub: row.wwf_is_composite ? null : subLabel,
    meta,
  };
}

/** Phase WWF-R — methodology filter options in product mode. */
type MethodologyView = Methodology | "all";

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

  const searchParams = useSearchParams();
  const queryMethodology = searchParams?.get("methodology") ?? null;

  /** Phase WWF-R — products mode defaults to "all" for PT+WWF projects.
   *  Single-methodology projects always focus their methodology. The
   *  ``?methodology=wwf|protein_tracker`` URL param still overrides. */
  const initialView: MethodologyView = (() => {
    if (queryMethodology === "wwf" && wwfEnabled) return "wwf";
    if (queryMethodology === "protein_tracker" && ptEnabled) {
      return "protein_tracker";
    }
    if (wwfEnabled && !ptEnabled) return "wwf";
    if (!wwfEnabled && ptEnabled) return "protein_tracker";
    return "all";
  })();
  const [methodologyView, setMethodologyView] =
    useState<MethodologyView>(initialView);
  useEffect(() => {
    if (typeof window === "undefined") return;
    const url = new URL(window.location.href);
    if (methodologyView === "all") {
      if (url.searchParams.has("methodology")) {
        url.searchParams.delete("methodology");
        window.history.replaceState({}, "", url.toString());
      }
      return;
    }
    if (url.searchParams.get("methodology") !== methodologyView) {
      url.searchParams.set("methodology", methodologyView);
      window.history.replaceState({}, "", url.toString());
    }
  }, [methodologyView]);

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
  const [submittingKey, setSubmittingKey] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [ptOverrides, setPtOverrides] = useState<
    Record<string, ProteinTrackerGroup>
  >({});
  const [wwfModalRow, setWwfModalRow] = useState<ClassificationRow | null>(
    null,
  );

  const canToggle = ptEnabled && wwfEnabled;

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      // In review mode we use the methodology as a server-side filter
      // (PT or WWF, never "all" — when the user picks "all" we just
      // omit the filter). In products mode we never filter server-side
      // by methodology so the side-by-side comparison keeps every row;
      // single-methodology projects (PT-only or WWF-only) are handled
      // by the backend's natural data shape (the other column is empty).
      let serverMethodology: Methodology | undefined;
      if (tableView === "review") {
        if (methodologyView === "protein_tracker") {
          serverMethodology = "protein_tracker";
        } else if (methodologyView === "wwf") {
          serverMethodology = "wwf";
        } else {
          serverMethodology = undefined;
        }
      }
      const r = await api.listClassifications(projectId, {
        ...filters,
        limit: PAGE_SIZE,
        offset,
        view: tableView,
        methodology: serverMethodology,
      });
      setData(r);
    } catch (e) {
      setLoadError(
        e instanceof Error ? e.message : "Échec du chargement du tableau.",
      );
    }
  }, [api, projectId, filters, offset, tableView, methodologyView]);

  useEffect(() => {
    void load();
  }, [load]);

  /** Phase WWF-R — submit a decision against a specific methodology.
   *  PT actions never touch WWF and vice-versa. */
  async function submit(
    row: ClassificationRow,
    methodology: Methodology,
    decision: "accepted" | "changed",
    payload?: {
      to_category?: ProteinTrackerGroup;
    },
  ) {
    const key = `${row.product_id}:${methodology}:${decision}`;
    setSubmittingKey(key);
    setSubmitError(null);
    try {
      const to =
        decision === "changed" && methodology === "protein_tracker"
          ? payload?.to_category ?? ptOverrides[row.product_id]
          : undefined;
      if (
        decision === "changed" &&
        methodology === "protein_tracker" &&
        !to
      ) {
        setSubmitError(
          "Choisissez une catégorie avant de changer la classification PT.",
        );
        setSubmittingKey(null);
        return;
      }
      await api.submitDecision(projectId, row.product_id, methodology, {
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
      setSubmittingKey(null);
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

  // Phase WWF-R — in review mode the legacy single-methodology layout
  // is still the right answer (each row is one (product, methodology)
  // review item). Products mode uses the new side-by-side layout.
  const isProductsView = tableView === "products";
  const showPtColumns = isProductsView ? ptEnabled : false;
  const showWwfColumns = isProductsView ? wwfEnabled : false;
  const emphasizePt =
    methodologyView === "protein_tracker" || methodologyView === "all";
  const emphasizeWwf = methodologyView === "wwf" || methodologyView === "all";

  // Review mode keeps the legacy single-methodology rendering. The
  // "isReviewWwfView" name preserves the Phase WWF-P branching used by
  // the row renderer below.
  const isReviewWwfView =
    !isProductsView && methodologyView === "wwf";

  const ptReviewTotal = data.pt_review_total ?? 0;
  const wwfReviewTotal = data.wwf_review_total ?? 0;
  const totalReview = ptReviewTotal + wwfReviewTotal;

  // Phase UX-Validation-S — confidence button state derived from the
  // current min/max filter values. The min/max number inputs are no
  // longer rendered; the four buttons are the only entry point.
  const confidencePreset: "low" | "mid" | "high" | "all" = (() => {
    const lo = filters.min_confidence;
    const hi = filters.max_confidence;
    if (lo == null && hi != null && hi <= 0.6) return "low";
    if (lo != null && hi != null && lo >= 0.6 && hi <= 0.8) return "mid";
    if (lo != null && lo >= 0.8 && hi == null) return "high";
    return "all";
  })();

  return (
    <Card>
      {/* Title + subtitle */}
      <div className="mb-2">
        <h3 className="text-sm font-semibold text-gray-800">
          {isProductsView
            ? "Validation des produits"
            : isReviewWwfView
              ? "Validation WWF"
              : "Validation Protein Tracker"}
        </h3>
        <p className="mt-0.5 text-xs text-gray-500">
          {isProductsView
            ? "Vue d'ensemble des classifications Protein Tracker et WWF — actions indépendantes par méthodologie."
            : isReviewWwfView
              ? "Vérifiez les groupes alimentaires WWF, sous-groupes et produits composites."
              : "Vérifiez les catégories Protein Tracker assignées par les règles déterministes et l'IA."}
        </p>
      </div>

      {/* Phase WWF-R — counts banner. Global PT/WWF review totals so
          the analyst can size the validation backlog without paging
          through the queue. */}
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="text-gray-600">
          {data.total} produit(s) affiché(s)
        </span>
        {ptEnabled && (
          <Pill tone={ptReviewTotal > 0 ? "warn" : "neutral"}>
            PT à vérifier : {ptReviewTotal}
          </Pill>
        )}
        {wwfEnabled && (
          <Pill tone={wwfReviewTotal > 0 ? "warn" : "neutral"}>
            WWF à vérifier : {wwfReviewTotal}
          </Pill>
        )}
        {canToggle && (
          <Pill tone={totalReview > 0 ? "brand" : "neutral"}>
            Total à valider : {totalReview}
          </Pill>
        )}
        {Object.entries(data.counts_by_source).map(([k, v]) => (
          <Pill
            key={k}
            tone={k === "ai" ? "brand" : k === "deterministic" ? "ok" : "neutral"}
          >
            {sourceLabel(k)} : {v}
          </Pill>
        ))}
      </div>

      {/* Phase UX-Validation-S — single consolidated filter bar.
          View · Méthodologie · Recherche · Source · Catégorie ·
          Statut · Confiance (boutons préréglés). On smaller screens
          the bar wraps to a second row. The legacy min/max
          confidence number inputs are removed. */}
      <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
        {/* View toggle */}
        <div className="inline-flex rounded-md border border-gray-200 bg-white p-0.5">
          <button
            type="button"
            onClick={() => {
              setTableView("products");
              setOffset(0);
            }}
            className={
              "rounded px-2 py-0.5 font-medium transition " +
              (tableView === "products"
                ? "bg-brand-600 text-white"
                : "text-gray-600 hover:bg-gray-50")
            }
          >
            Tous
          </button>
          <button
            type="button"
            onClick={() => {
              setTableView("review");
              setOffset(0);
            }}
            className={
              "rounded px-2 py-0.5 font-medium transition " +
              (tableView === "review"
                ? "bg-brand-600 text-white"
                : "text-gray-600 hover:bg-gray-50")
            }
          >
            À valider
            {totalReview > 0 && (
              <span
                className={
                  "ml-1 inline-flex items-center justify-center rounded-full px-1.5 text-[10px] font-semibold " +
                  (tableView === "review"
                    ? "bg-white/20 text-white"
                    : "bg-amber-100 text-amber-700")
                }
              >
                {totalReview}
              </span>
            )}
          </button>
        </div>

        {/* Methodology toggle (only on PT+WWF projects) */}
        {canToggle && (
          <div className="inline-flex rounded-md border border-gray-200 bg-white p-0.5">
            {isProductsView && (
              <button
                type="button"
                onClick={() => setMethodologyView("all")}
                className={
                  "rounded px-2 py-0.5 font-medium transition " +
                  (methodologyView === "all"
                    ? "bg-brand-600 text-white"
                    : "text-gray-600 hover:bg-gray-50")
                }
              >
                Toutes
              </button>
            )}
            <button
              type="button"
              onClick={() => setMethodologyView("protein_tracker")}
              className={
                "rounded px-2 py-0.5 font-medium transition " +
                (methodologyView === "protein_tracker"
                  ? "bg-brand-600 text-white"
                  : "text-gray-600 hover:bg-gray-50")
              }
            >
              PT
            </button>
            <button
              type="button"
              onClick={() => setMethodologyView("wwf")}
              className={
                "rounded px-2 py-0.5 font-medium transition " +
                (methodologyView === "wwf"
                  ? "bg-brand-600 text-white"
                  : "text-gray-600 hover:bg-gray-50")
              }
            >
              WWF
            </button>
          </div>
        )}

        <input
          type="text"
          placeholder="Rechercher (nom / marque)"
          value={filters.product_search ?? ""}
          onChange={(e) => patchFilter({ product_search: e.target.value })}
          className="w-48 rounded border border-gray-300 bg-white px-2 py-1 text-gray-800 focus:border-brand-500 focus:outline-none"
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
          className="rounded border border-gray-300 bg-white px-2 py-1 text-gray-800 focus:border-brand-500 focus:outline-none"
        >
          <option value="">Toutes sources</option>
          <option value="deterministic">Déterministe</option>
          <option value="ai">IA</option>
          <option value="manual_review">Manuel</option>
          <option value="unknown">Non classé</option>
        </select>
        {ptEnabled && (
          <select
            value={filters.pt_group ?? ""}
            onChange={(e) =>
              patchFilter({
                pt_group: (e.target.value || undefined) as
                  | ProteinTrackerGroup
                  | undefined,
              })
            }
            className="rounded border border-gray-300 bg-white px-2 py-1 text-gray-800 focus:border-brand-500 focus:outline-none"
          >
            <option value="">Toutes catégories PT</option>
            {PT_GROUP_OPTIONS.map((g) => (
              <option key={g} value={g}>
                {PT_GROUP_LABELS_FR[g]}
              </option>
            ))}
          </select>
        )}
        <select
          value={filters.review_status ?? ""}
          onChange={(e) =>
            patchFilter({
              review_status: (e.target.value || undefined) as
                | ClassificationsFilters["review_status"]
                | undefined,
            })
          }
          className="rounded border border-gray-300 bg-white px-2 py-1 text-gray-800 focus:border-brand-500 focus:outline-none"
        >
          <option value="">Tous statuts</option>
          <option value="in_queue">À vérifier</option>
          <option value="accepted">Accepté</option>
          <option value="changed">Modifié</option>
          <option value="deferred">Différé</option>
        </select>

        {/* Confidence preset buttons (Phase UX-Validation-S — replaces
            the old min/max number inputs). */}
        <span className="text-gray-400">·</span>
        <button
          type="button"
          onClick={() =>
            patchFilter({ min_confidence: undefined, max_confidence: 0.6 })
          }
          className={
            "rounded border px-1.5 py-0.5 transition " +
            (confidencePreset === "low"
              ? "border-rose-400 bg-rose-100 text-rose-800"
              : "border-rose-200 bg-rose-50 text-rose-700 hover:bg-rose-100")
          }
        >
          &lt; 0.60
        </button>
        <button
          type="button"
          onClick={() =>
            patchFilter({ min_confidence: 0.6, max_confidence: 0.8 })
          }
          className={
            "rounded border px-1.5 py-0.5 transition " +
            (confidencePreset === "mid"
              ? "border-amber-400 bg-amber-100 text-amber-800"
              : "border-amber-200 bg-amber-50 text-amber-700 hover:bg-amber-100")
          }
        >
          0.60–0.80
        </button>
        <button
          type="button"
          onClick={() =>
            patchFilter({ min_confidence: 0.8, max_confidence: undefined })
          }
          className={
            "rounded border px-1.5 py-0.5 transition " +
            (confidencePreset === "high"
              ? "border-emerald-400 bg-emerald-100 text-emerald-800"
              : "border-emerald-200 bg-emerald-50 text-emerald-700 hover:bg-emerald-100")
          }
        >
          ≥ 0.80
        </button>
        <button
          type="button"
          onClick={() =>
            patchFilter({
              min_confidence: undefined,
              max_confidence: undefined,
            })
          }
          className={
            "rounded border px-1.5 py-0.5 transition " +
            (confidencePreset === "all"
              ? "border-gray-400 bg-gray-100 text-gray-800"
              : "border-gray-200 bg-white text-gray-600 hover:bg-gray-50")
          }
        >
          Tous
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
              {isProductsView ? (
                <>
                  {showPtColumns && (
                    <>
                      <th
                        className={
                          "py-2 pr-3 font-medium " +
                          (emphasizePt ? "" : "opacity-50")
                        }
                      >
                        Protein Tracker
                      </th>
                      <th
                        className={
                          "py-2 pr-3 font-medium " +
                          (emphasizePt ? "" : "opacity-50")
                        }
                      >
                        Statut PT
                      </th>
                    </>
                  )}
                  {showWwfColumns && (
                    <>
                      <th
                        className={
                          "py-2 pr-3 font-medium " +
                          (emphasizeWwf ? "" : "opacity-50")
                        }
                      >
                        WWF
                      </th>
                      <th
                        className={
                          "py-2 pr-3 font-medium " +
                          (emphasizeWwf ? "" : "opacity-50")
                        }
                      >
                        Statut WWF
                      </th>
                    </>
                  )}
                  <th className="py-2 font-medium">Actions</th>
                </>
              ) : isReviewWwfView ? (
                <>
                  <th className="py-2 pr-3 font-medium">Groupe WWF</th>
                  <th className="py-2 pr-3 font-medium">Sous-groupe</th>
                  <th className="py-2 pr-3 font-medium">Composite</th>
                  <th className="py-2 pr-3 font-medium">Source</th>
                  <th className="py-2 pr-3 font-medium">Confiance</th>
                  <th className="py-2 pr-3 font-medium">Statut</th>
                  <th className="py-2 font-medium">Action</th>
                </>
              ) : (
                <>
                  <th className="py-2 pr-3 font-medium">PT</th>
                  {wwfEnabled && (
                    <th className="py-2 pr-3 font-medium">WWF</th>
                  )}
                  <th className="py-2 pr-3 font-medium">Source</th>
                  <th className="py-2 pr-3 font-medium">Confiance</th>
                  <th className="py-2 pr-3 font-medium">Statut</th>
                  <th className="py-2 font-medium">Action</th>
                </>
              )}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {data.items.length === 0 && (
              <tr>
                <td
                  colSpan={
                    isProductsView
                      ? 3 +
                        (showPtColumns ? 2 : 0) +
                        (showWwfColumns ? 2 : 0)
                      : isReviewWwfView
                        ? 8
                        : wwfEnabled
                          ? 8
                          : 7
                  }
                  className="py-4 text-center text-gray-500"
                >
                  Aucun produit ne correspond aux filtres.
                </td>
              </tr>
            )}
            {data.items.map((row) =>
              isProductsView
                ? renderProductsRow(row)
                : renderReviewRow(row),
            )}
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

      {/* Phase WWF-P — WWF correction modal. */}
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

  // -------------------------------------------------------------------
  // Row renderers — kept inside the component closure so they can use
  // the local handlers (submit, setWwfModalRow, ptOverrides). Pure
  // helpers above (ptSummary / wwfSummary / reviewStatusLabel) carry
  // most of the rendering logic so the row markup stays readable.
  // -------------------------------------------------------------------

  function renderProductsRow(row: ClassificationRow): ReactElement {
    const ptCell = ptSummary(row);
    const wwfCell = wwfSummary(row);
    const ptStatus = row.review_status ?? null;
    const wwfStatus = row.wwf_review_status ?? null;
    const ptInQueue = ptStatus === "in_queue" || ptStatus === "reviewing";
    const wwfInQueue =
      wwfStatus === "in_queue" || wwfStatus === "reviewing";
    const chosenPt = ptOverrides[row.product_id];
    const busyAcceptPt =
      submittingKey === `${row.product_id}:protein_tracker:accepted`;
    const busyChangePt =
      submittingKey === `${row.product_id}:protein_tracker:changed`;
    const busyAcceptWwf =
      submittingKey === `${row.product_id}:wwf:accepted`;
    return (
      <tr key={row.product_id} className="align-top">
        <td className="py-2 pr-3">
          <div className="font-medium text-gray-800">{row.product_name}</div>
          {row.brand && <div className="text-gray-500">{row.brand}</div>}
        </td>
        <td className="py-2 pr-3 text-gray-600">
          {row.retailer_category ?? "—"}
          {row.retailer_subcategory && (
            <div className="text-gray-400">{row.retailer_subcategory}</div>
          )}
        </td>
        {showPtColumns && (
          <>
            <td className={"py-2 pr-3 " + (emphasizePt ? "" : "opacity-60")}>
              {ptCell ? (
                <>
                  <Pill tone={ptCell.tone}>{ptCell.label}</Pill>
                  <div className="mt-0.5 text-gray-500">{ptCell.meta}</div>
                </>
              ) : (
                <span className="text-gray-400">—</span>
              )}
            </td>
            <td className={"py-2 pr-3 " + (emphasizePt ? "" : "opacity-60")}>
              {ptStatus ? (
                <Pill tone={reviewStatusTone(ptStatus)}>
                  {reviewStatusLabel(ptStatus)}
                </Pill>
              ) : (
                <span className="text-gray-400">—</span>
              )}
            </td>
          </>
        )}
        {showWwfColumns && (
          <>
            <td className={"py-2 pr-3 " + (emphasizeWwf ? "" : "opacity-60")}>
              {wwfCell ? (
                <>
                  <Pill tone={wwfCell.tone}>{wwfCell.label}</Pill>
                  {wwfCell.sub && (
                    <div className="mt-0.5 text-gray-500">{wwfCell.sub}</div>
                  )}
                  <div className="mt-0.5 text-gray-500">{wwfCell.meta}</div>
                </>
              ) : (
                <span className="text-gray-400">—</span>
              )}
            </td>
            <td className={"py-2 pr-3 " + (emphasizeWwf ? "" : "opacity-60")}>
              {wwfStatus ? (
                <Pill tone={reviewStatusTone(wwfStatus)}>
                  {reviewStatusLabel(wwfStatus)}
                </Pill>
              ) : (
                <span className="text-gray-400">—</span>
              )}
            </td>
          </>
        )}
        <td className="py-2">
          <div className="flex flex-col gap-1">
            {showPtColumns && ptCell && (
              <div
                className={
                  "flex flex-wrap items-center gap-1 " +
                  (emphasizePt ? "" : "opacity-70")
                }
              >
                <span className="text-[10px] font-semibold uppercase tracking-wide text-brand-700">
                  PT
                </span>
                <select
                  value={chosenPt ?? ""}
                  onChange={(e) =>
                    setPtOverrides((prev) => ({
                      ...prev,
                      [row.product_id]: e.target.value as ProteinTrackerGroup,
                    }))
                  }
                  disabled={busyChangePt}
                  className="rounded border border-gray-300 bg-white px-1.5 py-0.5 text-xs text-gray-800 focus:border-brand-500 focus:outline-none"
                >
                  <option value="">Changer…</option>
                  {PT_GROUP_OPTIONS.map((g) => (
                    <option key={g} value={g}>
                      {PT_GROUP_LABELS_FR[g]}
                    </option>
                  ))}
                </select>
                <span title="Corriger PT">
                  <Button
                    variant="ghost"
                    onClick={() =>
                      void submit(row, "protein_tracker", "changed")
                    }
                    disabled={busyChangePt || !chosenPt}
                  >
                    {busyChangePt ? "…" : "✎"}
                  </Button>
                </span>
                {ptInQueue && (
                  <span title="Accepter PT">
                    <Button
                      variant="secondary"
                      onClick={() =>
                        void submit(row, "protein_tracker", "accepted")
                      }
                      disabled={busyAcceptPt}
                    >
                      {busyAcceptPt ? "…" : "✓"}
                    </Button>
                  </span>
                )}
              </div>
            )}
            {showWwfColumns && wwfCell && (
              <div
                className={
                  "flex flex-wrap items-center gap-1 " +
                  (emphasizeWwf ? "" : "opacity-70")
                }
              >
                <span className="text-[10px] font-semibold uppercase tracking-wide text-amber-700">
                  WWF
                </span>
                <span title="Corriger WWF">
                  <Button
                    variant="ghost"
                    onClick={() => setWwfModalRow(row)}
                    disabled={busyAcceptWwf}
                  >
                    Corriger
                  </Button>
                </span>
                {wwfInQueue && (
                  <span title="Accepter WWF">
                    <Button
                      variant="secondary"
                      onClick={() => void submit(row, "wwf", "accepted")}
                      disabled={busyAcceptWwf}
                    >
                      {busyAcceptWwf ? "…" : "✓"}
                    </Button>
                  </span>
                )}
              </div>
            )}
          </div>
        </td>
      </tr>
    );
  }

  function renderReviewRow(row: ClassificationRow): ReactElement {
    // Phase WWF-N legacy review-mode rendering. Each row is one
    // ``(product, methodology)`` — methodology is carried by the
    // ``row.methodology`` field set by the backend.
    const rowMethodology: Methodology = row.methodology ?? "protein_tracker";
    const isWwfRow = rowMethodology === "wwf";
    const acceptKey = `${row.product_id}:${rowMethodology}:accepted`;
    const changeKey = `${row.product_id}:${rowMethodology}:changed`;
    const busyAccept = submittingKey === acceptKey;
    const busyChange = submittingKey === changeKey;
    const chosen = ptOverrides[row.product_id];
    const status = isWwfRow ? row.wwf_review_status : row.review_status;
    return (
      <tr
        key={`${row.product_id}-${row.methodology ?? "all"}`}
        className="align-top"
      >
        <td className="py-2 pr-3">
          <div className="font-medium text-gray-800">{row.product_name}</div>
          {row.brand && <div className="text-gray-500">{row.brand}</div>}
          <div className="mt-1">
            <Pill tone={isWwfRow ? "warn" : "brand"}>
              {isWwfRow ? "WWF" : "Protein Tracker"}
            </Pill>
          </div>
        </td>
        <td className="py-2 pr-3 text-gray-600">
          {row.retailer_category ?? "—"}
          {row.retailer_subcategory && (
            <div className="text-gray-400">{row.retailer_subcategory}</div>
          )}
        </td>
        {isWwfRow ? (
          <>
            <td className="py-2 pr-3">
              {row.wwf_food_group ? (
                <Pill
                  tone={
                    WWF_FOOD_GROUP_TONE[row.wwf_food_group] ?? "neutral"
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
                  {wwfBucketLabel(row.wwf_composite_step1_bucket ?? null)}
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
          {sourceLabel(isWwfRow ? row.wwf_source : row.pt_source)}
        </td>
        <td className="py-2 pr-3 text-gray-600">
          {confidenceText(isWwfRow ? row.wwf_confidence : row.pt_confidence)}
        </td>
        <td className="py-2 pr-3">
          {status ? (
            <Pill tone={reviewStatusTone(status)}>
              {reviewStatusLabel(status)}
            </Pill>
          ) : (
            <span className="text-gray-400">—</span>
          )}
        </td>
        <td className="py-2">
          {isWwfRow ? (
            <div className="flex flex-wrap items-center gap-1">
              <Button
                variant="ghost"
                onClick={() => setWwfModalRow(row)}
                disabled={busyAccept}
              >
                Corriger
              </Button>
              {(status === "in_queue" || status === "reviewing") &&
                row.wwf_food_group && (
                  <Button
                    variant="secondary"
                    onClick={() => void submit(row, "wwf", "accepted")}
                    disabled={busyAccept}
                  >
                    {busyAccept ? "…" : "✓"}
                  </Button>
                )}
            </div>
          ) : (
            <div className="flex flex-wrap items-center gap-1">
              <select
                value={chosen ?? ""}
                onChange={(e) =>
                  setPtOverrides((prev) => ({
                    ...prev,
                    [row.product_id]: e.target.value as ProteinTrackerGroup,
                  }))
                }
                disabled={busyChange}
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
                onClick={() =>
                  void submit(row, "protein_tracker", "changed")
                }
                disabled={busyChange || !chosen}
              >
                {busyChange ? "…" : "✎"}
              </Button>
              {(status === "in_queue" || status === "reviewing") &&
                row.pt_group && (
                  <Button
                    variant="secondary"
                    onClick={() =>
                      void submit(row, "protein_tracker", "accepted")
                    }
                    disabled={busyAccept}
                  >
                    {busyAccept ? "…" : "✓"}
                  </Button>
                )}
            </div>
          )}
        </td>
      </tr>
    );
  }
}
