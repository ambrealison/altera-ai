"use client";

/**
 * Phase 34L — Inline nutrition validation table for wizard Step 6.
 *
 * Sits between NEVO (Step 5) and Calcul (Step 7). Shows every PT-
 * eligible product with the final protein values that would be used
 * in the calculation plus their provenance (retailer / NEVO / manual
 * / missing). Lets the user manually fill in missing values without
 * leaving the wizard.
 *
 * Out of scope for this minimal cut (documented in ROADMAP as 34M):
 * - bulk-edit, "exclude from calculation" UI, AI-estimated split UI,
 *   confidence range filter sliders. The endpoint supports paginated
 *   server-side filtering already; the UI exposes the most important
 *   filters (status, source, search) plus per-row manual edit.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { Button, Card, Pill, Skeleton } from "@/components/ui";
import { useT } from "@/lib/i18n";
import type {
  NutritionValidationRow,
  NutritionValidationsResponse,
} from "@/lib/api";
import { ApiError, createApi } from "@/lib/api";

const PAGE_SIZE = 25;

// Phase 34M — extended status palette to surface confidence tiers
// from the backend. Codes map to i18n key suffixes resolved at render.
const STATUS_LABEL_KEYS: Record<string, string> = {
  ready: "nutrition.status.ready",
  ready_medium_confidence: "nutrition.status.ready_medium_confidence",
  needs_review: "nutrition.status.needs_review",
  needs_review_low_confidence: "nutrition.status.needs_review_low_confidence",
  suggested_very_low_confidence: "nutrition.status.suggested_very_low_confidence",
  missing: "nutrition.status.missing",
  excluded: "nutrition.status.excluded",
};
const STATUS_TONES: Record<string, "ok" | "warn" | "neutral" | "brand"> = {
  ready: "ok",
  ready_medium_confidence: "ok",
  needs_review: "warn",
  needs_review_low_confidence: "warn",
  suggested_very_low_confidence: "warn",
  missing: "neutral",
  excluded: "neutral",
};
const SOURCE_LABEL_KEYS: Record<string, string> = {
  retailer_csv: "nutrition.source.retailer_csv",
  nevo: "nutrition.source.nevo",
  ciqual: "nutrition.source.ciqual",
  manual: "nutrition.source.manual",
  missing: "nutrition.source.missing",
};

export function NutritionTable({
  projectId,
  accessToken,
  onChanged,
}: {
  projectId: string;
  accessToken: string | null;
  onChanged?: () => void | Promise<void>;
}) {
  const t = useT();
  const api = useMemo(() => createApi(accessToken), [accessToken]);
  const [filters, setFilters] = useState<{
    status?: "ready" | "needs_review" | "missing" | "excluded";
    source?: "retailer_csv" | "nevo" | "ciqual" | "manual" | "missing";
    product_search?: string;
  }>({});
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<NutritionValidationsResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Per-row manual edit state.
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState<{
    protein_pct: string;
    plant_protein_pct: string;
    animal_protein_pct: string;
  }>({
    protein_pct: "",
    plant_protein_pct: "",
    animal_protein_pct: "",
  });
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      const r = await api.listNutritionValidations(projectId, {
        ...filters,
        limit: PAGE_SIZE,
        offset,
      });
      setData(r);
    } catch (e) {
      setLoadError(
        e instanceof Error ? e.message : t("nutrition.error.load"),
      );
    }
  }, [api, projectId, filters, offset, t]);

  useEffect(() => {
    void load();
  }, [load]);

  function startEdit(row: NutritionValidationRow) {
    setEditing(row.product_id);
    setDraft({
      protein_pct: row.protein_pct ?? "",
      plant_protein_pct: row.plant_protein_pct ?? "",
      animal_protein_pct: row.animal_protein_pct ?? "",
    });
    setSubmitError(null);
  }

  async function saveEdit(row: NutritionValidationRow) {
    setSubmitting(true);
    setSubmitError(null);
    try {
      const protein = Number(draft.protein_pct);
      const plant = Number(draft.plant_protein_pct);
      const animal = Number(draft.animal_protein_pct);
      if (
        !Number.isFinite(protein) ||
        !Number.isFinite(plant) ||
        !Number.isFinite(animal)
      ) {
        setSubmitError(t("nutrition.error.numeric"));
        return;
      }
      if (protein < 0 || plant < 0 || animal < 0) {
        setSubmitError(t("nutrition.error.positive"));
        return;
      }
      await api.submitManualNutrition(projectId, row.product_id, {
        protein_pct: protein,
        plant_protein_pct: plant,
        animal_protein_pct: animal,
      });
      setEditing(null);
      await load();
      await onChanged?.();
    } catch (e) {
      if (e instanceof ApiError && typeof e.detail === "object" && e.detail) {
        const d = e.detail as { message?: string };
        setSubmitError(d.message ?? String(e));
      } else {
        setSubmitError(
          e instanceof Error ? e.message : t("nutrition.error.save"),
        );
      }
    } finally {
      setSubmitting(false);
    }
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
          <Skeleton className="h-56 w-full" />
        </div>
      </Card>
    );
  }

  const pageCount = Math.max(1, Math.ceil(data.total / PAGE_SIZE));
  const pageIdx = Math.floor(offset / PAGE_SIZE);

  return (
    <Card>
      {/* Aggregate counters */}
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="font-medium text-forest-700">
          {t("nutrition.productCount").replace("{n}", String(data.total))}
        </span>
        {Object.entries(data.counts_by_status).map(([k, v]) => (
          <Pill
            key={k}
            tone={
              k === "ready"
                ? "ok"
                : k === "needs_review"
                  ? "warn"
                  : "neutral"
            }
          >
            {(STATUS_LABEL_KEYS[k] ? t(STATUS_LABEL_KEYS[k]) : k)}: {v}
          </Pill>
        ))}
      </div>

      {/* Filters */}
      <div className="mt-4 grid grid-cols-1 gap-2 rounded-2xl border border-line bg-mint-50/50 p-2 sm:grid-cols-3">
        <input
          type="text"
          placeholder={t("nutrition.filter.searchPlaceholder")}
          value={filters.product_search ?? ""}
          onChange={(e) => {
            setOffset(0);
            setFilters((p) => ({ ...p, product_search: e.target.value || undefined }));
          }}
          className="rounded-xl border border-line bg-white px-3 py-1.5 text-xs text-gray-800 shadow-soft focus:border-brand-400 focus:outline-none"
        />
        <select
          value={filters.status ?? ""}
          onChange={(e) => {
            setOffset(0);
            setFilters((p) => ({
              ...p,
              status: (e.target.value || undefined) as typeof filters.status,
            }));
          }}
          className="rounded-xl border border-line bg-white px-2.5 py-1.5 text-xs text-gray-800 shadow-soft focus:border-brand-400 focus:outline-none"
        >
          <option value="">{t("nutrition.filter.allStatuses")}</option>
          <option value="ready">{t("nutrition.filter.statusReady")}</option>
          <option value="needs_review">{t("nutrition.status.needs_review")}</option>
          <option value="missing">{t("nutrition.status.missing")}</option>
        </select>
        <select
          value={filters.source ?? ""}
          onChange={(e) => {
            setOffset(0);
            setFilters((p) => ({
              ...p,
              source: (e.target.value || undefined) as typeof filters.source,
            }));
          }}
          className="rounded-xl border border-line bg-white px-2.5 py-1.5 text-xs text-gray-800 shadow-soft focus:border-brand-400 focus:outline-none"
        >
          <option value="">{t("nutrition.filter.allSources")}</option>
          <option value="retailer_csv">{t("nutrition.source.retailer_csv")}</option>
          <option value="nevo">{t("nutrition.source.nevo")}</option>
          <option value="manual">{t("nutrition.source.manual")}</option>
          <option value="missing">{t("nutrition.source.missing")}</option>
        </select>
      </div>

      {/* Table */}
      <div className="scroll-soft mt-4 overflow-x-auto rounded-2xl border border-line">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-line bg-mint-50/70 text-left text-[11px] uppercase tracking-wider text-ink-soft">
              <th className="py-2.5 pl-4 pr-3 font-semibold">{t("nutrition.col.product")}</th>
              <th className="py-2.5 pr-3 font-semibold">{t("nutrition.col.pt")}</th>
              <th className="py-2.5 pr-3 font-semibold">{t("nutrition.col.protein")}</th>
              <th className="py-2.5 pr-3 font-semibold">{t("nutrition.col.plant")}</th>
              <th className="py-2.5 pr-3 font-semibold">{t("nutrition.col.animal")}</th>
              <th className="py-2.5 pr-3 font-semibold">{t("nutrition.col.source")}</th>
              <th className="py-2.5 pr-3 font-semibold">{t("nutrition.col.status")}</th>
              <th className="py-2.5 pr-3 font-semibold">{t("nutrition.col.action")}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line-soft">
            {data.items.length === 0 && (
              <tr>
                <td colSpan={8} className="px-4 py-10 text-center">
                  <div className="text-sm font-medium text-forest-700">
                    {t("nutrition.empty")}
                  </div>
                </td>
              </tr>
            )}
            {data.items.map((row) => {
              const isEditing = editing === row.product_id;
              return (
                <tr
                  key={row.product_id}
                  className="align-top transition-colors hover:bg-mint-50/50"
                >
                  <td className="py-2.5 pl-4 pr-3 font-medium text-forest-900">
                    {row.product_name}
                    {(row.source_display_label ?? row.reason) && (
                      <div className="mt-0.5 text-xs text-ink-soft">
                        {row.source_display_label ?? row.reason}
                      </div>
                    )}
                  </td>
                  <td className="py-2.5 pr-3 text-ink-muted">
                    {row.pt_group ?? "—"}
                  </td>
                  {isEditing ? (
                    <>
                      <td className="py-2 pr-3">
                        <input
                          type="number"
                          step={0.1}
                          min={0}
                          max={100}
                          value={draft.protein_pct}
                          onChange={(e) =>
                            setDraft((d) => ({
                              ...d,
                              protein_pct: e.target.value,
                            }))
                          }
                          className="w-16 rounded-lg border border-line bg-white px-2 py-1 text-xs focus:border-brand-400 focus:outline-none"
                        />
                      </td>
                      <td className="py-2 pr-3">
                        <input
                          type="number"
                          step={0.1}
                          min={0}
                          max={100}
                          value={draft.plant_protein_pct}
                          onChange={(e) =>
                            setDraft((d) => ({
                              ...d,
                              plant_protein_pct: e.target.value,
                            }))
                          }
                          className="w-16 rounded-lg border border-line bg-white px-2 py-1 text-xs focus:border-brand-400 focus:outline-none"
                        />
                      </td>
                      <td className="py-2 pr-3">
                        <input
                          type="number"
                          step={0.1}
                          min={0}
                          max={100}
                          value={draft.animal_protein_pct}
                          onChange={(e) =>
                            setDraft((d) => ({
                              ...d,
                              animal_protein_pct: e.target.value,
                            }))
                          }
                          className="w-16 rounded-lg border border-line bg-white px-2 py-1 text-xs focus:border-brand-400 focus:outline-none"
                        />
                      </td>
                    </>
                  ) : (
                    <>
                      <td className="py-2.5 pr-3 font-medium text-forest-700">
                        {row.protein_pct ?? "—"}
                      </td>
                      <td className="py-2.5 pr-3 text-ink-muted">
                        {row.plant_protein_pct ?? "—"}
                      </td>
                      <td className="py-2.5 pr-3 text-ink-muted">
                        {row.animal_protein_pct ?? "—"}
                      </td>
                    </>
                  )}
                  <td className="py-2.5 pr-3 text-ink-muted">
                    {SOURCE_LABEL_KEYS[row.source]
                      ? t(SOURCE_LABEL_KEYS[row.source])
                      : row.source}
                  </td>
                  <td className="py-2.5 pr-3">
                    <Pill tone={STATUS_TONES[row.status] ?? "neutral"}>
                      {STATUS_LABEL_KEYS[row.status]
                        ? t(STATUS_LABEL_KEYS[row.status])
                        : row.status}
                    </Pill>
                  </td>
                  <td className="py-2">
                    {isEditing ? (
                      <div className="flex items-center gap-1">
                        <Button
                          variant="secondary"
                          onClick={() => void saveEdit(row)}
                          disabled={submitting}
                        >
                          {submitting ? "…" : t("nutrition.saveEdit")}
                        </Button>
                        <Button
                          variant="ghost"
                          onClick={() => setEditing(null)}
                          disabled={submitting}
                        >
                          ✗
                        </Button>
                      </div>
                    ) : (
                      <Button variant="ghost" onClick={() => startEdit(row)}>
                        {t("common.edit")}
                      </Button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {submitError && (
        <div className="mt-3 rounded-xl border border-danger-100 bg-danger-50 px-3 py-2 text-sm text-danger-700">
          {submitError}
        </div>
      )}

      {/* Pagination */}
      <div className="mt-3 flex items-center justify-between text-xs text-ink-muted">
        <span>
          {t("nutrition.page")} <span className="font-semibold text-forest-700">{pageIdx + 1}</span> / {pageCount}
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
              setOffset(
                Math.min((pageCount - 1) * PAGE_SIZE, offset + PAGE_SIZE),
              )
            }
            disabled={pageIdx >= pageCount - 1}
          >
            →
          </Button>
        </div>
      </div>
    </Card>
  );
}
