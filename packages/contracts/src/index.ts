/**
 * @altera-ai/contracts
 *
 * Shared schema and type contracts between the backend (Python) and the
 * frontend (TypeScript).
 *
 * Status: placeholder. Real contracts land alongside Phase 4 (Pydantic
 * domain models) and Phase 11 (export shapes). The intention is that the
 * Pydantic models on the backend and the TypeScript types here remain in
 * lockstep, generated from a single source of truth.
 */

export const CONTRACTS_VERSION = "0.0.1" as const;

export type Methodology = "protein_tracker" | "wwf";

export type PTGroup =
  | "plant_based_core"
  | "plant_based_non_core"
  | "composite_products"
  | "animal_core";

export type WWFFoodGroup =
  | "FG1"
  | "FG2"
  | "FG3"
  | "FG4"
  | "FG5"
  | "FG6"
  | "FG7";
