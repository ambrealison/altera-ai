"use client";

/**
 * Phase WWF-P — WWF correction modal.
 *
 * Lets a reviewer pin every WWF field (food group + subgroup +
 * composite + bucket) directly. Posts via ``submitDecision`` with
 * the explicit ``wwf`` payload introduced in Phase WWF-O.
 *
 * The form enforces the same domain invariants the backend would
 * surface as 400s, but in the UI so the user sees the constraint
 * before submitting:
 *
 *   * FG1 requires fg1_subgroup
 *   * FG2 requires fg2_subgroup
 *   * FG3 requires fg3_subgroup
 *   * FG5 requires fg5_grain_kind
 *   * FG7 requires fg7_snack_kind
 *   * Composite requires composite_step1_bucket
 *   * out_of_scope / unknown carry no subgroup or bucket
 */

import { useMemo, useState } from "react";

import { Button } from "@/components/ui";
import { useT } from "@/lib/i18n";
import type { ClassificationRow, WWFCorrectionPayload } from "@/lib/api";

type FoodGroup = WWFCorrectionPayload["wwf_food_group"];

// ``labelKey`` holds the i18n key; the CODE in ``value`` is canonical and
// stays untouched. Components resolve the label via ``t(labelKey)``.
const FOOD_GROUP_OPTIONS: { value: FoodGroup; labelKey: string }[] = [
  { value: "FG1", labelKey: "correction.fg.FG1" },
  { value: "FG2", labelKey: "correction.fg.FG2" },
  { value: "FG3", labelKey: "correction.fg.FG3" },
  { value: "FG4", labelKey: "correction.fg.FG4" },
  { value: "FG5", labelKey: "correction.fg.FG5" },
  { value: "FG6", labelKey: "correction.fg.FG6" },
  { value: "FG7", labelKey: "correction.fg.FG7" },
  { value: "out_of_scope", labelKey: "correction.fg.out_of_scope" },
  { value: "unknown", labelKey: "correction.fg.unknown" },
];

const FG1_OPTIONS: { value: string; labelKey: string }[] = [
  { value: "red_meat", labelKey: "correction.fg1.red_meat" },
  { value: "poultry", labelKey: "correction.fg1.poultry" },
  { value: "processed_meats_alternatives", labelKey: "correction.fg1.processed_meats_alternatives" },
  { value: "seafood", labelKey: "correction.fg1.seafood" },
  { value: "eggs", labelKey: "correction.fg1.eggs" },
  { value: "legumes", labelKey: "correction.fg1.legumes" },
  { value: "nuts_seeds", labelKey: "correction.fg1.nuts_seeds" },
  { value: "alternative_protein_sources", labelKey: "correction.fg1.alternative_protein_sources" },
  { value: "meat_egg_seafood_alternatives", labelKey: "correction.fg1.meat_egg_seafood_alternatives" },
];

const FG2_OPTIONS: { value: string; labelKey: string }[] = [
  { value: "cheese", labelKey: "correction.fg2.cheese" },
  { value: "other_dairy_animal", labelKey: "correction.fg2.other_dairy_animal" },
  { value: "dairy_alternative_plant", labelKey: "correction.fg2.dairy_alternative_plant" },
];

const FG3_OPTIONS: { value: string; labelKey: string }[] = [
  { value: "plant_based_fat", labelKey: "correction.fg3.plant_based_fat" },
  { value: "animal_based_fat", labelKey: "correction.fg3.animal_based_fat" },
];

const FG5_OPTIONS: { value: string; labelKey: string }[] = [
  { value: "whole_grain", labelKey: "correction.fg5.whole_grain" },
  { value: "refined_grain", labelKey: "correction.fg5.refined_grain" },
];

const FG7_OPTIONS: { value: string; labelKey: string }[] = [
  { value: "plant_based_snack", labelKey: "correction.fg7.plant_based_snack" },
  { value: "animal_based_snack", labelKey: "correction.fg7.animal_based_snack" },
];

const BUCKET_OPTIONS: { value: string; labelKey: string }[] = [
  { value: "meat_based", labelKey: "correction.bucket.meat_based" },
  { value: "seafood_based", labelKey: "correction.bucket.seafood_based" },
  { value: "vegetarian", labelKey: "correction.bucket.vegetarian" },
  { value: "vegan", labelKey: "correction.bucket.vegan" },
];

