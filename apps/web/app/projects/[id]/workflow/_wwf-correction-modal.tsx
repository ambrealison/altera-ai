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
import type { ClassificationRow, WWFCorrectionPayload } from "@/lib/api";

type FoodGroup = WWFCorrectionPayload["wwf_food_group"];

const FOOD_GROUP_OPTIONS: { value: FoodGroup; label: string }[] = [
  { value: "FG1", label: "FG1 — Sources de protéines" },
  { value: "FG2", label: "FG2 — Produits laitiers et alternatives" },
  { value: "FG3", label: "FG3 — Matières grasses et huiles" },
  { value: "FG4", label: "FG4 — Fruits et légumes" },
  { value: "FG5", label: "FG5 — Céréales" },
  { value: "FG6", label: "FG6 — Tubercules" },
  { value: "FG7", label: "FG7 — Snacks riches en gras/sel/sucre" },
  { value: "out_of_scope", label: "Hors périmètre" },
  { value: "unknown", label: "Inconnu" },
];

const FG1_OPTIONS: { value: string; label: string }[] = [
  { value: "red_meat", label: "Viande rouge" },
  { value: "poultry", label: "Volaille" },
  { value: "processed_meats_alternatives", label: "Viandes transformées / alternatives" },
  { value: "seafood", label: "Poisson & fruits de mer" },
  { value: "eggs", label: "Œufs" },
  { value: "legumes", label: "Légumineuses" },
  { value: "nuts_seeds", label: "Noix & graines" },
  { value: "alternative_protein_sources", label: "Sources protéiques alternatives" },
  { value: "meat_egg_seafood_alternatives", label: "Alternatives viande/œuf/poisson" },
];

const FG2_OPTIONS: { value: string; label: string }[] = [
  { value: "cheese", label: "Produit laitier animal — Fromage" },
  { value: "other_dairy_animal", label: "Produit laitier animal — Autre" },
  { value: "dairy_alternative_plant", label: "Alternative végétale aux produits laitiers" },
];

const FG3_OPTIONS: { value: string; label: string }[] = [
  { value: "plant_based_fat", label: "Matières grasses végétales" },
  { value: "animal_based_fat", label: "Matières grasses animales" },
];

const FG5_OPTIONS: { value: string; label: string }[] = [
  { value: "whole_grain", label: "Céréales complètes" },
  { value: "refined_grain", label: "Céréales raffinées" },
];

const FG7_OPTIONS: { value: string; label: string }[] = [
  { value: "plant_based_snack", label: "Snack végétal" },
  { value: "animal_based_snack", label: "Snack animal" },
];

const BUCKET_OPTIONS: { value: string; label: string }[] = [
  { value: "meat_based", label: "À base de viande" },
  { value: "seafood_based", label: "À base de poisson/fruits de mer" },
  { value: "vegetarian", label: "Végétarien" },
  { value: "vegan", label: "Végane" },
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
 *  is valid; otherwise a French explanation of what's missing. */
export function validateWwfModalState(s: WWFModalState): string | null {
  const fg = s.food_group;
  if (fg === "out_of_scope" || fg === "unknown") {
    if (s.is_composite) {
      return "Hors périmètre / Inconnu ne peuvent pas être composite.";
    }
    return null;
  }
  if (fg === "FG1" && !s.fg1) return "Choisissez un sous-groupe FG1.";
  if (fg === "FG2" && !s.fg2) return "Choisissez un sous-groupe FG2.";
  if (fg === "FG3" && !s.fg3) return "Choisissez un sous-groupe FG3.";
  if (fg === "FG5" && !s.fg5) return "Choisissez le type de céréale (FG5).";
  if (fg === "FG7" && !s.fg7) return "Choisissez le type de snack (FG7).";
  if (s.is_composite && !s.bucket) {
    return "Choisissez le bucket composite (à base de viande / poisson / vegetarien / végane).";
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
      setError(e instanceof Error ? e.message : "Échec de la correction.");
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
      aria-label="Corriger la classification WWF"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-lg rounded-lg bg-white shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="border-b border-gray-200 px-4 py-3">
          <h3 className="text-base font-semibold text-gray-800">
            Corriger la classification WWF
          </h3>
          <p className="mt-0.5 text-xs text-gray-500">
            {row.product_name}
            {row.brand ? ` · ${row.brand}` : ""}
          </p>
        </div>

        <div className="space-y-3 px-4 py-3">
          {/* Food group */}
          <div>
            <label className="block text-xs font-medium uppercase tracking-wide text-gray-500">
              Groupe alimentaire
            </label>
            <select
              value={state.food_group}
              onChange={(e) =>
                patch({ food_group: e.target.value as FoodGroup })
              }
              className="mt-1 w-full rounded border border-gray-300 bg-white px-2 py-1 text-sm focus:border-brand-500 focus:outline-none"
            >
              {FOOD_GROUP_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </div>

          {/* Subgroup (conditional) */}
          {fg === "FG1" && (
            <ConditionalSelect
              label="Sous-groupe"
              value={state.fg1}
              onChange={(v) => patch({ fg1: v })}
              options={FG1_OPTIONS}
            />
          )}
          {fg === "FG2" && (
            <ConditionalSelect
              label="Type"
              value={state.fg2}
              onChange={(v) => patch({ fg2: v })}
              options={FG2_OPTIONS}
            />
          )}
          {fg === "FG3" && (
            <ConditionalSelect
              label="Type"
              value={state.fg3}
              onChange={(v) => patch({ fg3: v })}
              options={FG3_OPTIONS}
            />
          )}
          {fg === "FG5" && (
            <ConditionalSelect
              label="Type"
              value={state.fg5}
              onChange={(v) => patch({ fg5: v })}
              options={FG5_OPTIONS}
            />
          )}
          {fg === "FG7" && (
            <ConditionalSelect
              label="Type"
              value={state.fg7}
              onChange={(v) => patch({ fg7: v })}
              options={FG7_OPTIONS}
            />
          )}

          {/* Composite */}
          {!isSystemState && (
            <div>
              <label className="flex items-center gap-2 text-sm text-gray-700">
                <input
                  type="checkbox"
                  checked={state.is_composite}
                  onChange={(e) =>
                    patch({ is_composite: e.target.checked })
                  }
                />
                Produit composite
              </label>
            </div>
          )}

          {/* Composite bucket (conditional on composite) */}
          {!isSystemState && state.is_composite && (
            <ConditionalSelect
              label="Bucket composite"
              value={state.bucket}
              onChange={(v) => patch({ bucket: v })}
              options={BUCKET_OPTIONS}
            />
          )}

          {validationError && (
            <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
              {validationError}
            </div>
          )}
          {error && (
            <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-800">
              {error}
            </div>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-gray-200 px-4 py-3">
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Annuler
          </Button>
          <Button
            onClick={() => void handleSave()}
            disabled={busy || validationError !== null}
          >
            {busy ? "Enregistrement…" : "Enregistrer"}
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
  options: { value: string; label: string }[];
}) {
  return (
    <div>
      <label className="block text-xs font-medium uppercase tracking-wide text-gray-500">
        {label}
      </label>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="mt-1 w-full rounded border border-gray-300 bg-white px-2 py-1 text-sm focus:border-brand-500 focus:outline-none"
      >
        <option value="">— Choisir —</option>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </div>
  );
}
