/**
 * Typed fetch client for the Altera AI HTTP API.
 *
 * Every method that talks to a protected endpoint accepts an
 * `accessToken` argument; the caller (a hook or page component)
 * pulls it from `useAuth()`. In dev-auth mode the token is null and
 * the backend falls back to the demo user.
 */

export function getApiBaseUrl(): string {
  return (
    process.env.NEXT_PUBLIC_API_BASE_URL ??
    process.env.NEXT_PUBLIC_API_URL ??
    "http://localhost:8000"
  );
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export type Methodology = "protein_tracker" | "wwf";

export type ProteinTrackerGroup =
  | "plant_based_core"
  | "plant_based_non_core"
  | "composite_products"
  | "animal_core"
  | "out_of_scope"
  | "unknown";

export type WWFFoodGroup =
  | "FG1"
  | "FG2"
  | "FG3"
  | "FG4"
  | "FG5"
  | "FG6"
  | "FG7"
  | "out_of_scope"
  | "unknown";

export type ManualReviewStatus =
  | "in_queue"
  | "reviewing"
  | "accepted"
  | "changed"
  | "deferred";

export type ManualReviewReason =
  | "low_confidence"
  | "ai_parse_failed"
  | "ai_provider_error"
  | "rule_collision"
  | "contradiction_detected"
  | "requested";

export type DecisionType = "accepted" | "changed" | "deferred";

export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;
  constructor(status: number, detail: unknown) {
    const msg =
      typeof detail === "string"
        ? `${status} ${detail}`
        : typeof detail === "object" &&
          detail !== null &&
          "message" in detail &&
          typeof (detail as { message: unknown }).message === "string"
        ? `${status} ${(detail as { message: string }).message}`
        : `${status} Error`;
    super(msg);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

export interface Project {
  id: string;
  organisation_id: string;
  name: string;
  methodologies_enabled: Methodology[];
  reporting_period_label: string;
  pt_validation_status: string;
  upload_count: number;
  review_queue_count: number;
  run_count: number;
  unclassified_pt_count: number;
}

export interface ClassificationRequiredError {
  error_code: "classification_required";
  message: string;
  unclassified_count: number;
}

// Phase 34A — guided-workflow status.
export type WorkflowStepStatus =
  | "complete"
  | "ready"
  | "needs_action"
  | "blocked"
  | "available"
  | "locked"
  | "not_needed"
  | "disabled";

export interface WorkflowBlockingReason {
  code: string;
  label: string;
  count: number;
  next_action: string | null;
}

export interface WorkflowStep {
  key: string;
  label: string;
  status: WorkflowStepStatus;
  progress_pct: number;
  counts: Record<string, number>;
  blocking_reasons: WorkflowBlockingReason[];
  // Phase 34B — wizard fields
  accessible: boolean;
  editable: boolean;
  summary: string | null;
}

export interface WorkflowNextAction {
  label: string;
  action: string;
  href: string | null;
}

/** Phase WWF-H — per-methodology classification status. The PT+WWF
 *  wizard renders one card per methodology, each with its own
 *  Run/Resume/Voir-validation CTAs and progress counts. */
export interface MethodologyClassificationCounts {
  methodology: "protein_tracker" | "wwf";
  total: number;
  classified: number;
  pending: number;
  needs_review: number;
  unknown: number;
  failed: number;
  status: WorkflowStepStatus;
}

export interface WorkflowStatus {
  project_id: string;
  methodologies_enabled: string[];
  overall_progress_pct: number;
  current_step: string;
  active_step: string | null;  // Phase 34B alias
  next_action: WorkflowNextAction | null;
  steps: WorkflowStep[];
  // Phase WWF-H — optional + backward-compatible. Empty / missing
  // for projects with no methodology enabled (defensive).
  classification_by_methodology?: Partial<
    Record<"protein_tracker" | "wwf", MethodologyClassificationCounts>
  >;
}

export interface ProductEnrichmentDetail {
  product_id: string;
  product_name: string;
  outcome: "nevo_matched" | "ciqual_matched" | "ai_matched" | "ai_needs_review" | "no_match" | "skipped_has_retailer_value" | "skipped_no_pt_fields";
  source: string | null;
  reference_name: string | null;
  match_type: string | null;
  has_split: boolean;
}

export interface ApplyReferencesSummary {
  nevo_matched: number;
  nevo_with_split: number;
  ciqual_matched: number;
  nevo_ai_assisted_matched: number;
  nevo_ai_assisted_with_split: number;
  ciqual_ai_assisted_matched: number;
  ai_needs_review: number;
  no_match: number;
  skipped_has_retailer_value: number;
  skipped_no_pt_fields: number;
  ai_enabled: boolean;
  ai_model: string | null;
  // Phase 34C — per-product enrichment outcomes.
  // Phase 34T — capped to APPLY_REFERENCES_DETAIL_LIMIT entries (100).
  // The full count lives in `product_results_total` so the wizard can
  // render "Showing first 100 of N" for large CSVs.
  product_results: ProductEnrichmentDetail[];
  product_results_total: number;
  // Phase 34D — diagnostic table sizes + hard warning when result is 0.
  nevo_total_references: number;
  ciqual_total_references: number;
  warning: string | null;
}

export interface ValidationEntry {
  row_number: number;
  field: string | null;
  code: string;
  message: string;
}

export type UploadStatus =
  | "created"
  | "upload_url_created"
  | "uploaded_to_storage"
  | "validation_pending"
  | "validation_running"
  | "validation_failed"
  | "validation_completed"
  | "ingestion_running"
  | "ingestion_failed"
  | "ingestion_completed"
  | "ready_for_classification"
  | "pending"
  | "valid"
  | "invalid"
  | (string & {});

export interface UploadResult {
  id: string;
  project_id: string;
  original_filename: string;
  status: UploadStatus;
  row_count: number | null;
  dropped_columns: string[];
  products_count: number;
  // Phase 34S — `errors` and `warnings` are CAPPED to ~50 entries.
  // The full counts live in `errors_total` / `warnings_total`. The
  // wizard renders "Showing first 50 of N" for large CSVs.
  errors: ValidationEntry[];
  warnings: ValidationEntry[];
  errors_total: number;
  warnings_total: number;
  file_size_bytes: number | null;
  checksum_sha256: string | null;
  duplicate_of: string | null;
  validation_started_at: string | null;
  validation_completed_at: string | null;
  ingestion_started_at: string | null;
  ingestion_completed_at: string | null;
}

export interface PrepareUploadResult {
  upload_id: string;
  storage_path: string;
  signed_url: string;
  expires_in: number;
}

export interface ClassifySummary {
  methodology: Methodology;
  matched: number;
  pass_through: number;
  rule_collision: number;
  queued_for_review: number;
  // Phase 34C — whether AI was active for this classify run.
  ai_enabled: boolean;
  // Phase 34D — full diagnostic counts + reason when AI did not run.
  total_products: number;
  ai_attempted: number;
  ai_accepted: number;
  ai_review: number;
  ai_failed: number;
  ai_disabled_reason:
    | "deterministic_only"
    | "classifier_disabled"
    | "provider_disabled"
    | "provider_misconfigured"
    | null;
  // Phase 34F — finer-grained AI diagnostics for the wizard's Step 4 banner.
  ai_parse_failures: number;
  ai_unsupported_category_failures: number;
  ai_provider_errors: number;
  ai_batch_count: number;
  ai_sample_errors: string[];
  // Phase 34P — retry diagnostics from the small-batch recovery pass.
  ai_retry_batches: number;
  ai_recovered_rows: number;
  // Phase 34Q — coverage counters. A row in `review_required_total`
  // still has a proposed `pt_group`; it's "needs verification", not
  // "uncategorized". The wizard's Step 4 reads these and displays
  // "N catégorisés / M acceptés / K à vérifier / J échec".
  categorized_total: number;
  accepted_total: number;
  review_required_total: number;
  out_of_scope_total: number;
  unknown_total: number;
}

// Phase 34X — async, chunked CSV ingestion jobs.

export type IngestionJobStatus =
  | "queued"
  | "running"
  | "completed"
  | "completed_with_errors"
  | "failed"
  | "cancelled";

export interface IngestionJob {
  job_id: string;
  project_id: string;
  upload_id: string;
  status: IngestionJobStatus;
  total_rows: number;
  processed_rows: number;
  inserted_products: number;
  progress_pct: number;
  errors_total: number;
  warnings_total: number;
  sample_errors: string[];
  chunk_size: number;
  started_at: string | null;
  completed_at: string | null;
  error_code: string | null;
  error_message: string | null;
}

export const INGESTION_JOB_TERMINAL_STATUSES: IngestionJobStatus[] = [
  "completed",
  "completed_with_errors",
  "failed",
  "cancelled",
];

// Phase 34R — async, chunked AI classification jobs.

export type ClassificationJobStatus =
  | "queued"
  | "running"
  | "completed"
  | "completed_with_errors"
  | "failed"
  | "cancelled";

export interface ClassificationJob {
  job_id: string;
  project_id: string;
  upload_id: string;
  methodology: string;
  status: ClassificationJobStatus;
  total_products: number;
  processed_products: number;
  progress_pct: number;
  categorized_total: number;
  accepted_total: number;
  review_required_total: number;
  failed_total: number;
  unknown_total: number;
  out_of_scope_total: number;
  retry_batches: number;
  recovered_rows: number;
  failed_product_count: number;
  started_at: string | null;
  completed_at: string | null;
  error_code: string | null;
  error_message: string | null;
  sample_errors: string[];
}

export const CLASSIFICATION_JOB_TERMINAL_STATUSES: ClassificationJobStatus[] = [
  "completed",
  "completed_with_errors",
  "failed",
  "cancelled",
];

// Phase 34F — paginated category validation table.
export interface ClassificationRow {
  product_id: string;
  product_name: string;
  brand: string | null;
  retailer_category: string | null;
  retailer_subcategory: string | null;
  pt_group: ProteinTrackerGroup | null;
  pt_source: "deterministic" | "ai" | "manual_review" | null;
  pt_confidence: number | null;
  pt_rule_id: string | null;
  pt_ai_model: string | null;
  wwf_food_group: WWFFoodGroup | null;
  wwf_source: "deterministic" | "ai" | "manual_review" | null;
  wwf_confidence: number | null;
  // Phase WWF-I — full WWF subgroup + composite payload.
  wwf_fg1_subgroup?: string | null;
  wwf_fg2_subgroup?: string | null;
  wwf_fg3_subgroup?: string | null;
  wwf_fg5_grain_kind?: string | null;
  wwf_fg7_snack_kind?: string | null;
  wwf_is_composite?: boolean | null;
  wwf_composite_step1_bucket?: string | null;
  wwf_rule_id?: string | null;
  review_status: ManualReviewStatus | null;
  // Phase WWF-N — methodology + WWF review state for the unified
  // validation table.
  methodology?: "protein_tracker" | "wwf" | null;
  wwf_review_status?: ManualReviewStatus | null;
}

export interface ClassificationsResponse {
  items: ClassificationRow[];
  total: number;
  counts_by_source: Record<string, number>;
  counts_by_pt_group: Record<string, number>;
  pt_eligible_total: number;
  // Phase WWF-R — global review-queue totals (open ``in_queue`` +
  // ``reviewing`` items) per methodology. Used by the product-mode
  // side-by-side table to render the top "Total à valider" banner.
  // Optional for backward compatibility with older backends; default
  // to 0 client-side when missing.
  pt_review_total?: number;
  wwf_review_total?: number;
}

export interface ClassificationsFilters {
  source?: "deterministic" | "ai" | "manual_review" | "unknown";
  pt_group?: ProteinTrackerGroup;
  min_confidence?: number;
  max_confidence?: number;
  review_status?: ManualReviewStatus;
  product_search?: string;
  limit?: number;
  offset?: number;
  // Phase WWF-N — unified validation table.
  view?: "products" | "review";
  methodology?: "protein_tracker" | "wwf";
}

/** Phase WWF-O — explicit WWF manual-correction payload. Lets the
 *  reviewer pin every WWF field directly instead of relying on the
 *  backend's safe-default fallback. Domain invariants are enforced
 *  server-side (FG1 requires fg1_subgroup, composite requires
 *  composite_step1_bucket, etc.); a malformed combination yields a
 *  400 with a clear error message. */
export interface WWFCorrectionPayload {
  wwf_food_group:
    | "FG1"
    | "FG2"
    | "FG3"
    | "FG4"
    | "FG5"
    | "FG6"
    | "FG7"
    | "out_of_scope"
    | "unknown";
  wwf_is_composite?: boolean;
  fg1_subgroup?:
    | "red_meat"
    | "poultry"
    | "processed_meats_alternatives"
    | "seafood"
    | "eggs"
    | "legumes"
    | "nuts_seeds"
    | "alternative_protein_sources"
    | "meat_egg_seafood_alternatives"
    | null;
  fg2_subgroup?:
    | "cheese"
    | "other_dairy_animal"
    | "dairy_alternative_plant"
    | null;
  fg3_subgroup?: "plant_based_fat" | "animal_based_fat" | null;
  fg5_grain_kind?: "whole_grain" | "refined_grain" | null;
  fg7_snack_kind?: "plant_based_snack" | "animal_based_snack" | null;
  composite_step1_bucket?:
    | "meat_based"
    | "seafood_based"
    | "vegetarian"
    | "vegan"
    | null;
  confidence?: number;
}

// Phase 34L — nutrition validation table.
export interface NutritionValidationRow {
  product_id: string;
  product_name: string;
  pt_group: ProteinTrackerGroup | null;
  protein_pct: string | null;
  plant_protein_pct: string | null;
  animal_protein_pct: string | null;
  retailer_protein_pct: string | null;
  source: "retailer_csv" | "nevo" | "ciqual" | "manual" | "missing";
  match_method: "deterministic" | "ai_assisted" | "manual" | "none" | null;
  split_source:
    | "nevo_official_split"
    | "classification_assumption"
    | "manual"
    | "retailer_csv"
    | "missing";
  confidence: number | null;
  reference_name: string | null;
  reference_code: string | null;
  status:
    | "ready"
    | "ready_medium_confidence"
    | "needs_review"
    | "needs_review_low_confidence"
    | "suggested_very_low_confidence"
    | "missing"
    | "excluded";
  reason: string | null;
  // Quality-V2-AB — friendly matched-NEVO label for V2-applied rows
  // (e.g. "NEVO V2: Muesli w fruit/seeds"). Optional; null for other rows.
  source_display_label?: string | null;
}

export interface NutritionValidationsResponse {
  items: NutritionValidationRow[];
  total: number;
  counts_by_status: Record<string, number>;
  counts_by_source: Record<string, number>;
}

// Phase 34N — calculation preflight diagnostic.
export interface CalculationPreflightResponse {
  total_products: number;
  classified_products: number;
  products_with_volume: number;
  products_with_weight: number;
  products_with_total_protein: number;
  products_with_plant_animal_split: number;
  products_ready_for_calculation: number;
  products_missing_nutrition: number;
  products_missing_volume_or_weight: number;
  products_missing_classification: number;
  products_out_of_scope: number;
  sample_exclusion_reasons: string[];
  nevo_total_references: number;
  nevo_attempted: boolean;
  // Phase Product-UX-A — which methodology this preflight reflects.
  methodology?: "protein_tracker" | "wwf";
  requires_nutrition?: boolean;
}

export interface NutritionReferencesStats {
  nevo_total: number;
  nevo_with_protein: number;
  nevo_with_split: number;
  nevo_sample_names: string[];
  ciqual_total: number;
  ciqual_with_protein: number;
  ciqual_sample_names: string[];
}

export interface ReviewItem {
  product_id: string;
  upload_id: string | null;
  external_product_id: string;
  product_name: string;
  brand: string | null;
  methodology: Methodology;
  status: ManualReviewStatus;
  reason: ManualReviewReason;
  queued_at: string;
  current_category: string | null;
  confidence: number | null;
  // Phase 19B — safe classification rationale
  source: "deterministic" | "ai" | "manual_review" | null;
  rule_id: string | null;
  ai_model: string | null;
  ai_prompt_version: string | null;
  rationale_notes: string[];
  // Phase 19D — lock and assignment
  locked_by_user_id: string | null;
  locked_by_email: string | null;
  locked_at: string | null;
  lock_expires_at: string | null;
  lock_status: "unlocked" | "locked_by_me" | "locked_by_other" | "expired";
  assigned_to_user_id: string | null;
  assigned_to_email: string | null;
  // Phase 19E — priority
  priority_level: "low" | "medium" | "high" | "critical";
  priority_reasons: string[];
}

export type ReviewPriority = "low" | "medium" | "high" | "critical";

export interface ReviewFilters {
  methodology?: Methodology;
  status?: ManualReviewStatus;
  reason?: ManualReviewReason;
  priority_level?: ReviewPriority;
  upload_id?: string;
  product_search?: string;
  sort?: "oldest" | "newest" | "priority";
}

export type BulkReviewAction = "bulk_accept" | "bulk_defer" | "bulk_change_pt_group";

export interface BulkActionRequest {
  action: BulkReviewAction;
  methodology: Methodology;
  product_ids: string[];
  to_pt_group?: string;
  reason?: string;
}

export interface BulkActionResponse {
  action: string;
  requested_count: number;
  updated_count: number;
  decision_ids: string[];
}

export interface Run {
  id: string;
  project_id: string;
  methodology: Methodology;
  rows_count: number;
  started_at: string;
  finished_at: string | null;
  summary: Record<string, unknown>;
}

export interface CurrentUser {
  user_id: string;
  email: string;
  organisation_id: string;
  role: string;
  organisation_type: "gms_client" | "altera_internal";
  auth_provider: "supabase" | "dev";
  is_dev_auth: boolean;
}

export type ApprovalStatus = "draft" | "under_review" | "approved" | "rejected" | "delivered";

export type JobType =
  | "validate_upload"
  | "ingest_upload"
  | "classify_upload"
  | "run_calculation"
  | "generate_export"
  | "generate_report";

export type JobStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "retrying";

export interface JobResult {
  // classify_upload result — deterministic counts
  methodology?: string;
  matched?: number;
  pass_through?: number;
  rule_collision?: number;
  queued_for_review?: number;
  total_products?: number;
  // classify_upload result — AI pipeline counts (zero when AI disabled)
  ai_attempted?: number;
  ai_accepted?: number;
  ai_review?: number;
  ai_failed?: number;
  // run_calculation result
  run_id?: string;
  rows_count?: number;
  // generate_export result
  fmt?: string;
  filename?: string;
  size_bytes?: number;
  export_id?: string;
  storage_path?: string;
  // validate_upload result
  errors?: string[];
  is_valid?: boolean;
  // generic
  [key: string]: unknown;
}

export interface Job {
  job_id: string;
  organisation_id: string;
  project_id: string;
  upload_id: string | null;
  run_id: string | null;
  job_type: JobType;
  status: JobStatus;
  progress_pct: number | null;
  created_by: string;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  failed_at: string | null;
  error_message: string | null;
  retry_count: number;
  idempotency_key: string | null;
  result: JobResult | null;
}

export interface ExportRecord {
  id: string;
  run_id: string;
  format: string;
  approval_status: ApprovalStatus;
  filename: string;
  size_bytes: number;
  created_at: string;
  // Phase 20 — approval/delivery metadata
  approved_by: string | null;
  approved_at: string | null;
  rejected_by: string | null;
  rejected_at: string | null;
  rejection_reason: string | null;
  under_review_by: string | null;
  under_review_at: string | null;
  delivered_by: string | null;
  delivered_at: string | null;
  client_download_count: number;
  client_downloaded_at: string | null;
}

// ---------------------------------------------------------------------------
// Phase 21 — Report document types
// ---------------------------------------------------------------------------

export interface ClassificationSources {
  deterministic: number;
  ai: number;
  manual_review: number;
  total: number;
}

export interface ReviewSummary {
  total_reviewed: number;
  accepted: number;
  changed: number;
  deferred: number;
  pending: number;
  top_reasons: string[];
}

export interface PTGroupData {
  pt_group: string;
  item_count: number;
  volume_kg: string;
  protein_kg: string;
}

// Phase Product-UX-C — Top-N product contributors (optional; empty for
// older runs or when line-level data is unavailable).
export interface PTProductContributor {
  product_id: string;
  product_name: string;
  retailer_category: string | null;
  pt_group: string;
  plant_protein_kg: string;
  animal_protein_kg: string;
  total_protein_kg: string;
  rationale: string;
}

export interface WWFProductContributor {
  product_id: string;
  product_name: string;
  retailer_category: string | null;
  wwf_group: string;
  wwf_bucket: string | null;
  weight_kg: string;
  rationale: string;
}

export interface PTReportSection {
  methodology_version: string;
  methodology_source_edition: string;
  taxonomy_version: string;
  rules_version: string;
  reporting_period_label: string;
  plant_protein_kg: string;
  animal_protein_kg: string;
  total_in_scope_protein_kg: string;
  plant_share_pct: string | null;
  animal_share_pct: string | null;
  groups: PTGroupData[];
  composite_note: string;
  out_of_scope_count: number;
  unknown_count: number;
  rows_with_per_product_split: number;
  rows_protein_source_label: number;
  rows_protein_source_reference_db: number;
  classification_sources: ClassificationSources;
  pt_validation_status: string;
  top_positive_contributors?: PTProductContributor[];
  top_watchout_contributors?: PTProductContributor[];
}

export interface WWFFoodGroupData {
  food_group: string;
  weight_kg: string;
  share_pct: string;
  phd_reference_share_pct: string | null;
}

export interface WWFReportSection {
  methodology_version: string;
  methodology_source_edition: string;
  taxonomy_version: string;
  rules_version: string;
  reporting_period_label: string;
  total_in_scope_weight_kg: string;
  per_food_group: WWFFoodGroupData[];
  composites_meat_based_kg: string;
  composites_seafood_based_kg: string;
  composites_vegetarian_kg: string;
  composites_vegan_kg: string;
  composites_total_weight_kg: string;
  whole_diet_plant_weight_kg: string;
  whole_diet_animal_weight_kg: string;
  out_of_scope_count: number;
  unknown_count: number;
  classification_sources: ClassificationSources;
  top_positive_contributors?: WWFProductContributor[];
  top_watchout_contributors?: WWFProductContributor[];
}

export interface ReportMeta {
  run_id: string;
  project_name: string;
  organisation_id: string;
  reporting_period: string;
  methodology: string;
  generated_at: string;
  approval_status: string;
  approved_by: string | null;
  approved_at: string | null;
  delivered_at: string | null;
  export_id: string | null;
}

// Phase 22 — data coverage and uncertainty
export interface CoverageSection {
  // Upload / validation tier
  uploaded_rows: number | null;
  valid_rows: number | null;
  invalid_rows: number | null;
  warning_count: number | null;
  error_count: number | null;
  // Product tier
  products_total: number;
  products_classified: number;
  products_unknown: number;
  products_out_of_scope: number;
  products_sent_to_review: number;
  products_reviewed_by_altera: number;
  products_ai_classified: number;
  products_rule_classified: number;
  products_manual_classified: number;
  products_with_missing_weight: number;
  products_with_missing_protein: number | null;
  products_with_missing_category: number;
  products_with_missing_ingredients: number | null;
  // Percentages
  valid_row_share_pct: string | null;
  classified_product_share_pct: string | null;
  ai_classified_share_pct: string | null;
  manual_review_share_pct: string | null;
  unknown_product_share_pct: string | null;
  missing_weight_share_pct: string | null;
  missing_protein_share_pct: string | null;
  // Uncertainty
  uncertainty_level: "low" | "medium" | "high";
  uncertainty_rationale: string;
  // Caveats
  caveats: string[];
  review_completion_note: string;
}

// Phase 25A / 25B — recommendation engine + lifecycle
export type RecommendationPriority = "low" | "medium" | "high" | "critical";
export type RecommendationStatus = "draft" | "proposed" | "accepted" | "dismissed" | "archived";

export interface RecommendationItem {
  id: string | null;
  run_id: string | null;
  action_type: string;
  category: string;
  title: string;
  description: string;
  rationale: string;
  expected_direction: string;
  priority: RecommendationPriority;
  confidence: string;
  evidence: string[];
  status: RecommendationStatus;
  caveats: string[];
  client_facing: boolean;
}

export interface PersistedRecommendation extends RecommendationItem {
  id: string;
  run_id: string;
  created_at: string;
  updated_at: string;
}

// Phase 26A — scenario modelling
export type ScenarioStatus = "draft" | "active" | "archived";
export type ScenarioOperationType =
  | "shift_protein_between_groups"
  | "increase_plant_core_protein"
  | "reduce_animal_core_protein"
  | "improve_composite_split";

export interface ScenarioResponse {
  id: string;
  organisation_id: string;
  project_id: string;
  base_run_id: string;
  name: string;
  description: string;
  status: ScenarioStatus;
  methodology: string;
  created_by: string;
  created_at: string;
  updated_at: string;
  operation_count: number;
}

export interface ScenarioOperationRequest {
  operation_type: ScenarioOperationType;
  parameters: Record<string, string | number>;
  rationale?: string;
  order?: number;
}

export interface ScenarioOperationResponse {
  id: string;
  scenario_id: string;
  operation_type: ScenarioOperationType;
  parameters: Record<string, string | number>;
  rationale: string;
  order: number;
  created_at: string;
}

export interface PTProjectedGroupResponse {
  pt_group: string;
  base_protein_kg: string;
  projected_protein_kg: string;
  delta_protein_kg: string;
}

export interface PTProjectedSummaryResponse {
  base_plant_protein_kg: string;
  base_animal_protein_kg: string;
  base_total_protein_kg: string;
  base_plant_share_pct: string | null;
  projected_plant_protein_kg: string;
  projected_animal_protein_kg: string;
  projected_total_protein_kg: string;
  projected_plant_share_pct: string | null;
  projected_animal_share_pct: string | null;
  delta_plant_protein_kg: string;
  delta_animal_protein_kg: string;
  delta_plant_share_pct: string | null;
  per_group: PTProjectedGroupResponse[];
}

export interface ScenarioResultResponse {
  scenario_id: string;
  base_run_id: string;
  methodology: string;
  pt_projected: PTProjectedSummaryResponse | null;
  warnings: string[];
  created_at: string;
}

// Phase 27A — run comparisons
export interface PTGroupComparisonResponse {
  pt_group: string;
  baseline_protein_kg: string;
  comparison_protein_kg: string;
  delta_protein_kg: string;
}

export interface PTComparisonSummaryResponse {
  baseline_reporting_period: string;
  comparison_reporting_period: string;
  baseline_methodology_version: string;
  comparison_methodology_version: string;
  baseline_taxonomy_version: string;
  comparison_taxonomy_version: string;
  baseline_rules_version: string;
  comparison_rules_version: string;
  baseline_plant_protein_kg: string;
  baseline_animal_protein_kg: string;
  baseline_total_protein_kg: string;
  baseline_plant_share_pct: string | null;
  baseline_animal_share_pct: string | null;
  comparison_plant_protein_kg: string;
  comparison_animal_protein_kg: string;
  comparison_total_protein_kg: string;
  comparison_plant_share_pct: string | null;
  comparison_animal_share_pct: string | null;
  delta_plant_protein_kg: string;
  delta_animal_protein_kg: string;
  delta_total_protein_kg: string;
  delta_plant_share_pct: string | null;
  delta_animal_share_pct: string | null;
  direction: "improving" | "declining" | "stable";
  per_group: PTGroupComparisonResponse[];
}

export interface WWFFoodGroupComparisonResponse {
  food_group: string;
  baseline_weight_kg: string;
  comparison_weight_kg: string;
  delta_weight_kg: string;
  baseline_share_pct: string;
  comparison_share_pct: string;
  delta_share_pct: string;
  phd_reference_share_pct: string | null;
}

export interface WWFComparisonSummaryResponse {
  baseline_reporting_period: string;
  comparison_reporting_period: string;
  baseline_methodology_version: string;
  comparison_methodology_version: string;
  baseline_taxonomy_version: string;
  comparison_taxonomy_version: string;
  baseline_rules_version: string;
  comparison_rules_version: string;
  baseline_total_weight_kg: string;
  comparison_total_weight_kg: string;
  delta_total_weight_kg: string;
  baseline_plant_weight_kg: string;
  comparison_plant_weight_kg: string;
  delta_plant_weight_kg: string;
  baseline_animal_weight_kg: string;
  comparison_animal_weight_kg: string;
  delta_animal_weight_kg: string;
  direction: "improving" | "declining" | "stable";
  per_food_group: WWFFoodGroupComparisonResponse[];
}

export interface RunComparisonResponse {
  baseline_run_id: string;
  comparison_run_id: string;
  project_id: string;
  methodology: string;
  pt_comparison: PTComparisonSummaryResponse | null;
  wwf_comparison: WWFComparisonSummaryResponse | null;
  warnings: string[];
  created_at: string;
}

export interface ReportDocument {
  meta: ReportMeta;
  executive_summary: string;
  pt_section: PTReportSection | null;
  wwf_section: WWFReportSection | null;
  review_summary: ReviewSummary;
  coverage: CoverageSection;
  recommendations: RecommendationItem[];
}

/** Latest report per methodology (either may be null). PT and WWF documents
 *  stay separate — never merged metrics. */
export interface LatestReports {
  protein_tracker: ReportDocument | null;
  wwf: ReportDocument | null;
}

/** Sentinel thrown by ``downloadCategorizedExport`` when ``fetch`` itself
 *  rejects (network / CORS / timeout — no backend response). The UI maps it
 *  to a localised message; ``api.ts`` has no access to ``t()``. */
export const EXPORT_NETWORK_ERROR = "EXPORT_NETWORK_ERROR";

// ---------------------------------------------------------------------------
// Phase 32A — admin types
// ---------------------------------------------------------------------------

export interface OrgResponse {
  id: string;
  name: string;
  slug: string;
  organisation_type: string;
  created_at: string;
}

export interface InviteUserRequest {
  email: string;
  role?: string;
  redirect_to?: string;
}

export interface InviteUserResponse {
  user_id: string;
  email: string;
  organisation_id: string;
  role: string;
  invite_sent: boolean;
}

// Phase 33B — column mapping types

export interface ColumnMappingEntry {
  raw_header: string;
  normalised_header: string;
  canonical_field: string | null;
  confidence: "exact" | "synonym" | "none";
  enrichment_needed: boolean;
  auto_ignore: boolean;
}

export interface MappingPreviewResult {
  entries: ColumnMappingEntry[];
  missing_required_pt: string[];
  missing_required_wwf: string[];
  duplicate_normalised: string[];
}

// Phase 32B — member management types

export interface MemberResponse {
  user_id: string;
  email: string;
  display_name: string;
  role: string;
  organisation_id: string;
}

export interface ResendInviteResponse {
  user_id: string;
  email: string;
  organisation_id: string;
  invite_sent: boolean;
}

export interface UpdateMemberRequest {
  role: string;
}

async function request<T>(
  path: string,
  init: RequestInit,
  accessToken: string | null,
): Promise<T> {
  const url = `${getApiBaseUrl()}${path}`;
  const headers: Record<string, string> = {
    ...(init.body && !(init.body instanceof FormData)
      ? { "Content-Type": "application/json" }
      : {}),
    ...((init.headers as Record<string, string>) ?? {}),
  };
  if (accessToken) headers["Authorization"] = `Bearer ${accessToken}`;
  const res = await fetch(url, {
    ...init,
    headers,
    cache: "no-store",
    credentials: "omit",
  });
  if (!res.ok) {
    let detail: unknown = res.statusText;
    try {
      const body = await res.json();
      if (body?.detail !== undefined) detail = body.detail;
    } catch {
      // ignore
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/**
 * Bind an API token to all method calls so pages don't have to thread
 * it through manually. Pages typically do:
 *
 *     const { accessToken } = useAuth();
 *     const api = createApi(accessToken);
 *     const projects = await api.listProjects();
 */
export function createApi(accessToken: string | null) {
  return {
    me: () => request<CurrentUser>("/api/v1/me", { method: "GET" }, accessToken),
    listProjects: () =>
      request<Page<Project>>("/api/v1/projects", { method: "GET" }, accessToken),
    getProject: (id: string) =>
      request<Project>(`/api/v1/projects/${id}`, { method: "GET" }, accessToken),
    createProject: (body: {
      name: string;
      methodologies_enabled: Methodology[];
      reporting_period_label: string;
    }) =>
      request<Project>(
        "/api/v1/projects",
        { method: "POST", body: JSON.stringify(body) },
        accessToken,
      ),

    listUploads: (projectId: string) =>
      request<Page<UploadResult>>(
        `/api/v1/projects/${projectId}/uploads`,
        { method: "GET" },
        accessToken,
      ),
    previewMapping: (headers: string[], methodologies?: string[]): Promise<MappingPreviewResult> =>
      request<MappingPreviewResult>(
        "/api/v1/uploads/preview-mapping",
        {
          method: "POST",
          body: JSON.stringify({ headers, methodologies: methodologies ?? null }),
        },
        accessToken,
      ),

    uploadCsv: async (
      projectId: string,
      file: File,
      columnMapping?: Record<string, string>,
    ): Promise<UploadResult> => {
      const fd = new FormData();
      fd.append("file", file);
      if (columnMapping) fd.append("column_mapping", JSON.stringify(columnMapping));
      return request<UploadResult>(
        `/api/v1/projects/${projectId}/uploads`,
        { method: "POST", body: fd },
        accessToken,
      );
    },

    // Phase 34Y — chunked CSV ingestion job (Phase 34X backend).
    // The wizard uses this in place of `uploadCsv` for all files:
    // create returns quickly with a job id, then the wizard polls
    // advance until the job is terminal. Each request stays well
    // under Render's HTTP timeout regardless of file size.
    //
    // Frontend mints a UUID for the upload_id so the path parameter
    // is known before the multipart request returns; the backend
    // creates the Upload record up-front with that id and the
    // ingestion job processes products into it chunk-by-chunk.
    createIngestionJob: async (
      projectId: string,
      uploadId: string,
      file: File,
      options?: { columnMapping?: Record<string, string>; chunkSize?: number },
    ): Promise<IngestionJob> => {
      const fd = new FormData();
      fd.append("file", file);
      if (options?.columnMapping) {
        fd.append("column_mapping", JSON.stringify(options.columnMapping));
      }
      if (options?.chunkSize != null) {
        fd.append("chunk_size", String(options.chunkSize));
      }
      return request<IngestionJob>(
        `/api/v1/projects/${projectId}/uploads/${uploadId}/ingestion-jobs`,
        { method: "POST", body: fd },
        accessToken,
      );
    },

    getIngestionJob: (projectId: string, jobId: string) =>
      request<IngestionJob>(
        `/api/v1/projects/${projectId}/ingestion-jobs/${jobId}`,
        { method: "GET" },
        accessToken,
      ),

    advanceIngestionJob: (projectId: string, jobId: string) =>
      request<IngestionJob>(
        `/api/v1/projects/${projectId}/ingestion-jobs/${jobId}/advance`,
        { method: "POST" },
        accessToken,
      ),

    prepareUpload: (projectId: string, filename: string): Promise<PrepareUploadResult> =>
      request<PrepareUploadResult>(
        `/api/v1/projects/${projectId}/uploads/prepare`,
        { method: "POST", body: JSON.stringify({ filename }) },
        accessToken,
      ),

    ingestUpload: (
      projectId: string,
      uploadId: string,
      storagePath: string,
      originalFilename: string,
      columnMapping?: Record<string, string>,
    ): Promise<UploadResult> =>
      request<UploadResult>(
        `/api/v1/projects/${projectId}/uploads/${uploadId}/ingest`,
        {
          method: "POST",
          body: JSON.stringify({
            storage_path: storagePath,
            original_filename: originalFilename,
            ...(columnMapping ? { column_mapping: columnMapping } : {}),
          }),
        },
        accessToken,
      ),

    classify: (
      projectId: string,
      uploadId: string,
      methodology: Methodology,
      options?: {
        deterministic_only?: boolean;
        // Phase 34I — when true, skip the deterministic rule engine
        // entirely and use AI as the primary classifier. This is the
        // new normal-user default; the wizard's Step 3 sets it.
        skip_deterministic?: boolean;
      },
    ) =>
      request<ClassifySummary>(
        `/api/v1/projects/${projectId}/uploads/${uploadId}/classify`,
        {
          method: "POST",
          body: JSON.stringify({
            methodology,
            deterministic_only: options?.deterministic_only ?? false,
            skip_deterministic: options?.skip_deterministic ?? false,
          }),
        },
        accessToken,
      ),

    deleteUpload: (projectId: string, uploadId: string) =>
      request<void>(
        `/api/v1/projects/${projectId}/uploads/${uploadId}`,
        { method: "DELETE" },
        accessToken,
      ),

    // Phase 34R — async, chunked AI classification jobs.
    createClassificationJob: (
      projectId: string,
      uploadId: string,
      body: {
        methodology: Methodology;
        overwrite?: boolean;
        only_missing_or_failed?: boolean;
        batch_size?: number;
      },
    ) =>
      request<ClassificationJob>(
        `/api/v1/projects/${projectId}/uploads/${uploadId}/classification-jobs`,
        {
          method: "POST",
          // Phase 35-perf — omit batch_size when the caller didn't
          // specify one, letting the backend pick from
          // ALTERA_AI_CLASSIFICATION_BATCH_SIZE. The env override lets
          // ops bench 25 / 40 / 50 without a frontend redeploy.
          body: JSON.stringify({
            methodology: body.methodology,
            overwrite: body.overwrite ?? false,
            only_missing_or_failed: body.only_missing_or_failed ?? true,
            ...(body.batch_size !== undefined
              ? { batch_size: body.batch_size }
              : {}),
          }),
        },
        accessToken,
      ),

    getClassificationJob: (projectId: string, jobId: string) =>
      request<ClassificationJob>(
        `/api/v1/projects/${projectId}/classification-jobs/${jobId}`,
        { method: "GET" },
        accessToken,
      ),

    // Phase 35A — resume support. Looks up the most-recent
    // non-terminal classification job for the given (upload,
    // methodology) pair. Returns ``null`` when the backend says
    // 404 ``no_active_job`` — that's the "nothing to resume" signal
    // for the wizard's Step 4 mount.
    getActiveClassificationJob: async (
      projectId: string,
      uploadId: string,
      methodology: Methodology,
    ): Promise<ClassificationJob | null> => {
      try {
        return await request<ClassificationJob>(
          `/api/v1/projects/${projectId}/classification-jobs/active` +
            `?upload_id=${uploadId}&methodology=${methodology}`,
          { method: "GET" },
          accessToken,
        );
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) return null;
        throw e;
      }
    },

    advanceClassificationJob: (projectId: string, jobId: string) =>
      request<ClassificationJob>(
        `/api/v1/projects/${projectId}/classification-jobs/${jobId}/advance`,
        { method: "POST" },
        accessToken,
      ),

    cancelClassificationJob: (projectId: string, jobId: string) =>
      request<ClassificationJob>(
        `/api/v1/projects/${projectId}/classification-jobs/${jobId}/cancel`,
        { method: "POST" },
        accessToken,
      ),

    retryFailedClassificationJob: (projectId: string, jobId: string) =>
      request<ClassificationJob>(
        `/api/v1/projects/${projectId}/classification-jobs/${jobId}/retry-failed`,
        { method: "POST" },
        accessToken,
      ),

    applyNutritionReferences: (
      projectId: string,
      options?: { providers?: string[] },
    ) =>
      request<ApplyReferencesSummary>(
        `/api/v1/projects/${projectId}/enrichments/apply-references`,
        {
          method: "POST",
          body: options?.providers
            ? JSON.stringify({ providers: options.providers })
            : undefined,
        },
        accessToken,
      ),

    // Phase 34L — nutrition validation table.
    listNutritionValidations: (
      projectId: string,
      filters: {
        status?: "ready" | "needs_review" | "missing" | "excluded";
        source?: "retailer_csv" | "nevo" | "ciqual" | "manual" | "missing";
        product_search?: string;
        limit?: number;
        offset?: number;
      } = {},
    ) => {
      const params = new URLSearchParams();
      if (filters.status) params.set("status", filters.status);
      if (filters.source) params.set("source", filters.source);
      if (filters.product_search) params.set("product_search", filters.product_search);
      if (filters.limit != null) params.set("limit", String(filters.limit));
      if (filters.offset != null) params.set("offset", String(filters.offset));
      const q = params.size > 0 ? `?${params.toString()}` : "";
      return request<NutritionValidationsResponse>(
        `/api/v1/projects/${projectId}/nutrition-validations${q}`,
        { method: "GET" },
        accessToken,
      );
    },
    submitManualNutrition: (
      projectId: string,
      productId: string,
      body: {
        protein_pct: number;
        plant_protein_pct: number;
        animal_protein_pct: number;
        rationale?: string;
      },
    ) =>
      request<NutritionValidationRow>(
        `/api/v1/projects/${projectId}/nutrition-validations/${productId}/manual`,
        { method: "POST", body: JSON.stringify(body) },
        accessToken,
      ),

    // Phase 34F — paginated category validation table.
    listClassifications: (
      projectId: string,
      filters: ClassificationsFilters = {},
    ) => {
      const params = new URLSearchParams();
      if (filters.source) params.set("source", filters.source);
      if (filters.pt_group) params.set("pt_group", filters.pt_group);
      if (filters.min_confidence != null)
        params.set("min_confidence", String(filters.min_confidence));
      if (filters.max_confidence != null)
        params.set("max_confidence", String(filters.max_confidence));
      if (filters.review_status) params.set("review_status", filters.review_status);
      if (filters.product_search)
        params.set("product_search", filters.product_search);
      if (filters.limit != null) params.set("limit", String(filters.limit));
      if (filters.offset != null) params.set("offset", String(filters.offset));
      // Phase WWF-N — unified validation table query params.
      if (filters.view) params.set("view", filters.view);
      if (filters.methodology) params.set("methodology", filters.methodology);
      const q = params.size > 0 ? `?${params.toString()}` : "";
      return request<ClassificationsResponse>(
        `/api/v1/projects/${projectId}/classifications${q}`,
        { method: "GET" },
        accessToken,
      );
    },

    // Phase 34D — NEVO/CIQUAL reference table diagnostics (Altera-only).
    // Phase 34N — calculation preflight diagnostic. The wizard reads
    // this to power Step 7's "Conditions requises" panel with the
    // same source of truth the run engine uses, so eligible_rows and
    // rows_count never disagree.
    getCalculationPreflight: (
      projectId: string,
      methodology?: "protein_tracker" | "wwf",
    ) =>
      request<CalculationPreflightResponse>(
        `/api/v1/projects/${projectId}/calculation-preflight` +
          (methodology ? `?methodology=${methodology}` : ""),
        { method: "GET" },
        accessToken,
      ),

    getNutritionReferencesStats: () =>
      request<NutritionReferencesStats>(
        `/api/v1/admin/nutrition-references/stats`,
        { method: "GET" },
        accessToken,
      ),

    // Phase 34A — guided-workflow status (stepper + next CTA + blockers).
    getWorkflowStatus: (projectId: string) =>
      request<WorkflowStatus>(
        `/api/v1/projects/${projectId}/workflow-status`,
        { method: "GET" },
        accessToken,
      ),

    listReview: (projectId: string, filters: ReviewFilters = {}) => {
      const params = new URLSearchParams();
      if (filters.methodology) params.set("methodology", filters.methodology);
      if (filters.status) params.set("status", filters.status);
      if (filters.reason) params.set("reason", filters.reason);
      if (filters.priority_level) params.set("priority_level", filters.priority_level);
      if (filters.upload_id) params.set("upload_id", filters.upload_id);
      if (filters.product_search) params.set("product_search", filters.product_search);
      if (filters.sort) params.set("sort", filters.sort);
      const q = params.size > 0 ? `?${params.toString()}` : "";
      return request<Page<ReviewItem>>(
        `/api/v1/projects/${projectId}/review${q}`,
        { method: "GET" },
        accessToken,
      );
    },
    submitDecision: (
      projectId: string,
      productId: string,
      methodology: Methodology,
      body: {
        decision: DecisionType;
        to_category?: string;
        reason?: string;
        // Phase WWF-O — explicit WWF correction payload. When supplied
        // (methodology must be wwf), backend pins every field instead
        // of falling back to ``_build_wwf_target`` safe defaults.
        wwf?: WWFCorrectionPayload;
      },
    ) =>
      request<ReviewItem>(
        `/api/v1/projects/${projectId}/review/${productId}/${methodology}/decision`,
        { method: "POST", body: JSON.stringify(body) },
        accessToken,
      ),

    bulkAction: (projectId: string, body: BulkActionRequest) =>
      request<BulkActionResponse>(
        `/api/v1/projects/${projectId}/review/bulk-action`,
        { method: "POST", body: JSON.stringify(body) },
        accessToken,
      ),

    claimItem: (projectId: string, productId: string, methodology: Methodology) =>
      request<ReviewItem>(
        `/api/v1/projects/${projectId}/review/${productId}/${methodology}/claim`,
        { method: "POST" },
        accessToken,
      ),

    releaseItem: (projectId: string, productId: string, methodology: Methodology) =>
      request<ReviewItem>(
        `/api/v1/projects/${projectId}/review/${productId}/${methodology}/release`,
        { method: "POST" },
        accessToken,
      ),

    refreshLock: (projectId: string, productId: string, methodology: Methodology) =>
      request<ReviewItem>(
        `/api/v1/projects/${projectId}/review/${productId}/${methodology}/refresh-lock`,
        { method: "POST" },
        accessToken,
      ),

    assignItem: (projectId: string, productId: string, methodology: Methodology, assignToUserId: string) =>
      request<ReviewItem>(
        `/api/v1/projects/${projectId}/review/${productId}/${methodology}/assign`,
        { method: "POST", body: JSON.stringify({ assign_to_user_id: assignToUserId }) },
        accessToken,
      ),

    createRun: (
      projectId: string,
      methodology: Methodology,
      options?: { allow_partial?: boolean; use_enriched_nutrition?: boolean },
    ) =>
      request<Run>(
        `/api/v1/projects/${projectId}/runs`,
        {
          method: "POST",
          body: JSON.stringify({
            methodology,
            // Phase 34K — when true, the run is allowed even if some
            // products have no usable nutrition data. The calculation
            // engine drops those products; the run summary carries
            // coverage metrics so the report can disclose the gap.
            allow_partial: options?.allow_partial ?? false,
            // Phase 34U — explicitly send use_enriched_nutrition=true
            // for partial runs. The backend already defaults to true
            // but being explicit means a future server-side default
            // change can't silently break partial calculation.
            use_enriched_nutrition: options?.use_enriched_nutrition ?? true,
          }),
        },
        accessToken,
      ),
    listRuns: (projectId: string) =>
      request<Page<Run>>(
        `/api/v1/projects/${projectId}/runs`,
        { method: "GET" },
        accessToken,
      ),
    getRun: (projectId: string, runId: string) =>
      request<Run>(
        `/api/v1/projects/${projectId}/runs/${runId}`,
        { method: "GET" },
        accessToken,
      ),

    /**
     * Download an export, sending the auth token so the backend approval
     * gate is enforced. Follows any redirect (signed URL), then triggers a
     * browser download via a blob URL.
     */
    downloadExport: async (
      projectId: string,
      runId: string,
      fmt: "csv" | "json" | "md",
    ): Promise<void> => {
      const url = `${getApiBaseUrl()}/api/v1/projects/${projectId}/runs/${runId}/export?fmt=${fmt}`;
      const headers: Record<string, string> = {};
      if (accessToken) headers["Authorization"] = `Bearer ${accessToken}`;
      const res = await fetch(url, { headers, cache: "no-store", credentials: "omit" });
      if (!res.ok) {
        const body = await res.json().catch(() => null) as { detail?: string } | null;
        throw new Error(body?.detail ?? `${res.status} ${res.statusText}`);
      }
      const disposition = res.headers.get("Content-Disposition");
      const filenameMatch = disposition?.match(/filename="([^"]+)"/);
      const filename = filenameMatch?.[1] ?? `export.${fmt}`;
      const blob = await res.blob();
      const objectUrl = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = objectUrl;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(objectUrl);
    },

    /**
     * Download the categorised catalogue as an .xlsx: one data sheet (all
     * products + their PT & WWF categories) plus one analysis sheet per
     * methodology with charts. Sends the auth token and triggers a browser
     * download via a blob URL.
     */
    downloadCategorizedExport: async (
      projectId: string,
      lang: "fr" | "en" = "fr",
    ): Promise<void> => {
      const url = `${getApiBaseUrl()}/api/v1/projects/${projectId}/export/categorized.xlsx?lang=${lang}`;
      const headers: Record<string, string> = {};
      if (accessToken) headers["Authorization"] = `Bearer ${accessToken}`;
      let res: Response;
      try {
        res = await fetch(url, { headers, cache: "no-store", credentials: "omit" });
      } catch {
        // fetch() itself rejected — a network error / CORS failure / timeout,
        // i.e. NO backend response to read a detail from. Throw a stable
        // sentinel the UI maps to a localised message (api.ts has no t()),
        // instead of leaking a raw English Error.message as the bare
        // "Failed to fetch".
        throw new Error(EXPORT_NETWORK_ERROR);
      }
      if (!res.ok) {
        // The backend responded with an error — surface its detail so the UI
        // shows the real reason, not a generic failure.
        const body = (await res.json().catch(() => null)) as { detail?: string } | null;
        throw new Error(body?.detail ?? `${res.status} ${res.statusText}`);
      }
      const disposition = res.headers.get("Content-Disposition");
      const filenameMatch = disposition?.match(/filename="([^"]+)"/);
      const filename = filenameMatch?.[1] ?? "altera-export-categorise.xlsx";
      const blob = await res.blob();
      const objectUrl = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = objectUrl;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(objectUrl);
    },

    listExports: (projectId: string, runId: string) =>
      request<Page<ExportRecord>>(
        `/api/v1/projects/${projectId}/runs/${runId}/exports`,
        { method: "GET" },
        accessToken,
      ),

    approveExport: (projectId: string, runId: string, exportId: string) =>
      request<ExportRecord>(
        `/api/v1/projects/${projectId}/runs/${runId}/exports/${exportId}/approve`,
        { method: "POST", body: JSON.stringify({}) },
        accessToken,
      ),

    rejectExport: (projectId: string, runId: string, exportId: string, reason?: string) =>
      request<ExportRecord>(
        `/api/v1/projects/${projectId}/runs/${runId}/exports/${exportId}/reject`,
        { method: "POST", body: JSON.stringify({ rejection_reason: reason ?? null }) },
        accessToken,
      ),

    submitExportForReview: (projectId: string, runId: string, exportId: string) =>
      request<ExportRecord>(
        `/api/v1/projects/${projectId}/runs/${runId}/exports/${exportId}/submit-for-review`,
        { method: "POST", body: JSON.stringify({}) },
        accessToken,
      ),

    deliverExport: (projectId: string, runId: string, exportId: string) =>
      request<ExportRecord>(
        `/api/v1/projects/${projectId}/runs/${runId}/exports/${exportId}/deliver`,
        { method: "POST", body: JSON.stringify({}) },
        accessToken,
      ),

    getReport: (projectId: string, runId: string) =>
      request<ReportDocument>(
        `/api/v1/projects/${projectId}/runs/${runId}/report`,
        { method: "GET" },
        accessToken,
      ),

    /** Latest Protein Tracker AND WWF report documents for the project, so
     *  the Result step can show both when both methodology runs exist. */
    getLatestReports: (projectId: string) =>
      request<LatestReports>(
        `/api/v1/projects/${projectId}/reports/latest`,
        { method: "GET" },
        accessToken,
      ),

    // -----------------------------------------------------------------------
    // Jobs (Phase 16)
    // -----------------------------------------------------------------------

    getJob: (jobId: string) =>
      request<Job>(`/api/v1/jobs/${jobId}`, { method: "GET" }, accessToken),

    listJobs: (projectId: string, jobType?: JobType) => {
      const q = jobType ? `?job_type=${jobType}` : "";
      return request<Page<Job>>(
        `/api/v1/projects/${projectId}/jobs${q}`,
        { method: "GET" },
        accessToken,
      );
    },

    uploadWwfStep2: async (
      projectId: string,
      file: File,
    ): Promise<WWFStep2UploadResult> => {
      const fd = new FormData();
      fd.append("file", file);
      return request<WWFStep2UploadResult>(
        `/api/v1/projects/${projectId}/wwf-ingredients/upload`,
        { method: "POST", body: fd },
        accessToken,
      );
    },

    enqueueClassify: (projectId: string, uploadId: string, methodology: Methodology) =>
      request<Job>(
        `/api/v1/projects/${projectId}/uploads/${uploadId}/jobs/classify`,
        { method: "POST", body: JSON.stringify({ methodology }) },
        accessToken,
      ),

    enqueueCalculate: (projectId: string, methodology: Methodology) =>
      request<Job>(
        `/api/v1/projects/${projectId}/jobs/calculate`,
        { method: "POST", body: JSON.stringify({ methodology }) },
        accessToken,
      ),

    enqueueExport: (projectId: string, runId: string, fmt: "csv" | "json" | "md") =>
      request<Job>(
        `/api/v1/projects/${projectId}/runs/${runId}/jobs/export`,
        { method: "POST", body: JSON.stringify({ fmt }) },
        accessToken,
      ),

    // -----------------------------------------------------------------------
    // Recommendations (Phase 25B)
    // -----------------------------------------------------------------------

    listRecommendations: (projectId: string, runId: string) =>
      request<Page<PersistedRecommendation>>(
        `/api/v1/projects/${projectId}/runs/${runId}/recommendations`,
        { method: "GET" },
        accessToken,
      ),

    generateRecommendations: (projectId: string, runId: string) =>
      request<PersistedRecommendation[]>(
        `/api/v1/projects/${projectId}/runs/${runId}/recommendations/generate`,
        { method: "POST", body: JSON.stringify({}) },
        accessToken,
      ),

    proposeRecommendation: (recommendationId: string) =>
      request<PersistedRecommendation>(
        `/api/v1/recommendations/${recommendationId}/propose`,
        { method: "POST", body: JSON.stringify({}) },
        accessToken,
      ),

    dismissRecommendation: (recommendationId: string) =>
      request<PersistedRecommendation>(
        `/api/v1/recommendations/${recommendationId}/dismiss`,
        { method: "POST", body: JSON.stringify({}) },
        accessToken,
      ),

    archiveRecommendation: (recommendationId: string) =>
      request<PersistedRecommendation>(
        `/api/v1/recommendations/${recommendationId}/archive`,
        { method: "POST", body: JSON.stringify({}) },
        accessToken,
      ),

    acceptRecommendation: (recommendationId: string) =>
      request<PersistedRecommendation>(
        `/api/v1/recommendations/${recommendationId}/accept`,
        { method: "POST", body: JSON.stringify({}) },
        accessToken,
      ),

    // -----------------------------------------------------------------------
    // Scenarios (Phase 26A)
    // -----------------------------------------------------------------------

    listScenarios: (projectId: string) =>
      request<Page<ScenarioResponse>>(
        `/api/v1/projects/${projectId}/scenarios`,
        { method: "GET" },
        accessToken,
      ),

    createScenario: (projectId: string, body: { name: string; description?: string; base_run_id: string }) =>
      request<ScenarioResponse>(
        `/api/v1/projects/${projectId}/scenarios`,
        { method: "POST", body: JSON.stringify(body) },
        accessToken,
      ),

    listScenarioOperations: (scenarioId: string) =>
      request<ScenarioOperationResponse[]>(
        `/api/v1/scenarios/${scenarioId}/operations`,
        { method: "GET" },
        accessToken,
      ),

    addScenarioOperation: (scenarioId: string, body: ScenarioOperationRequest) =>
      request<ScenarioOperationResponse>(
        `/api/v1/scenarios/${scenarioId}/operations`,
        { method: "POST", body: JSON.stringify(body) },
        accessToken,
      ),

    runScenario: (scenarioId: string) =>
      request<ScenarioResultResponse>(
        `/api/v1/scenarios/${scenarioId}/run`,
        { method: "POST", body: JSON.stringify({}) },
        accessToken,
      ),

    getScenarioResult: (scenarioId: string) =>
      request<ScenarioResultResponse>(
        `/api/v1/scenarios/${scenarioId}/result`,
        { method: "GET" },
        accessToken,
      ),

    // -----------------------------------------------------------------------
    // Run comparisons (Phase 27A)
    // -----------------------------------------------------------------------

    getRunComparison: (
      projectId: string,
      baselineRunId: string,
      comparisonRunId: string,
    ) =>
      request<RunComparisonResponse>(
        `/api/v1/projects/${projectId}/comparisons?baseline_run_id=${baselineRunId}&comparison_run_id=${comparisonRunId}`,
        { method: "GET" },
        accessToken,
      ),

    // -----------------------------------------------------------------------
    // Admin (Phase 32A)
    // -----------------------------------------------------------------------

    listOrgs: () =>
      request<OrgResponse[]>("/api/v1/admin/organisations", { method: "GET" }, accessToken),

    createOrg: (body: { name: string; slug: string }) =>
      request<OrgResponse>(
        "/api/v1/admin/organisations",
        { method: "POST", body: JSON.stringify(body) },
        accessToken,
      ),

    inviteUser: (orgId: string, body: InviteUserRequest) =>
      request<InviteUserResponse>(
        `/api/v1/admin/organisations/${orgId}/invite`,
        { method: "POST", body: JSON.stringify(body) },
        accessToken,
      ),

    listMembers: (orgId: string) =>
      request<MemberResponse[]>(
        `/api/v1/admin/organisations/${orgId}/members`,
        { method: "GET" },
        accessToken,
      ),

    resendInvite: (orgId: string, userId: string) =>
      request<ResendInviteResponse>(
        `/api/v1/admin/organisations/${orgId}/members/${userId}/resend-invite`,
        { method: "POST" },
        accessToken,
      ),

    updateMemberRole: (orgId: string, userId: string, body: UpdateMemberRequest) =>
      request<MemberResponse>(
        `/api/v1/admin/organisations/${orgId}/members/${userId}`,
        { method: "PATCH", body: JSON.stringify(body) },
        accessToken,
      ),

    removeMember: (orgId: string, userId: string) =>
      request<void>(
        `/api/v1/admin/organisations/${orgId}/members/${userId}`,
        { method: "DELETE" },
        accessToken,
      ),

    /** Poll a job until it reaches a terminal state (succeeded/failed/cancelled). */
    pollJob: async (
      jobId: string,
      { intervalMs = 1500, timeoutMs = 60000 }: { intervalMs?: number; timeoutMs?: number } = {},
    ): Promise<Job> => {
      const deadline = Date.now() + timeoutMs;
      while (Date.now() < deadline) {
        const job = await request<Job>(`/api/v1/jobs/${jobId}`, { method: "GET" }, accessToken);
        if (job.status === "succeeded" || job.status === "failed" || job.status === "cancelled") {
          return job;
        }
        await new Promise((res) => setTimeout(res, intervalMs));
      }
      throw new Error(`job ${jobId} did not complete within ${timeoutMs}ms`);
    },
  };
}

export const PT_GROUP_OPTIONS: ProteinTrackerGroup[] = [
  "plant_based_core",
  "plant_based_non_core",
  "composite_products",
  "animal_core",
];

export const WWF_FOOD_GROUP_OPTIONS: WWFFoodGroup[] = [
  "FG1",
  "FG2",
  "FG3",
  "FG4",
  "FG5",
  "FG6",
  "FG7",
];

// ---------------------------------------------------------------------------
// WWF Step 2 ingredients (Phase 24A)
// ---------------------------------------------------------------------------

export interface WWFIngredientRowError {
  ingredient_index: number;
  field: string;
  message: string;
}

export interface WWFIngredientProductResult {
  external_product_id: string;
  product_id: string | null;
  is_own_brand: boolean | null;
  is_composite: boolean | null;
  ingredient_count: number;
  valid_ingredient_count: number;
  total_attributed_weight_kg: string;
  product_weight_kg: string | null;
  residual_weight_kg: string | null;
  errors: WWFIngredientRowError[];
  warnings: string[];
}

export interface WWFStep2UploadResult {
  total_products_in_file: number;
  valid_product_count: number;
  error_count: number;
  warning_count: number;
  unknown_product_count: number;
  branded_composite_count: number;
  stored: boolean;
  replaced: boolean;
  product_results: WWFIngredientProductResult[];
}