interface WWFModalState {
  food_group: FoodGroup;
  is_composite: boolean;
  fg1: string;
  fg2: string;
  fg3: string;
  fg5: string;
  fg7: string;
  bucket: string;
}

function _initialState(row: ClassificationRow): WWFModalState {
  return {
    food_group: (row.wwf_food_group as FoodGroup) ?? "FG1",
    is_composite: row.wwf_is_composite ?? false,
    fg1: row.wwf_fg1_subgroup ?? "",
    fg2: row.wwf_fg2_subgroup ?? "",
    fg3: row.wwf_fg3_subgroup ?? "",
    fg5: row.wwf_fg5_grain_kind ?? "",
    fg7: row.wwf_fg7_snack_kind ?? "",
    bucket: row.wwf_composite_step1_bucket ?? "",
  };
}

/** Phase WWF-P — pure function (also exported for tests if a frontend
 *  test framework lands later). Returns ``null`` when the modal state
 *  is valid; otherwise an i18n key describing what's missing (resolved
 *  to a display string by the caller via ``t()``). */
export function validateWwfModalState(s: WWFModalState): string | null {
  const fg = s.food_group;
  if (fg === "out_of_scope" || fg === "unknown") {
    if (s.is_composite) {
      return "correction.validation.systemNotComposite";
    }
    return null;
  }
  if (fg === "FG1" && !s.fg1) return "correction.validation.fg1Subgroup";
  if (fg === "FG2" && !s.fg2) return "correction.validation.fg2Subgroup";
  if (fg === "FG3" && !s.fg3) return "correction.validation.fg3Subgroup";
  if (fg === "FG5" && !s.fg5) return "correction.validation.fg5Grain";
  if (fg === "FG7" && !s.fg7) return "correction.validation.fg7Snack";
  if (s.is_composite && !s.bucket) {
    return "correction.validation.compositeBucket";
  }
  return null;
}

/** Phase WWF-P — pure function (exported for tests). Converts the
 *  validated modal state into the WWFCorrectionPayload the backend
 *  expects. */
export function modalStateToPayload(s: WWFModalState): WWFCorrectionPayload {
  const fg = s.food_group;
  const isSystemState = fg === "out_of_scope" || fg === "unknown";
  return {
    wwf_food_group: fg,
    wwf_is_composite: isSystemState ? false : s.is_composite,
    fg1_subgroup:
      !isSystemState && fg === "FG1" && s.fg1 ? (s.fg1 as never) : null,
    fg2_subgroup:
      !isSystemState && fg === "FG2" && s.fg2 ? (s.fg2 as never) : null,
    fg3_subgroup:
      !isSystemState && fg === "FG3" && s.fg3 ? (s.fg3 as never) : null,
    fg5_grain_kind:
      !isSystemState && fg === "FG5" && s.fg5 ? (s.fg5 as never) : null,
    fg7_snack_kind:
      !isSystemState && fg === "FG7" && s.fg7 ? (s.fg7 as never) : null,
    composite_step1_bucket:
      !isSystemState && s.is_composite && s.bucket
        ? (s.bucket as never)
        : null,
  };
}

