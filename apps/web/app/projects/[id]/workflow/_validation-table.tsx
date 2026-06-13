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

import { Button, Card, Pill, Segmented, Skeleton } from "@/components/ui";
import { useT } from "@/lib/i18n";
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

type TFn = (key: string) => string;

// CODE → i18n key. The CODE keys are canonical and must not change; the
// display labels are looked up through ``t`` at render time.
const WWF_FOOD_GROUP_LABEL_KEYS: Record<string, string> = {
  FG1: "validation.fg.FG1",
  FG2: "validation.fg.FG2",
  FG3: "validation.fg.FG3",
  FG4: "validation.fg.FG4",
  FG5: "validation.fg.FG5",
  FG6: "validation.fg.FG6",
  FG7: "validation.fg.FG7",
  out_of_scope: "validation.fg.out_of_scope",
  unknown: "validation.fg.unknown",
};

const WWF_SUBGROUP_LABEL_KEYS: Record<string, string> = {
  // FG1
  red_meat: "validation.subgroup.red_meat",
  poultry: "validation.subgroup.poultry",
  processed_meats_alternatives:
    "validation.subgroup.processed_meats_alternatives",
  seafood: "validation.subgroup.seafood",
  eggs: "validation.subgroup.eggs",
  legumes: "validation.subgroup.legumes",
  nuts_seeds: "validation.subgroup.nuts_seeds",
  alternative_protein_sources: "validation.subgroup.alternative_protein_sources",
  meat_egg_seafood_alternatives:
    "validation.subgroup.meat_egg_seafood_alternatives",
  // FG2
  cheese: "validation.subgroup.cheese",
  other_dairy_animal: "validation.subgroup.other_dairy_animal",
  dairy_alternative_plant: "validation.subgroup.dairy_alternative_plant",
  // FG3
  plant_based_fat: "validation.subgroup.plant_based_fat",
  animal_based_fat: "validation.subgroup.animal_based_fat",
  // FG5
  whole_grain: "validation.subgroup.whole_grain",
  refined_grain: "validation.subgroup.refined_grain",
  // FG7
  plant_based_snack: "validation.subgroup.plant_based_snack",
  animal_based_snack: "validation.subgroup.animal_based_snack",
};