export function WwfCorrectionModal({
  row,
  onClose,
  onSubmit,
}: {
  row: ClassificationRow;
  onClose: () => void;
  onSubmit: (payload: WWFCorrectionPayload) => Promise<void>;
}) {
  const t = useT();
  const [state, setState] = useState<WWFModalState>(() =>
    _initialState(row),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const validationError = useMemo(
    () => validateWwfModalState(state),
    [state],
  );
  const fg = state.food_group;
  const isSystemState = fg === "out_of_scope" || fg === "unknown";

  async function handleSave() {
    if (validationError) return;
    setBusy(true);
    setError(null);
    try {
      await onSubmit(modalStateToPayload(state));
      onClose();
    } catch (e) {
      setError(
        e instanceof Error ? e.message : t("correction.error.submit"),
      );
    } finally {
      setBusy(false);
    }
  }

  function patch(p: Partial<WWFModalState>) {
    setState((s) => ({ ...s, ...p }));
  }

  return (
    <div
      role="dialog"
      aria-label={t("correction.title")}
      className="fixed inset-0 z-50 flex animate-fade-in items-center justify-center bg-forest-900/40 p-4 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="w-full max-w-lg animate-scale-in overflow-hidden rounded-2xl border border-line bg-white shadow-card-hover"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="border-b border-line-soft bg-mint-50/60 px-5 py-4">
          <h3 className="text-base font-semibold text-forest-900">
            {t("correction.title")}
          </h3>
          <p className="mt-0.5 text-xs text-ink-muted">
            {row.product_name}
            {row.brand ? ` · ${row.brand}` : ""}
          </p>
        </div>

        <div className="space-y-3 px-5 py-4">
          {/* Food group */}
          <div>
            <label className="block text-[11px] font-medium uppercase tracking-wide text-ink-soft">
              {t("correction.label.foodGroup")}
            </label>
            <select
              value={state.food_group}
              onChange={(e) =>
                patch({ food_group: e.target.value as FoodGroup })
              }
              className="mt-1 w-full rounded-xl border border-line bg-white px-3 py-2 text-sm focus:border-brand-400 focus:outline-none"
            >
              {FOOD_GROUP_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {t(o.labelKey)}
                </option>
              ))}
            </select>
          </div>

          {/* Subgroup (conditional) */}
          {fg === "FG1" && (
            <ConditionalSelect
              label={t("correction.label.subgroup")}
              value={state.fg1}
              onChange={(v) => patch({ fg1: v })}
              options={FG1_OPTIONS}
            />
          )}
          {fg === "FG2" && (
            <ConditionalSelect
              label={t("correction.label.type")}
              value={state.fg2}
              onChange={(v) => patch({ fg2: v })}
              options={FG2_OPTIONS}
            />
          )}
          {fg === "FG3" && (
            <ConditionalSelect
              label={t("correction.label.type")}
              value={state.fg3}
              onChange={(v) => patch({ fg3: v })}
              options={FG3_OPTIONS}
            />
          )}
          {fg === "FG5" && (
            <ConditionalSelect
              label={t("correction.label.type")}
              value={state.fg5}
              onChange={(v) => patch({ fg5: v })}
              options={FG5_OPTIONS}
            />
          )}
          {fg === "FG7" && (
            <ConditionalSelect
              label={t("correction.label.type")}
              value={state.fg7}
              onChange={(v) => patch({ fg7: v })}
              options={FG7_OPTIONS}
            />
          )}

          {/* Composite */}
          {!isSystemState && (
            <div>
              <label className="flex items-center gap-2 rounded-xl border border-line bg-mint-50/50 px-3 py-2 text-sm text-forest-700">
                <input
                  type="checkbox"
                  checked={state.is_composite}
                  onChange={(e) =>
                    patch({ is_composite: e.target.checked })
                  }
                  className="accent-brand-600"
                />
                {t("correction.label.composite")}
              </label>
            </div>
          )}

          {/* Composite bucket (conditional on composite) */}
          {!isSystemState && state.is_composite && (
            <ConditionalSelect
              label={t("correction.label.compositeBucket")}
              value={state.bucket}
              onChange={(v) => patch({ bucket: v })}
              options={BUCKET_OPTIONS}
            />
          )}

          {validationError && (
            <div className="rounded-xl border border-warn-100 bg-warn-50 px-3 py-2 text-xs text-warn-700">
              {t(validationError)}
            </div>
          )}
          {error && (
            <div className="rounded-xl border border-danger-100 bg-danger-50 px-3 py-2 text-xs text-danger-700">
              {error}
            </div>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-line-soft bg-mint-50/40 px-5 py-3">
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            {t("common.cancel")}
          </Button>
          <Button
            onClick={() => void handleSave()}
            disabled={busy || validationError !== null}
          >
            {busy ? t("common.saving") : t("common.save")}
          </Button>
        </div>
      </div>
    </div>
  );
}

function ConditionalSelect({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: { value: string; labelKey: string }[];
}) {
  const t = useT();
  return (
    <div>
      <label className="block text-[11px] font-medium uppercase tracking-wide text-ink-soft">
        {label}
      </label>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="mt-1 w-full rounded-xl border border-line bg-white px-3 py-2 text-sm focus:border-brand-400 focus:outline-none"
      >
        <option value="">{t("correction.choose")}</option>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {t(o.labelKey)}
          </option>
        ))}
      </select>
    </div>
  );
}