function wwfSubgroupLabel(t: TFn, v: string | null): string {
  if (!v) return "—";
  const key = WWF_SUBGROUP_LABEL_KEYS[v];
  return key ? t(key) : v;
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

const WWF_BUCKET_LABEL_KEYS: Record<string, string> = {
  meat_based: "validation.bucket.meat_based",
  seafood_based: "validation.bucket.seafood_based",
  vegetarian: "validation.bucket.vegetarian",
  vegan: "validation.bucket.vegan",
};

function wwfFoodGroupLabel(t: TFn, fg: string | null | undefined): string {
  if (!fg) return "—";
  const key = WWF_FOOD_GROUP_LABEL_KEYS[fg];
  return key ? t(key) : fg;
}

function wwfBucketLabel(t: TFn, b: string | null | undefined): string {
  if (!b) return "—";
  const key = WWF_BUCKET_LABEL_KEYS[b];
  return key ? t(key) : b;
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

const PT_GROUP_LABEL_KEYS: Record<ProteinTrackerGroup, string> = {
  plant_based_core: "validation.ptGroup.plant_based_core",
  plant_based_non_core: "validation.ptGroup.plant_based_non_core",
  composite_products: "validation.ptGroup.composite_products",
  animal_core: "validation.ptGroup.animal_core",
  out_of_scope: "validation.ptGroup.out_of_scope",
  unknown: "validation.ptGroup.unknown",
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

const SOURCE_LABEL_KEYS: Record<string, string> = {
  deterministic: "validation.source.deterministic",
  ai: "validation.source.ai",
  manual_review: "validation.source.manual_review",
  unknown: "validation.source.unknown",
};

function ptGroupLabel(t: TFn, g: ProteinTrackerGroup | null): string {
  if (!g) return t("validation.none");
  const key = PT_GROUP_LABEL_KEYS[g];
  return key ? t(key) : g;
}

function sourceLabel(t: TFn, s: string | null): string {
  if (!s) return t("validation.none");
  const key = SOURCE_LABEL_KEYS[s];
  return key ? t(key) : s;
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
const REVIEW_STATUS_LABEL_KEYS: Record<string, string> = {
  in_queue: "validation.reviewStatus.in_queue",
  reviewing: "validation.reviewStatus.reviewing",
  accepted: "validation.reviewStatus.accepted",
  changed: "validation.reviewStatus.changed",
  deferred: "validation.reviewStatus.deferred",
};
function reviewStatusLabel(t: TFn, status: string | null): string {
  if (!status) return "—";
  const key = REVIEW_STATUS_LABEL_KEYS[status];
  return key ? t(key) : status;
}

/** Phase WWF-R — PT cell summary ("Végétal — cœur · 92% · IA"). */
function ptSummary(t: TFn, row: ClassificationRow): {
  label: string;
  tone: StatusTone;
  meta: string;
} | null {
  if (!row.pt_group) return null;
  const conf = confidenceText(row.pt_confidence);
  const src = sourceLabel(t, row.pt_source);
  return {
    label: ptGroupLabel(t, row.pt_group),
    tone: PT_GROUP_TONE[row.pt_group] ?? "neutral",
    meta: `${conf} · ${src}`,
  };
}

/** WWF category pill label. Composite products show "Composite · {bucket}"
 *  (the Step-1 bucket is what the calculation uses), NOT the underlying
 *  schema-filler food group / subgroup — so a composite vegetarian pizza
 *  reads "Composite · Végétarien", never "FG2 · Lait & alternatives". */
function wwfCategoryLabel(t: TFn, row: ClassificationRow): string {
  if (!row.wwf_food_group) return "—";
  if (row.wwf_is_composite) {
    const bucket = wwfBucketLabel(t, row.wwf_composite_step1_bucket ?? null);
    return t("validation.compositePrefix").replace("{bucket}", String(bucket));
  }
  return wwfFoodGroupLabel(t, row.wwf_food_group);
}

function wwfCategoryTone(row: ClassificationRow): StatusTone {
  if (row.wwf_is_composite) return "warn";
  return WWF_FOOD_GROUP_TONE[row.wwf_food_group ?? ""] ?? "neutral";
}

/** Phase WWF-R — WWF cell summary ("FG1 — Légumineuses · 100% · IA",
 *  "Composite · Végétarien · 69%"). */
function wwfSummary(t: TFn, row: ClassificationRow): {
  label: string;
  tone: StatusTone;
  sub: string | null;
  meta: string;
} | null {
  if (!row.wwf_food_group) return null;
  const sub = wwfSubgroupOf(row);
  const subLabel = sub ? wwfSubgroupLabel(t, sub) : null;
  const bucket = row.wwf_is_composite
    ? wwfBucketLabel(t, row.wwf_composite_step1_bucket ?? null)
    : null;
  const label = row.wwf_is_composite
    ? t("validation.compositePrefix").replace("{bucket}", String(bucket))
    : wwfFoodGroupLabel(t, row.wwf_food_group);
  const meta = `${confidenceText(row.wwf_confidence)} · ${sourceLabel(
    t,
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
  const t = useT();
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
        e instanceof Error ? e.message : t("validation.error.load"),
      );
    }
  }, [api, projectId, filters, offset, tableView, methodologyView, t]);

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
          t("validation.error.choosePtCategory"),
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
          e instanceof Error ? e.message : t("validation.error.decision"),
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
        <div className="rounded-xl border border-danger-100 bg-danger-50 px-3 py-2 text-sm text-danger-700">
          {loadError}
        </div>
      </Card>
    );
  }
  if (!data) {
    return (
      <Card>
        <div className="space-y-2">
          <Skeleton className="h-8 w-1/3" />
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-64 w-full" />
        </div>
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
      <div className="mb-3">
        <h3 className="text-base font-semibold text-forest-900">
          {isProductsView
            ? t("validation.title.products")
            : isReviewWwfView
              ? t("validation.title.wwf")
              : t("validation.title.pt")}
        </h3>
        <p className="mt-0.5 text-xs text-ink-muted">
          {isProductsView
            ? t("validation.subtitle.products")
            : isReviewWwfView
              ? t("validation.subtitle.wwf")
              : t("validation.subtitle.pt")}
        </p>
      </div>

      {/* Phase WWF-R — counts banner. Global PT/WWF review totals so
          the analyst can size the validation backlog without paging
          through the queue. */}
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="font-medium text-forest-700">
          {t("validation.counts.displayed").replace(
            "{n}",
            String(data.total),
          )}
        </span>
        {ptEnabled && (
          <Pill tone={ptReviewTotal > 0 ? "warn" : "neutral"}>
            {t("validation.counts.ptToReview").replace(
              "{n}",
              String(ptReviewTotal),
            )}
          </Pill>
        )}
        {wwfEnabled && (
          <Pill tone={wwfReviewTotal > 0 ? "warn" : "neutral"}>
            {t("validation.counts.wwfToReview").replace(
              "{n}",
              String(wwfReviewTotal),
            )}
          </Pill>
        )}
        {canToggle && (
          <Pill tone={totalReview > 0 ? "brand" : "neutral"}>
            {t("validation.counts.totalToValidate").replace(
              "{n}",
              String(totalReview),
            )}
          </Pill>
        )}
        {Object.entries(data.counts_by_source).map(([k, v]) => (
          <Pill
            key={k}
            tone={k === "ai" ? "brand" : k === "deterministic" ? "ok" : "neutral"}
          >
            {t("validation.counts.bySource")
              .replace("{label}", sourceLabel(t, k))
              .replace("{n}", String(v))}
          </Pill>
        ))}
      </div>

      {/* Phase Design-A — premium consolidated filter bar. View ·
          Méthodologie · Recherche · Source · Catégorie · Statut ·
          Confiance (segmented controls + preset buttons). Wraps on
          small screens. */}
      <div className="mt-4 flex flex-wrap items-center gap-2 rounded-2xl border border-line bg-mint-50/50 p-2 text-xs">
        {/* View toggle */}
        <Segmented
          size="sm"
          value={tableView}
          onChange={(v) => {
            setTableView(v);
            setOffset(0);
          }}
          options={[
            { value: "products", label: t("validation.view.all") },
            {
              value: "review",
              label: (
                <span className="inline-flex items-center gap-1">
                  {t("validation.view.toValidate")}
                  {totalReview > 0 && (
                    <span
                      className={
                        "inline-flex items-center justify-center rounded-full px-1.5 text-[10px] font-semibold " +
                        (tableView === "review"
                          ? "bg-white/25 text-white"
                          : "bg-warn-100 text-warn-700")
                      }
                    >
                      {totalReview}
                    </span>
                  )}
                </span>
              ),
            },
          ]}
        />

        {/* Methodology toggle (only on PT+WWF projects) */}
        {canToggle && (
          <Segmented
            size="sm"
            value={methodologyView}
            onChange={(v) => setMethodologyView(v)}
            options={[
              ...(isProductsView
                ? [{ value: "all" as const, label: t("validation.methodology.all") }]
                : []),
              { value: "protein_tracker" as const, label: t("validation.methodology.pt") },
              { value: "wwf" as const, label: t("validation.methodology.wwf") },
            ]}
          />
        )}

        <input
          type="text"
          placeholder={t("validation.search.placeholder")}
          value={filters.product_search ?? ""}
          onChange={(e) => patchFilter({ product_search: e.target.value })}
          className="w-48 rounded-xl border border-line bg-white px-3 py-1.5 text-gray-800 shadow-soft transition-colors focus:border-brand-400 focus:outline-none"
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
          className="rounded-xl border border-line bg-white px-2.5 py-1.5 text-gray-800 shadow-soft transition-colors focus:border-brand-400 focus:outline-none"
        >
          <option value="">{t("validation.filter.allSources")}</option>
          <option value="deterministic">{t("validation.source.deterministic")}</option>
          <option value="ai">{t("validation.source.ai")}</option>
          <option value="manual_review">{t("validation.source.manual_review")}</option>
          <option value="unknown">{t("validation.filter.unclassified")}</option>
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
            className="rounded-xl border border-line bg-white px-2.5 py-1.5 text-gray-800 shadow-soft transition-colors focus:border-brand-400 focus:outline-none"
          >
            <option value="">{t("validation.filter.allPtCategories")}</option>
            {PT_GROUP_OPTIONS.map((g) => (
              <option key={g} value={g}>
                {t(PT_GROUP_LABEL_KEYS[g])}
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
          className="rounded-xl border border-line bg-white px-2.5 py-1.5 text-gray-800 shadow-soft transition-colors focus:border-brand-400 focus:outline-none"
        >
          <option value="">{t("validation.filter.allStatuses")}</option>
          <option value="in_queue">{t("validation.reviewStatus.in_queue")}</option>
          <option value="accepted">{t("validation.reviewStatus.accepted")}</option>
          <option value="changed">{t("validation.reviewStatus.changed")}</option>
          <option value="deferred">{t("validation.reviewStatus.deferred")}</option>
        </select>

        {/* Confidence preset buttons. */}
        <span className="mx-0.5 h-5 w-px bg-line" />
        <button
          type="button"
          onClick={() =>
            patchFilter({ min_confidence: undefined, max_confidence: 0.6 })
          }
          className={
            "rounded-lg border px-2 py-1 font-medium transition " +
            (confidencePreset === "low"
              ? "border-danger-400 bg-danger-100 text-danger-700"
              : "border-danger-100 bg-danger-50 text-danger-700 hover:bg-danger-100")
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
            "rounded-lg border px-2 py-1 font-medium transition " +
            (confidencePreset === "mid"
              ? "border-warn-400 bg-warn-100 text-warn-700"
              : "border-warn-100 bg-warn-50 text-warn-700 hover:bg-warn-100")
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
            "rounded-lg border px-2 py-1 font-medium transition " +
            (confidencePreset === "high"
              ? "border-brand-400 bg-mint-100 text-brand-700"
              : "border-brand-100 bg-mint-50 text-brand-700 hover:bg-mint-100")
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
            "rounded-lg border px-2 py-1 font-medium transition " +
            (confidencePreset === "all"
              ? "border-ink-soft bg-line-soft text-forest-700"
              : "border-line bg-white text-ink-muted hover:bg-mint-50")
          }
        >
          {t("validation.view.all")}
        </button>
      </div>

      {submitError && (
        <div className="mt-3 rounded-xl border border-danger-100 bg-danger-50 px-3 py-2 text-sm text-danger-700">
          {submitError}
        </div>
      )}

      {/* Table */}
      <div className="scroll-soft mt-4 overflow-x-auto rounded-2xl border border-line">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-line bg-mint-50/70 text-left text-[11px] uppercase tracking-wider text-ink-soft">
              <th className="py-2.5 pl-4 pr-3 font-semibold">{t("validation.col.product")}</th>
              <th className="py-2.5 pr-3 font-semibold">{t("validation.col.retailerCategory")}</th>
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
                        {t("validation.col.proteinTracker")}
                      </th>
                      <th
                        className={
                          "py-2 pr-3 font-medium " +
                          (emphasizePt ? "" : "opacity-50")
                        }
                      >
                        {t("validation.col.ptStatus")}
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
                        {t("validation.col.wwf")}
                      </th>
                      <th
                        className={
                          "py-2 pr-3 font-medium " +
                          (emphasizeWwf ? "" : "opacity-50")
                        }
                      >
                        {t("validation.col.wwfStatus")}
                      </th>
                    </>
                  )}
                  <th className="py-2 font-medium">{t("validation.col.actions")}</th>
                </>
              ) : isReviewWwfView ? (
                <>
                  <th className="py-2 pr-3 font-medium">{t("validation.col.wwfGroup")}</th>
                  <th className="py-2 pr-3 font-medium">{t("validation.col.subgroup")}</th>
                  <th className="py-2 pr-3 font-medium">{t("validation.col.composite")}</th>
                  <th className="py-2 pr-3 font-medium">{t("validation.col.source")}</th>
                  <th className="py-2 pr-3 font-medium">{t("validation.col.confidence")}</th>
                  <th className="py-2 pr-3 font-medium">{t("validation.col.status")}</th>
                  <th className="py-2 font-medium">{t("validation.col.action")}</th>
                </>
              ) : (
                <>
                  <th className="py-2 pr-3 font-medium">{t("validation.col.pt")}</th>
                  {wwfEnabled && (
                    <th className="py-2 pr-3 font-medium">{t("validation.col.wwf")}</th>
                  )}
                  <th className="py-2 pr-3 font-medium">{t("validation.col.source")}</th>
                  <th className="py-2 pr-3 font-medium">{t("validation.col.confidence")}</th>
                  <th className="py-2 pr-3 font-medium">{t("validation.col.status")}</th>
                  <th className="py-2 font-medium">{t("validation.col.action")}</th>
                </>
              )}
            </tr>
          </thead>
          <tbody className="divide-y divide-line-soft">
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
                  className="px-4 py-10 text-center"
                >
                  <div className="text-sm font-medium text-forest-700">
                    {t("validation.empty.title")}
                  </div>
                  <div className="mt-1 text-xs text-ink-muted">
                    {t("validation.empty.body")}
                  </div>
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
      <div className="mt-3 flex items-center justify-between text-xs text-ink-muted">
        <span>
          {t("validation.pagination.page")} <span className="font-semibold text-forest-700">{pageIdx + 1}</span> / {pageCount}
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
                  reason: t("validation.wwf.manualCorrection"),
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
    const ptCell = ptSummary(t, row);
    const wwfCell = wwfSummary(t, row);
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
      <tr
        key={row.product_id}
        className="align-top transition-colors hover:bg-mint-50/50"
      >
        <td className="py-2.5 pl-4 pr-3">
          <div className="font-medium text-forest-900">{row.product_name}</div>
          {row.brand && <div className="text-ink-soft">{row.brand}</div>}
        </td>
        <td className="py-2.5 pr-3 text-ink-muted">
          {row.retailer_category ?? "—"}
          {row.retailer_subcategory && (
            <div className="text-ink-soft">{row.retailer_subcategory}</div>
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
                  {reviewStatusLabel(t, ptStatus)}
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
                  {reviewStatusLabel(t, wwfStatus)}
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
                  <option value="">{t("validation.action.change")}</option>
                  {PT_GROUP_OPTIONS.map((g) => (
                    <option key={g} value={g}>
                      {t(PT_GROUP_LABEL_KEYS[g])}
                    </option>
                  ))}
                </select>
                <span title={t("validation.action.correctPt")}>
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
                  <span title={t("validation.action.acceptPt")}>
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
                <span title={t("validation.action.correctWwf")}>
                  <Button
                    variant="ghost"
                    onClick={() => setWwfModalRow(row)}
                    disabled={busyAcceptWwf}
                  >
                    {t("validation.action.correct")}
                  </Button>
                </span>
                {wwfInQueue && (
                  <span title={t("validation.action.acceptWwf")}>
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
        className="align-top transition-colors hover:bg-mint-50/50"
      >
        <td className="py-2.5 pl-4 pr-3">
          <div className="font-medium text-forest-900">{row.product_name}</div>
          {row.brand && <div className="text-ink-soft">{row.brand}</div>}
          <div className="mt-1">
            <Pill tone={isWwfRow ? "warn" : "brand"}>
              {isWwfRow ? t("validation.col.wwf") : t("validation.col.proteinTracker")}
            </Pill>
          </div>
        </td>
        <td className="py-2.5 pr-3 text-ink-muted">
          {row.retailer_category ?? "—"}
          {row.retailer_subcategory && (
            <div className="text-gray-400">{row.retailer_subcategory}</div>
          )}
        </td>
        {/* Phase Demo-Golden-fix — the row's category cells MUST match the
            header layout, which is chosen by ``isReviewWwfView`` (the
            methodology filter), NOT by the per-row methodology. Previously a
            WWF review row rendered the 3-column WWF-detail layout even when
            the active header was the 2-column PT/WWF "all" layout, shifting
            every following cell (the WWF food group appeared under the PT
            header). Branch on ``isReviewWwfView`` so columns always align. */}
        {isReviewWwfView ? (
          <>
            <td className="py-2 pr-3">
              {row.wwf_food_group ? (
                <Pill tone={wwfCategoryTone(row)}>
                  {wwfCategoryLabel(t, row)}
                </Pill>
              ) : (
                <span className="text-gray-400">—</span>
              )}
            </td>
            <td className="py-2 pr-3 text-gray-600">
              {/* Subgroup is the schema filler for composites — hide it; the
                  Step-1 bucket is shown in the composite column instead. */}
              {row.wwf_is_composite
                ? "—"
                : wwfSubgroupLabel(t, wwfSubgroupOf(row))}
            </td>
            <td className="py-2 pr-3 text-gray-600">
              {row.wwf_is_composite ? (
                <span title={row.wwf_composite_step1_bucket ?? ""}>
                  {wwfBucketLabel(t, row.wwf_composite_step1_bucket ?? null)}
                </span>
              ) : (
                <span className="text-gray-400">—</span>
              )}
            </td>
          </>
        ) : (
          <>
            {/* PT column — the product's Protein Tracker category (shown for
                every review row so the cell lines up under the PT header). */}
            <td className="py-2 pr-3">
              {row.pt_group ? (
                <Pill tone={PT_GROUP_TONE[row.pt_group]}>
                  {ptGroupLabel(t, row.pt_group)}
                </Pill>
              ) : (
                <span className="text-gray-400">—</span>
              )}
            </td>
            {/* WWF column — composite-aware label (used by both PT and WWF
                review rows in the combined "all" view). Composites show
                "Composite · {bucket}", never the schema-filler food group. */}
            {wwfEnabled && (
              <td className="py-2 pr-3">
                {row.wwf_food_group ? (
                  <Pill tone={wwfCategoryTone(row)}>
                    {wwfCategoryLabel(t, row)}
                  </Pill>
                ) : (
                  <span className="text-gray-400">—</span>
                )}
              </td>
            )}
          </>
        )}
        <td className="py-2 pr-3 text-gray-600">
          {sourceLabel(t, isWwfRow ? row.wwf_source : row.pt_source)}
        </td>
        <td className="py-2 pr-3 text-gray-600">
          {confidenceText(isWwfRow ? row.wwf_confidence : row.pt_confidence)}
        </td>
        <td className="py-2 pr-3">
          {status ? (
            <Pill tone={reviewStatusTone(status)}>
              {reviewStatusLabel(t, status)}
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
                {t("validation.action.correct")}
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
                <option value="">{t("validation.action.change")}</option>
                {PT_GROUP_OPTIONS.map((g) => (
                  <option key={g} value={g}>
                    {t(PT_GROUP_LABEL_KEYS[g])}
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
