# Roadmap

This roadmap captures the path from the current codebase to a
production pilot with one or two design-partner retailers. It runs
through **Phase 35 (pilot readiness)**.

The roadmap reflects the managed-service direction set in
[../project/vision.md](../project/vision.md): Altera operates the
platform on behalf of grocery clients, owns the methodology review,
and approves reports before client download.

## Current status (verified 2026-05-19, through Phase 33B)

| Phase | Scope                                            | Status                                                                                                                            |
|-------|--------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------|
| 13A   | Supabase schema + RLS + auth trigger             | **Done.** SQL migrations and RLS policies in `supabase/`.                                                                         |
| 13B   | Postgres persistence (replace `InMemoryStore`)   | **Done.** `StoreProtocol` + `PostgresRepository` + `MemoryRepository` under `persistence/`. Feature-flagged via `ALTERA_USE_IN_MEMORY_STORE`. Integration test scaffold in `tests/integration/`. |
| 13C   | Supabase Auth on backend + frontend              | **Done.** Backend JWT verification, `/me`, cross-tenant 404, dev fallback, frontend Bearer attachment, AuthGate, login page. |
| 13D   | Supabase Storage for uploads + report files      | **Done.** `StorageService`, `prepare`/`ingest` endpoints, two-step browser upload flow, export persistence + signed-URL redirect, migration `0016_storage_uploads.sql`. |
| 14    | Organisation type + role namespace split         | **Done.** `ClientRole` / `AlteraRole` namespaces, `organisation_type` on `AuthContext`, Altera cross-org visibility, per-request JWT RLS client, export approval workflow (`draft`/`approved`/`rejected`), approve/reject endpoints gated to `altera_methodology_lead`. Migration `0017_phase14_role_namespaces.sql`. 15 new tests. |
| 15    | Production upload pipeline                       | **Done.** 11-value `UploadStatus` lifecycle enum, pre-flight validation (type/size/empty/content-type), SHA-256 duplicate detection, validation report persistence, 4 lifecycle timestamps, `file_size_bytes`/`checksum_sha256`/`duplicate_of` on upload response, storage path `organisations/{org}/projects/{proj}/uploads/{upload}/raw/{file}`, `update_upload` idempotency, `GET /uploads/{id}` detail endpoint. Migration `0018_phase15_upload_lifecycle.sql`. 9 test classes. Frontend: `UploadStatus` type, file info display, duplicate warning, expanded file-type accept. |
| 16    | Background jobs + async processing               | **Done.** `Job` domain model (6 types, 6 statuses, idempotency key, payload, result). `SyncDevRunner` (in-process, swappable via `WorkerBackend` protocol). 5 job endpoints: validate/ingest/classify per upload, calculate per project, export per run. `GET /jobs/{id}` + `GET /projects/{id}/jobs` listing. Job audit trail (6 event types). Migration `0019_phase16_jobs.sql` with RLS. Frontend: `Job` type, `enqueueClassify/Calculate/Export`, `pollJob`, job status pill on classify. 25 new tests. |
| 16B   | Storage-first job resolution                     | **Done.** Job handlers resolve uploaded files from Supabase Storage via `StorageProtocol`; `file_bytes_b64` is an optional dev/test fallback. `generate_export` persists to the `exports` bucket and creates an `ExportRecord`. `FakeStorageService` test double + `StorageProtocol` duck-typed interface. 9 new tests. |
| 17    | AI classifier integration                        | **Done.** `ClassifierProvider` ABC + `OpenAIProvider` (lazy `openai` import). `get_ai_provider()` factory reads `ALTERA_AI_CLASSIFIER_ENABLED` / `ALTERA_AI_PROVIDER` / `OPENAI_API_KEY` / `ALTERA_OPENAI_MODEL`. Pipeline: deterministic rules → AI for pass-through → manual review for low-confidence / parse-failed / provider-error. `ClassifySummary` extended with `ai_attempted/accepted/review/failed`. `ManualReviewQueueReason.AI_PROVIDER_ERROR`. Privacy guard (`assert_payload_allowed`) before every outbound call; `ClassifierPromptInput` strict allow-list. Migration `0020_phase17_ai_provider_error.sql`. Frontend: AI counts in classify result, blue AI summary banner. 26 new tests (690 total). |
| 18    | Advanced deterministic classification + taxonomy | **Done.** Rules VERSION 0.1.0 → 0.2.0. 30+ new PT and WWF rules across 10 YAML files: processed meats, game, whey protein, mycoprotein, plant-based supplements, plant cream/butter/cheese, protein salads/soups/burgers/sushi, FG3 animal fats, FG4 fruit (with plurals), new FG6 starchy veg file, FG7 plant/animal snacks. EN/FR bilingual keyword coverage throughout. Contradiction detection engine (`_detect_contradictions`): vegan+animal-ingredient, vegan+animal-retailer-category, vegetarian+meat-ingredient, plant-based+whey, OOS signals (pet food, nappies, tobacco). `PTContradiction`/`WWFContradiction` verdict types bypass AI and route to `ManualReviewQueueReason.CONTRADICTION_DETECTED`. `ClassifySummary.contradictions` counter. Migration `0021_phase18_contradiction.sql`. 100 new tests (786 total). |
| 19A   | Review queue filtering and sorting               | **Done.** Filter params on `GET /projects/{id}/review`: `methodology`, `status`, `reason`, `upload_id`, `product_search` (case-insensitive substring on name or external ID). Sort: `oldest` (default) / `newest` by `queued_at`. `ReviewItemResponse` extended with `upload_id` and `confidence`. Cross-org and client-role access controls. Frontend: Altera-only filter bar (methodology/status/reason/search/sort dropdowns); client view remains read-only. 18 new tests (804 total). |
| 19B   | Safe classification rationale in review queue    | **Done.** `ManualReviewItem.rationale_notes` persisted at queue-time: contradiction notes for `contradiction_detected`, conflicting rule IDs for `rule_collision`, empty for all other reasons. `ReviewItemView` + `ReviewItemResponse` extended with `source`, `rule_id`, `ai_model`, `ai_prompt_version`, `rationale_notes`. No commercial fields exposed. Frontend: Altera reviewers see source metadata row + amber-highlighted rationale notes per review item. 12 new tests (816 total). |
| 19C   | Bulk review actions                              | **Done.** `POST /projects/{id}/review/bulk-action`: `bulk_accept`, `bulk_defer`, `bulk_change_pt_group` (PT only). All-or-nothing validation: batch ≤ 100, all IDs must exist + be non-terminal + match methodology. Each item emits a `ManualReviewDecision` (persisted) + `review.decision_made` audit event; one `review.bulk_action` event per call. `AuditEventType.REVIEW_DECISION_MADE` + `REVIEW_BULK_ACTION` added; `add_review_decision` on `StoreProtocol` + `InMemoryStore`. Frontend: per-row checkbox, select-all with indeterminate state, bulk-accept / bulk-defer / bulk-change-PT-group toolbar (Altera only). WWF bulk change not supported (requires full classification object; use single-item endpoint). 16 new tests (832 total). |
| 19D   | Reviewer assignment and soft-lock visibility     | **Done.** `ManualReviewItem.assigned_to_user_id`. `release_item()` + `refresh_lock()` pure helpers in `review/workflow.py`. `ReviewItemView` + `ReviewItemResponse` extended with `lock_status` (viewer-relative enum: `unlocked/locked_by_me/locked_by_other/expired`), `locked_by_user_id/email`, `locked_at`, `lock_expires_at`, `assigned_to_user_id/email`. Four new endpoints per item: `POST .../claim` (409 if locked by other), `.../release`, `.../refresh-lock`, `.../assign` (body: `assign_to_user_id`; admins/leads can assign to others, reviewers self-only). Bulk pre-check rejects batch if any item is locked by another reviewer. `list_review` threads `viewer_user_id` for relative `lock_status`. Frontend: lock badge (locked_by_me/locked_by_other/expired), assignment display, Claim/Release buttons, decision controls disabled when `locked_by_other`. 13 new tests (845 total). |
| 19E   | Review prioritisation foundation                 | **Done.** `ManualReviewPriority` enum (`low/medium/high/critical`) computed at view-time from `ManualReviewQueueReason` — no DB schema change. Pure `review/priority.py` module: `assign_priority()` maps contradiction/ai_parse_failed/ai_provider_error → critical, rule_collision → high, low_confidence → medium, requested → low; `priority_weight()` for sort comparisons. `filter_by_priority()` + `sort_by_priority()` helpers in `review/queue.py`. `ReviewItemView` + `ReviewItemResponse` extended with `priority_level` + `priority_reasons` (no commercial fields). `list_review` gains `priority_level` filter param and `sort=priority` option (replaces `oldest_first` bool). Frontend: priority badge (Pill component), priority filter dropdown, priority sort option, inline priority reasons display. 18 new tests (863 total). |
| 20    | Report approval, delivery, and client-safe workflow | **Done.** `ReportApprovalStatus` extended: `draft → under_review → approved → delivered` (+ `rejected`). `ExportRecord` gains `under_review_by/at`, `delivered_by/at`, `client_downloaded_at`, `client_download_count`. Pure `report_approval.py`: `can_submit_for_review` (any Altera internal), `can_approve/reject` (methodology_lead only), `can_deliver` (methodology_lead + admin). New `AuthContext.can_deliver_report`. Five new audit event types: `export.submitted_for_review`, `export.approved`, `export.rejected`, `export.delivered`, `export.downloaded`. Four routes added/updated: `POST .../submit-for-review`, `.../deliver`, enriched `.../approve` and `.../reject` (now emit audit events). `list_exports` filters to `approved/delivered` for client users. Client download gate accepts `approved` + `delivered`; records download metadata. `ExportRecordResponse` enriched with all lifecycle metadata (no `storage_path`). `StoreProtocol` + `InMemoryStore` + `PostgresRepository` + mappers updated. Frontend: `ApprovalStatus` + `ExportRecord` types extended; `submitExportForReview` + `deliverExport` API methods; run detail page shows lifecycle badge, metadata, Submit/Approve/Reject/Deliver buttons by role, rejection reason input, download count. 40 new tests (903 total). |
| 21    | Client-ready reporting layer                        | **Done.** `ReportDocument` Pydantic model hierarchy: `ReportMeta`, `PTReportSection`, `WWFReportSection`, `ReviewSummary`, `ClassificationSources`. `build_report_document(store, run, project, export)` assembler in `exports/report.py` — reads pre-computed `summary_payload`, counts classification sources per product, builds deterministic executive summary from `_APPROVAL_PHRASES`. `GET /projects/{id}/runs/{run_id}/report` endpoint: Altera sees all states; clients get 403 unless export is `approved` or `delivered`. Frontend: `ReportDocument` + section types in `api.ts`; `getReport()` method; `/projects/[id]/runs/[runId]/report/page.tsx` — exec summary card, methodology section (PT four-group table or WWF FG1–FG7 table), classification sources, review summary (Altera only), amber preview banner for non-approved states; "View Report" button on run detail page. 36 new tests (939 total). |
| 22    | Data coverage and uncertainty engine                | **Done.** `CoverageSection` Pydantic model added to `ReportDocument`. Pure `exports/coverage.py` assembler: upload-tier validation metrics (rows, errors, warnings), product-tier counts (total, classified, unknown, out_of_scope, sent_to_review, reviewed_by_altera, ai/rule/manual classified, missing weight/protein/category/ingredients), formatted percentage strings. Deterministic uncertainty labels (`low/medium/high`) with documented thresholds (high if errors>0 or unknown≥10% or pending≥5%; medium if ai≥30% or missing_protein≥10% or missing_weight≥10% or any pending). Methodology-specific caveats: PT (50/50 composite split, per-product split, missing protein substitution), WWF (weight-not-protein caveat, dairy equivalents, Step 1 composite classification). `TYPE_CHECKING` guard breaks circular import `exports→coverage→persistence→api→exports`. Frontend: `CoverageSection` interface in `api.ts`; `CoverageSectionCard` component with uncertainty badge, upload/product stats grid, review completion note, caveats list. 27 new tests (966 total). |
| 23A   | Nutrition enrichment foundation                     | **Done.** `NutritionEnrichmentSource` / `NutritionEnrichmentStatus` / `NutritionEnrichmentRecord` domain models in `domain/enrichment.py`. `NutritionEnrichmentProvider` structural protocol. Static `ENRICHMENT_SOURCE_REGISTRY` with 7 sources (RETAILER_PROVIDED, MANUAL_ALTERA, CATEGORY_AVERAGE available; OPEN_FOOD_FACTS, CIQUAL, OQALI, NEVO planned but `is_available=False` — no external API calls in Phase 23A). `assess_protein_enrichment_needs()` pure assessor: retailer-provided → `NOT_NEEDED`; missing → `NEEDED`. `ProteinSource.ENRICHED` added to enum; `PTProductFields.protein_pct` made optional (`Decimal | None`); `calculate_pt_run` skips products with `None` protein_pct (no silent enrichment use). Enrichment methods on `StoreProtocol` + `InMemoryStore`. `_enrichment_caveats()` in `coverage.py` appends disclosure caveats to PT coverage section. 18 new tests (984 total). |
| 23B   | Manual and category-average nutrition enrichment    | **Done.** `CategoryAverageProvider` in `enrichment/providers/category_average.py` loads a static YAML table (`enrichment/data/category_protein_averages.yaml`) with protein % averages for all four PT methodology groups (plant_based_core 15%, plant_based_non_core 12%, composite_products 10%, animal_core 18%; confidence 0.50–0.60). Three Altera-only endpoints: `GET /projects/{id}/products/{pid}/enrichments` (list records); `POST .../enrichments/manual` (create manual enrichment with validation: value 0–100, no overwrite of retailer-provided data); `POST .../enrichments/category-average` (apply group average; requires existing PT classification; rejects out_of_scope/unknown groups with 404). Calculation unchanged — enriched records stored separately, not applied to `calculate_pt_run` without explicit opt-in. `_enrichment_caveats()` updated with per-source breakdown: manual_altera and category_average counts each disclosed separately with explicit "not yet applied to this calculation" note. 30 new tests (1014 total). |
| 23C   | Explicit enriched nutrition usage in PT calculations | **Done.** `use_enriched_nutrition: bool = False` flag added to `RunCreateRequest`, `CalculateJobRequest`, `RunRecord`, `run_calculation()`, and the calculate job task payload. When `true` (Altera internal only; 403 for clients/GMS), the orchestrator pre-resolves a `{product_id: (protein_pct, NutritionEnrichmentSource)}` lookup from stored ENRICHED records before calling the pure `calculate_pt_run`. Selection logic in `enrichment/selection.py`: `select_protein_enrichment()` filters to ENRICHED records with a non-None `enriched_value` and selects by priority (`manual_altera=0 > category_average=1`; unknown sources ranked last). Retailer-provided `pt_fields.protein_pct` is never overridden. Formula unchanged. `ProteinTrackerCalculationSummary` gains 5 backward-compatible fields: `use_enriched_nutrition`, `enriched_nutrition_used_count`, `manual_enrichment_used_count`, `category_average_used_count`, `missing_protein_after_enrichment_count` (all default to 0/False for old records). `_enrichment_caveats()` dual-mode: run-mode (summary counts, "in this calculation") vs project-mode ("not yet applied to this calculation"). WWF unaffected. 29 new tests (1043 total). |
| 24A   | WWF Step 2 ingredient upload validation foundation   | **Done.** `validate_wwf_step2_json()` pure validator in `ingestion/wwf_step2.py`: keyed by `external_product_id`, validates parent exists + is own-brand composite, food group in FG1–FG6 (FG7 rejected), FG1/FG2 subgroups required and validated, ingredient weight strictly positive, sum-vs-product-weight warning. Branded composites get a warning (not error), ingredients not stored — they remain at Step 1. `Step2ValidationResult` / `ProductIngredientResult` / `IngredientRowError` dataclasses carry per-product validation detail. Three new `StoreProtocol` methods: `upsert_wwf_ingredients_for_product`, `clear_wwf_ingredients_for_project`, `get_wwf_ingredients_for_product`; `InMemoryStore` implementations added. Two new routes: `POST /projects/{id}/wwf-ingredients/upload` (accepts JSON file, GMS-only cross-org gate, stores on `is_valid`), `GET /projects/{id}/products/{pid}/wwf-ingredients`. Upload page gains a Step 2 card (shown after WWF classification) with file picker, validation summary (product counts, errors, warnings, branded/unknown notes, error detail). `WWFStep2UploadResult` type + `uploadWwfStep2` method in `api.ts`. 28 new tests (1071 total). |
| 24B   | WWF Step 2 ingredient workflow hardening              | **Done.** File-size cap (50 MB → HTTP 413) and row-count cap (200,000 → HTTP 422) enforced before any row is processed. JSON shape validation: non-dict top-level entry, missing `"ingredients"` key, non-list `"ingredients"`, empty list all produce hard errors. Re-upload semantics: valid upload atomically replaces all prior Step 2 data for the project; invalid upload preserves old records. `"replaced": bool` added to `WWFStep2UploadResponse` (and `WWFStep2UploadResult` in `api.ts`). Duplicate `(food_group, subgroup)` detection per product (warning, both rows stored). FG3/FG5 dimension fields: `fg3_subgroup: WWFFG3Subgroup | None` and `fg5_grain_kind: WWFFG5GrainKind | None` on `WWFCompositeIngredient`; `_subgroups_match_food_group` validator updated; `_whole_diet_contribution_for_ingredient` updated to route FG3 by subgroup (plant → plant split, animal → animal split, None → excluded). FG3 missing-subgroup warning emitted at upload time. `valid_product_count` fixed to only increment when `valid_ingredient_count > 0`. Coverage report caveats: `_wwf_step2_caveats()` in `coverage.py` discloses Step 2 applied count (own-brand composites with stored ingredients) and branded Step 1 count. Frontend: "max 50 MB" label, "Replaced previous data" prefix on second upload, blue "Re-run calculation" notice when data stored, stat label changed to "Own-brand stored". 34 new tests (1105 total). |
| 25A   | Recommendation engine foundation                      | **Done.** Deterministic recommendation engine — no LLM, no numeric impact estimates. `domain/recommendation.py`: `RecommendationActionType` (11 types), `RecommendationPriority` (low/medium/high/critical), `RecommendationStatus` (draft/proposed/accepted/dismissed/archived), `RecommendationCategory` (5 categories), `Recommendation` Pydantic model. `recommendations/taxonomy.py`: static dict of 11 action definitions (description, expected_direction, caveats, client_facing, altera_only per entry). `recommendations/engine.py`: pure `generate_recommendations()` function; PT rules (low plant share < 40 %, high composite pool ≥ 30 %, missing protein, high unknown ≥ 5 %, high AI share ≥ 30 %); WWF rules (step2 gap, branded composites, FG1 > PHD reference); data-quality rules (high uncertainty → critical, create_category_target). Deduplicates by action_type; stable ordering. `ReportDocument.recommendations: list[Recommendation]` added. `build_report_document` calls engine after coverage; computes own-brand/branded composite counts for WWF runs. `api.ts`: `RecommendationItem` interface + `recommendations` in `ReportDocument`. Report page: `RecommendationsCard` component (priority badge, action type, rationale, expected direction, evidence bullets, caveats; Altera-only badge for non-client-facing items). `docs/recommendations/action-taxonomy.md` created. 25 new tests (1130 total). |
| 25B   | Recommendation lifecycle and persistence              | **Done.** `PersistedRecommendation` dataclass added to `api/state.py` (id, organisation_id, project_id, run_id, methodology, all content fields, status, client_facing, created_at/updated_at, created_by/updated_by). `InMemoryStore` gains `self.recommendations: dict[UUID, PersistedRecommendation]` and 5 new methods: `upsert_recommendations_for_run` (upserts with status preservation — already-proposed/accepted/dismissed/archived recs keep their status on re-generate), `list_recommendations_for_run`, `list_recommendations_for_project`, `get_recommendation`, `update_recommendation_status`. `StoreProtocol` updated with 5 matching method signatures. `domain/recommendation.py`: `Recommendation` gains optional `id`/`run_id` fields for persisted recs. 5 new `AuditEventType` values: `RECOMMENDATION_GENERATED`, `RECOMMENDATION_PROPOSED`, `RECOMMENDATION_ACCEPTED`, `RECOMMENDATION_DISMISSED`, `RECOMMENDATION_ARCHIVED`. `AuthContext.can_propose_recommendation` added (ALTERA_METHODOLOGY_LEAD + ALTERA_ADMIN). `build_report_document` gains `is_altera: bool = True` param; prefers persisted recs (filtered by status for clients); falls back to engine for Altera preview when nothing persisted. 6 new API endpoints: `GET /runs/{run_id}/recommendations` (list; clients see proposed/accepted only), `POST .../generate` (generate+persist; Altera only), `POST /recommendations/{id}/propose` (METHODOLOGY_LEAD+ADMIN), `/dismiss`, `/archive`, `/accept`. `RecommendationResponse` Pydantic model. Frontend: `RecommendationItem` gains `id`/`run_id`; `PersistedRecommendation` type; 6 new `createApi` methods; `RecommendationsCard` updated with status badge, "Generate / refresh" button (Altera), propose/dismiss/archive/accept action buttons per item (Altera, contextual by current status). Supabase migration `0023_phase25b_recommendations.sql` with RLS (Altera sees all, clients see proposed/accepted). 25 new tests. |
| 26A   | Scenario modelling foundation                         | **Done.** Deterministic PT-only scenario projection engine — no LLM, no mutations. `domain/scenario.py`: `Scenario`, `ScenarioStatus`, `ScenarioOperation`, `ScenarioResult`, `PTProjectedSummary`, `PTProjectedGroup` Pydantic models. 4 operation types: `shift_protein_between_groups`, `increase_plant_core_protein`, `reduce_animal_core_protein`, `improve_composite_split`. Pure `project_pt_scenario()` in `scenarios/pt_projection.py`: deep-copies base protein totals, applies operations in order, clamps negatives to zero with warnings, recomputes group shares. `ScenarioRecord`, `ScenarioOperationRecord`, `ScenarioResultRecord` dataclasses in `state.py`; `InMemoryStore` gains 8 new methods; `StoreProtocol` updated. 5 API endpoints: `POST /projects/{id}/scenarios` (create; Altera only), `GET /projects/{id}/scenarios` (list; Altera only), `POST /scenarios/{id}/operations` (add op; Altera only), `POST /scenarios/{id}/run` (project; Altera; auto-promotes draft→active; 422 for WWF), `GET /scenarios/{id}/result` (Altera + clients for active scenarios). Frontend: `ScenariosPlaceholderCard` (Altera + PT only; blue info box with API endpoint reference). `api.ts`: full scenario type set + 5 API methods. Supabase migration `0024_phase26a_scenarios.sql` with RLS. `docs/scenarios/overview.md` created. 18 new tests (1180 total). WWF scenario modelling deferred. |
| 26B   | Scenario UI and recommendation bridge                 | **Done.** Backend: `GET /scenarios/{id}/operations` list endpoint added; `listScenarioOperations` in `api.ts`. Frontend: `ScenariosPlaceholderCard` replaced with full `ScenariosCard` — create form (name + description), per-scenario operations list, add-operation form with conditional parameter fields per type (amount_kg for increase/reduce/shift; plant_pct+animal_pct for composite split), Run button, inline result table (base vs projected plant/animal kg and share % with signed colour-coded deltas), projection warnings. `RecommendationsCard` gains `onCreateScenario` prop; three action types (`increase_plant_core_share`, `reduce_animal_core_dependency`, `improve_composite_breakdown`) show a **Simulate ↓** button that prefills the scenario create form with the recommendation's title and rationale and scrolls to the scenarios section. User must still enter numeric parameters — no assumptions are auto-applied. `ScenarioPrefill`/`REC_TO_OP` bridge types added. `docs/scenarios/overview.md` extended with Phase 26B UI details and recommendation bridge table. No new backend tests needed (existing 18 scenario tests cover the new list-operations endpoint behaviour; 1162 total passing). |
| 27A   | Run comparison foundation (YoY / period-to-period)    | **Done.** Pure deterministic comparison engine in `comparisons/engine.py`: `compare_pt_runs()`, `compare_wwf_runs()`, `build_run_comparison()`. Domain models in `domain/comparison.py`: `PTComparisonSummary`, `PTGroupComparison`, `WWFComparisonSummary`, `WWFFoodGroupComparison`, `RunComparisonResult`. API endpoint `GET /projects/{id}/comparisons?baseline_run_id=&comparison_run_id=`; same-run 422, mismatched-methodology 422, cross-org 404, client approval gate (approved/delivered export required per run). Response serializes all `Decimal` fields as strings; no commercial fields. Version mismatches (methodology/taxonomy/rules) emit warnings, not errors. Direction: PT from delta plant-share pp (threshold 0.1); WWF from plant weight fraction change (threshold 0.001). Frontend: **Compare runs** card on the Runs page (two selects + Compare button, direction badge, headline table, collapsible per-group breakdown, amber warnings box). `docs/comparisons/overview.md` created. 24 new tests (1210 total). |
| 28A-1 | Security: cross-org scenario result access fix        | **Done.** `GET /scenarios/{scenario_id}/result` was missing an org-membership guard; client users from other organisations could read active scenario results. Added a 404 guard before the status check. 4 regression tests added. |
| 28A-2 | Persistence: PostgresRepository protocol parity       | **Done.** 25 methods missing from `PostgresRepository` vs `StoreProtocol` (all covered by `InMemoryStore`). Implemented: upload lifecycle, WWF ingredients, review decisions, jobs, enrichment records, recommendations, scenarios. Added mappers for all new domain types, updated `upload_from_row`/`upload_to_row` with Phase 15 fields, created migration 0025 (`review_decisions` + `nutrition_enrichment_records` tables). AST-based protocol compliance check; 7 new integration tests (skipped without DB credentials). |
| 28A-3 | Audit coverage: scenarios, comparisons, enrichment    | **Done.** Three new `AuditEventType` values: `SCENARIO_RUN`, `COMPARISON_REQUESTED`, `ENRICHMENT_APPLIED`. Emitted from `run_scenario_route`, `get_run_comparison_route`, `create_manual_enrichment_route`, and `apply_category_average_enrichment_route`. Migration 0026 backfills `audit_events_action_check` constraint with all 34 action values (exports, reviews, recommendations, and the 3 new ones). 8 new tests (1194 total). |
| 28A-4 | WWF Step 2 coverage caveats and disclosure            | **Done.** `_wwf_step2_caveats()` in `coverage.py` improved: (1) Step 2 caveat now shows denominator "X of Y own-brand composite product(s)" instead of just "X"; (2) new "own-brand Step 1 only" caveat when Y−X > 0; (3) branded composite caveat refactored into single-pass loop (no duplicate `get_wwf_classification` calls); (4) new FG3 missing-subgroup caveat when any stored Step 2 FG3 ingredient row has `fg3_subgroup=None`. Docs updated: `report-structure.md` (caveats table), `wwf.md` (Step 2 coverage disclosure section). 4 new tests (1198 total). |
| 28A-5 | Production-readiness cleanup                          | **Done.** (1) ROADMAP stale section replaced (old section still referenced Phase 13B/13D ordering). (2) Org-lifecycle audit gap documented (no app-level provisioning endpoints; `ORG_CREATED`/`ORG_MEMBER_INVITED`/`ORG_ROLE_CHANGED` events defined but not yet emitted). (3) Auth predicates `can_create_scenario`, `can_apply_enrichment`, `can_generate_recommendations` added to `AuthContext` as semantic aliases for `is_altera_internal`; 7 route guards updated to use them. |
| 28B   | Operational baseline for pilot readiness              | **Done.** (1) `altera_api/observability/` module: `logging.py` (`_JsonFormatter`, `_ContextFilter`, `configure_logging()`, `get_logger()`), `middleware.py` (`RequestLoggingMiddleware` — request_id, duration_ms, path logged; auth headers never logged), `sentry.py` (`init_sentry()` with `try: import sentry_sdk` optional pattern, `_before_send()` stripping auth headers). (2) `main.py` updated: lifespan context manager calling `configure_logging()` + `init_sentry()`; `RequestLoggingMiddleware` added. (3) `.env.example` updated with `LOG_LEVEL`, `SENTRY_DSN`, `SENTRY_ENVIRONMENT`, `SENTRY_TRACES_SAMPLE_RATE`. (4) `docs/development/job-backend.md` created: `SyncDevRunner` rationale, pilot SLAs, Celery/RQ/Dramatiq swap-in guide, operational considerations. (5) 6 runbooks in `docs/development/runbooks/`: upload-failure, job-stuck, export-download-failure, rls-permission-denied, ai-classification-failure, report-delivery-issue. (6) `tests/integration/README.md` created. (7) `docs/development/deployment.md` observability section rewritten. (8) `apps/api/README.md` updated with observability config and layout. (9) Tests: `tests/observability/test_logging_middleware.py` (5 tests), `test_sentry.py` (7 tests), `test_rls_audit.py` (2 tests — RLS enabled + policy present on all 14 multi-tenant tables; no live DB required). |
| 29A   | API hardening: error standardisation + pagination     | **Done.** `ErrorDetail` model + `raise_not_found/forbidden/conflict/bad_request/unprocessable` helpers in `altera_api/api/errors.py`. `Page[T]` envelope + `PaginationParams` + `paginate()` in `altera_api/api/pagination.py`. Review queue and jobs list endpoints paginated. 13 permission regression tests in `test_phase29a_permissions.py`. `docs/saas/api.md` rewritten with error shape, pagination, auth, role matrix. |
| 29B   | API pagination extended + permission matrix           | **Done.** 6 additional list endpoints paginated: `GET /projects`, `/uploads`, `/runs`, `/exports`, `/recommendations`, `/scenarios`. Frontend `api.ts` types + all 7 `.items` unwrap call sites updated. 19-test permission matrix (`test_phase29b_permission_matrix.py`) covering role × action scenarios, org scoping, export visibility, pagination contract. `docs/saas/api.md` updated with all paginated endpoints and corrected role table. 1244 total tests. |
| 30A   | Security hardening baseline                           | **Done.** (1) `SecurityHeadersMiddleware` in `altera_api/observability/security.py`: X-Content-Type-Options, X-Frame-Options, Referrer-Policy, Permissions-Policy, Cache-Control: no-store on API paths. (2) `main.py` wired to read `CORS_ALLOWED_ORIGINS` from env (comma-separated; default `http://localhost:3000`). (3) Export download URL default expiry reduced from 3600 → 600 s across `service.py`, `protocol.py`, `fake.py`. (4) Removed real OpenAI API key from root `.env.example`. (5) 20 new tests in `tests/security/test_phase30a_security.py`: headers, CORS config, secrets safety, signed URL expiry bounds, upload validation limits. (6) `docs/development/deployment.md` security section added: headers, CORS, secrets table, signed URL policy, rate-limiting TODO, dependency audit commands, pre-pilot checklist. (7) `apps/api/README.md` production security notes. 1264 total tests. |
| 30B   | Rate limiting baseline                                | **Done.** In-memory sliding-window rate limiter in `altera_api/ratelimit.py`. Disabled by default (`RATE_LIMIT_ENABLED=false`). Four route groups with configurable per-minute limits via env vars: `uploads` (20), `classify` (10), `exports` (30), `default` (200). Keys by JWT `sub` (base64-decoded, no verification) or client IP fallback. `RateLimitMiddleware` inserted as innermost middleware so `SecurityHeadersMiddleware` stamps all 429 responses. 429 body: `{"detail": {"error_code": "rate_limited", "message": "...", "details": {"retry_after_seconds": N}}}` + `Retry-After` header. 26 new tests in `tests/security/test_phase30b_ratelimit.py` (route group, key extraction, disabled-by-default, blocking, response shape, bucket isolation). 1290 total tests. |
| 30C   | Security remediation                                  | **Done.** (1) Rate-limit keying changed to IP-only — unverified JWT `sub` removed (was exploitable). (2) `TRUSTED_PROXIES` env var (CIDR list): `X-Forwarded-For` only trusted from known proxy hosts. (3) Bucket eviction: stale empty buckets pruned every 500 checks; `RATE_LIMIT_MAX_BUCKETS` cap (default 100 000) with oldest-first eviction. (4) Route group coverage extended: legacy `POST …/uploads` → `uploads`; `…/jobs/validate` → `uploads`; `…/jobs/calculate`, `…/scenarios/{id}/run`, GET `…/comparisons` → new `compute` group (5 req/min). (5) CORS fail-closed: if `CORS_ALLOWED_ORIGINS` is unset in production mode, server refuses to start via `_check_cors_production_config()` called in `_lifespan`. (6) `.gitleaks.toml` secret-scanning config added at repo root. (7) Root `tests/conftest.py` sets `CORS_ALLOWED_ORIGINS` for all tests. 33 new tests in `tests/security/test_phase30c_remediation.py`; 30B tests updated for IP-only keying. 1323 total tests. |
| 30D   | Security polish                                       | **Done.** (1) `.gitleaks.toml` global `[allowlist] paths` section removed — it exempted `.env.example` files from all scanning rules (the exact files where a leaked key was committed). Per-rule `[rules.allowlist]` blocks are now the only exemption mechanism. (2) `RateLimiter._buckets` changed from `dict` to `collections.OrderedDict`; `move_to_end()` called on every `check()` access to maintain LRU order; cap eviction replaced `min()` O(n) scan with `popitem(last=False)` O(1). (3) `_extract_ip()`: XFF first-hop validated with `ipaddress.ip_address()` before use; malformed values fall back to peer IP rather than becoming an unvalidated bucket key. (4) Dead `_COMPUTE_SUFFIXES` constant removed from `ratelimit.py`. 11 new tests in `tests/security/test_phase30d_polish.py`. |
| 31A   | CI/CD foundation                                      | **Done.** (1) `.github/workflows/ci.yml`: three parallel jobs — `backend` (uv + pytest + ruff), `frontend` (pnpm + typecheck + lint + next build), `security` (gitleaks `--no-git` + secret-safety pytest tests). uv cache keyed on `uv.lock`; pnpm store cached via `setup-node`. Integration tests excluded from default CI run; env vars documented for staging runners. (2) `scripts/check_all.sh`: portable local pre-push script running all five checks with pass/fail summary. (3) `docs/development/ci.md`: CI job reference, integration test instructions, gitleaks git-history cleanup note, cache strategy. (4) `docs/development/deployment.md`: staging deployment section — full backend/frontend env var reference, Supabase migration flow, Storage bucket checklist, secret rotation runbook, staging deployment checklist. |
| 31B   | Git history cleanup and remote-readiness              | **Done.** (1) `docs/development/runbooks/git-history-secret-cleanup.md`: full runbook for rewriting commit `27205ca` (revoked OpenAI key) — `git filter-repo --replace-text` approach, `bfg` alternative, backup/force-push/collaboration warnings, final verification checklist. (2) `scripts/verify_no_tracked_secrets.sh`: working-tree check for real keys in tracked files, non-placeholder `.env.example` values, and gitleaks scan if installed; exits non-zero on any finding. (3) `ci.md` git history note updated to reference runbook. (4) `deployment.md` pre-pilot checklist updated with history-cleanup and `verify_no_tracked_secrets.sh` items. History rewrite was executed in Phase 31E. |
| 31C   | Staging deployment wiring                             | **Done.** (1) `apps/api/Dockerfile`: multi-stage build (uv builder → slim runtime); non-root user; `$PORT` env var support; `.dockerignore`. (2) `apps/api/render.yaml`: Render deployment template with all env vars documented and `sync: false` markers for secrets. (3) `.github/workflows/staging-smoke.yml`: manual `workflow_dispatch` smoke-test workflow (no secrets required — hits `/health`, `/version`, `/api/v1/me`). (4) `scripts/staging_smoke.sh`: portable bash smoke test accepting `API_BASE_URL` + optional `WEB_BASE_URL`; retries with `--retry 2`; exit non-zero on any failure. (5) `apps/web/README.md`: added Vercel deployment section with env var table and Auth redirect URL instructions. (6) `docs/development/deployment.md`: Docker build/run examples, Render deployment steps, Supabase staging project setup (create buckets, configure Auth redirects, verify RLS), bootstrap gap documented (manual org/membership SQL), smoke test instructions, updated staging checklist. (7) `docs/development/ci.md`: staging smoke workflow section added. |
| 31E   | Git history rewrite (Phase 31E)                       | **Done.** History rewritten 2026-05-19 using `git filter-repo --replace-text --force`. Revoked OpenAI key (original commit `27205ca`) replaced with `***REMOVED_OPENAI_KEY***` across all affected blobs. All commit hashes changed. Pre-rewrite state archived at `/tmp/altera_backup_pre_cleanup.bundle`. Runbook updated with bundle backup approach, `--force` note, and completion status. `ci.md` history note updated to reflect completion. Repo is safe to push to a private remote. |
| 31G   | Staging deployment readiness                          | **Done.** (1) `docs/development/staging-readiness.md` created: full 10-section checklist covering Supabase setup (26 migrations, private buckets, auth redirects), Render backend (secrets table, Docker config, smoke verification), Vercel frontend (env vars, root dir, Auth redirect update), bootstrap script, smoke test via GitHub Actions, known limitations table, rollback instructions, GitHub secrets for CI smoke workflow. (2) `apps/api/render.yaml` bug fixed: `dockerfilePath` corrected from `./Dockerfile` (repo root — wrong) to `apps/api/Dockerfile` (correct path relative to repo root). (3) ROADMAP updated. |
| 31F   | Private GitHub remote setup and CI verification       | **Done.** (1) gitleaks `[allowlist]` updated to include `.next/` and `.env.local` variants (gitignored build artifacts / local dev files generating false positives); guard test tightened to reject tracked source files rather than any allowlist section. (2) Repo created as private at `https://github.com/ambrealison/altera-ai`; `main` branch and `backup-pre-history-cleanup` tag pushed. (3) Frontend CI job: Node version bumped from 20 → 22 (`pnpm@11.1.2` requires Node ≥22.13; Node 20 crashed with `ERR_UNKNOWN_BUILTIN_MODULE: node:sqlite`). (4) All three CI jobs pass: backend ✅ security ✅ frontend ✅. `docs/development/ci.md` updated with remote URL, Node version rationale. |
| 31D   | First Altera admin bootstrap tooling                  | **Done.** (1) `apps/api/scripts/bootstrap_altera_admin.py`: idempotent CLI script with `--confirm`/`BOOTSTRAP_CONFIRM` safety gate, `_validate_slug`/`_validate_uuid` input validation, injectable `client` for testing. Three operations: `upsert_organisation` (by slug; returns `(id, created)` tuple), `upsert_user_profile` (upsert on `user_id`), `upsert_membership` (upsert on `user_id, organisation_id`). FK violation printed with dashboard hint. (2) `apps/api/tests/test_bootstrap.py`: 32 tests covering validation helpers, each upsert function independently, `run()` safety gate, full flow, idempotency, display name defaulting, DB error exit, and service role key not leaked to stdout. Per-table mock caching via `side_effect` ensures table call assertions are independent. (3) `docs/development/runbooks/bootstrap-first-admin.md`: three Auth user creation options (dashboard invite, CLI, Admin API), all CLI options table, expected output, idempotency behaviour, troubleshooting (FK error, slug guard, missing env), rollback SQL, adding more Altera users. (4) `docs/development/deployment.md` bootstrap section replaced with script reference; `apps/api/README.md` Bootstrap section added. |
| 31H   | Staging deployment execution                          | **Done 2026-05-19.** Backend live at `https://altera-ai.onrender.com`; frontend live at `https://altera-ai-web.vercel.app`. Smoke test workflow green; manual login + project creation verified. Deploy-time fixes shipped along the way: (a) migration `0002` removed an invalid CHECK subquery; the slug-reserved trigger is the sole enforcement. (b) migration `0019` + `0025`: `user_profiles.organisation_type` references rewritten to `visible_organisation_ids()` / `current_user_is_altera()` (column lives on `organisations`, added in `0015`). (c) migration `0023` + `0024`: replaced non-existent `organisation_members` with `current_user_is_altera()` + `current_user_organisations()` helpers. (d) migration `0027` (new): widened every legacy write policy to the namespaced roles via `user_role_can_{admin,write,review}_org_data` helpers — fixes `42501: new row violates row-level security policy` on project creation for `altera_admin`. (e) `apps/api/Dockerfile` now COPYs `README.md` before `uv sync` (pyproject references it). (f) `apps/api/render.yaml` aligned with Root Directory = `apps/api` (path relative to root). (g) Vercel: deploy from repo root via `vercel.json`; `next` declared at root `package.json` so framework detection works; root `engines.node` bumped to `>=22.13` (pnpm 11 requires Node 22.13 for `node:sqlite`). (h) auth: web login race fixed via `AuthContext.signIn` that awaits `/me` before resolving (no more `/login?next=%2F` loop). (i) auth verifier rewritten to accept Supabase ES256/JWKS tokens with the `apikey` header against `{SUPABASE_URL}/auth/v1/.well-known/jwks.json`; HS256 path preserved. (j) FastAPI handler translates PostgREST `APIError(42501)` to a structured `403 rls_denied` instead of a bare 500. Backend suite: 1382 passed. Outstanding minor warnings (not blocking): GitHub `actions/checkout@v4` deprecation notice for Node 20 (follow-up tracked in `docs/development/ci.md`); rate limiter still in-memory (per known-limitations table in `staging-readiness.md`). |
| 32A   | Client onboarding: org creation + invite flow         | **Done 2026-05-19.** `GET/POST /api/v1/admin/organisations` (list all orgs; create GMS client org with slug validation + 409 on duplicate) and `POST .../organisations/{org_id}/invite` (server-side Supabase Auth Admin API invite; pre-provisions `user_profiles` row so first login resolves to the correct org + role). All endpoints gated to `altera_admin`. `supabase_admin.py` wraps the service-role `invite_user_by_email` call; dev-mode UUID fallback when Supabase not configured (`invite_sent: false`). `create_organisation` / `list_organisations` added to `StoreProtocol`, `InMemoryStore`, and `PostgresRepository`. Frontend: `/admin` org-management page (org list, create-org form, per-org invite form), `/auth/callback` (handles `#type=invite` and `#type=recovery` hash fragments), `/reset-password` (password-set form), forgot-password toggle on `/login`, Admin nav item visible only to `isAltera` users. 11 new tests (1393 total). **Pending verification in staging:** end-to-end invite → accept invite email → set password → first login flow. Note: `ORG_CREATED` / `ORG_MEMBER_INVITED` audit events not yet emitted — wiring deferred (see Phase 32B). |
| 32B   | Client account management — member list, resend invite, role change, remove | **Done 2026-05-19.** Backend: `GET /organisations/{org_id}/members` (list all members), `POST .../members/{user_id}/resend-invite` (uses `generate_link(type="recovery")` — works for both pending and confirmed Supabase Auth users), `PATCH .../members/{user_id}` (change role; ClientRole only — Altera roles rejected 400), `DELETE .../members/{user_id}` (removes membership row; `user_profiles` + `auth.users` preserved). All endpoints `altera_admin`-gated. Audit events: `ORG_CREATED` (from create org), `ORG_MEMBER_INVITED` (from invite + resend), `ORG_ROLE_CHANGED` (from role change), `ORG_MEMBER_REMOVED` (from remove). `ORG_MEMBER_REMOVED` added to `AuditEventType` enum and migration `0028` widens the `audit_events_action_check` DB constraint. `list_members` + `remove_member` added to `StoreProtocol`, `InMemoryStore`, and `PostgresRepository`. `resend_invite()` added to `SupabaseAdminClient`. Frontend: `/admin` page rebuilt with expandable per-org members panel; inline role-change dropdown (saves on change); resend-invite and remove buttons per row; invite form inline within panel. 21 new tests (32 total in admin suite). **Pending verification in staging:** confirm resend-invite link arrives in email; confirm role change persists on next login; confirm removed member cannot log in to the org. |
| 33A   | ANSES-CIQUAL nutrition fallback + upload templates + data requirements page | **Done 2026-05-19.** (A) Upload templates: 4 authenticated `GET /api/v1/templates/*.csv` endpoints (`protein-tracker`, `wwf`, `wwf-step2-ingredients`, `business-assumptions`) returning `StreamingResponse` with `Content-Disposition: attachment`. All columns match ingestion parser expectations. (B) CIQUAL enrichment: `CiqualEntry` domain model in `domain/ciqual.py`; `CiqualProvider` with in-memory exact-name + food-group-average indexes (`confidence` 0.80 / 0.55); `scripts/import_ciqual.py` reads CIQUAL 2025 Excel via openpyxl — normalises comma decimals, handles `"-"` (missing) and `"< N"` (below-detection) markers, upserts in batches of 500; `CIQUAL` enrichment source moved from planned to available with priority 2 (above `category_average`, below `manual_altera`); `enrichment/selection.py` updated; `_enrichment_caveats()` in `coverage.py` emits CIQUAL-specific disclosure with ANSES attribution. Migration `0029_phase33a_ciqual.sql` creates `ciqual_reference` table with RLS (Altera internal only). Source Excel added to `.gitignore`; test fixture at `tests/fixtures/ciqual_sample.csv`. (C) Tests: 48 new tests — `TestParseNumeric` (10), `TestReadCiqualExcel` (5), `TestCiqualProviderMatch` (5), `TestCiqualProviderEnrich` (6), `TestEnrichmentPriority` (4), `TestProteinTrackerTemplate` (8), `TestWWFTemplate` (3), `TestWWFStep2Template` (3), `TestBusinessAssumptionsTemplate` (3). 1511 total. (D) Frontend: `/data-requirements` page with template download buttons (authenticated fetch + blob download), full Protein Tracker and WWF field tables (required/recommended/optional), Step 2 explanation, business assumptions, CIQUAL nutrition note with ANSES attribution, AI privacy note. Sidebar updated with "Data Requirements" nav item. (E) Docs: `docs/data/ciqual-enrichment.md` created with importer usage, matching logic, priority table, disclosure rules, and test-fixture note. |
| 33B   | Flexible column mapping and upload preview layer | **Done 2026-05-19.** (A) Backend — `normalise_header()` enhanced: NFKD Unicode decomposition + ASCII encoding strips accent characters (é→e, ç→c, ü→u) before punctuation collapse. `ingestion/mapping.py` (new): `CANONICAL_FIELDS` set, `_RAW_SYNONYMS` registry (17 canonical fields × ~200 synonyms, EN+FR coverage), `_SYNONYM_LOOKUP` dict, `ColumnMappingEntry` / `MappingPreviewRequest` / `MappingPreviewResult` Pydantic models, `infer_mapping()` (normalises each header, exact match → "exact", synonym → "synonym", miss → "none"; reports missing required PT/WWF fields and duplicate normalised headers; sets `enrichment_needed` for `protein_pct`), `apply_column_mapping()` (rename or "ignore"; passthrough unmapped; no-op on empty). `ingest_csv_bytes()` gains `column_mapping: dict[str, str] | None` parameter; `apply_column_mapping` called before `filter_commercial_columns` so commercial-column strip still fires. `ingest_upload` in `orchestrator.py` threads `column_mapping` through. Both upload routes updated: multipart `upload_csv` gains `column_mapping: str | None = Form(None)` (JSON-encoded); `IngestFromStorageRequest` gains `column_mapping: dict[str, str] | None = None`. `api/mapping.py` registers `POST /api/v1/uploads/preview-mapping` (authenticated; pure registry lookup — no tenant data read). `main.py` mounts `mapping_router`. (B) Frontend — `ColumnMappingEntry` + `MappingPreviewResult` types in `api.ts`; `previewMapping()` method; `uploadCsv` and `ingestUpload` accept optional `columnMapping`. `upload/page.tsx` redesigned: file input triggers `parseHeadersFromFile` (reads first 8KB, parses first non-empty line) + `previewMapping` call → new "1b. Column mapping" card shows mapping table with dropdowns pre-seeded from inference (user can override any entry or set to "ignore" / "use as-is") + diagnostics for missing required fields + duplicates → "Upload with this mapping" button builds effective `column_mapping` dict and passes to upload. (C) Tests: `tests/ingestion/test_phase33b_mapping.py` — 30 tests covering `normalise_header` enhancements (9), `infer_mapping` exact/synonym/unmatched/missing-required/duplicates/enrichment (16), `apply_column_mapping` (7), end-to-end pipeline with mapping (4); `tests/supabase/test_multi_org_rls.py` — 7 RLS regression tests for scalar-subquery fix. (D) Docs: `docs/data/column-mapping.md` created (header normalisation algorithm, synonym registry guide, API contract, security note). Also in Phase 33B: migration `0030_fix_multi_org_rls.sql` fixes `visible_organisation_ids()` CASE scalar-subquery bug (broke staging after Phase 32A/32B added multiple organisations) and `report_exports_update` policy `limit 1` bug. |

## Roadmap

All phases through 33B are complete (see status table above).
The remaining roadmap runs from Phase 32C to pilot readiness.

**Recommended next phase: Phase 32C — Audit log UI** — the highest-leverage
item for Altera-internal operators and a hard requirement for pilot sign-off
(methodology leads need an immutable audit trail visible in the UI before any
client report is delivered). Audit events are now emitted for all org
management operations (Phases 32A–32B), so the data is ready.

### Phase 32C — Audit log UI

- Internal UI surface for `audit_events`: who approved what and when,
  methodology version stamped on each decision, review decision history.
- Client-facing audit summary panel on the approved report header
  (methodology version, approval date, approver role).

### Phase 32 — Methodology version pinning + replay

- Pin methodology, taxonomy, and rules versions per project at
  approval time.
- Replay endpoint: re-run a project against its pinned versions to
  reproduce a historical number byte-for-byte.
- Required for auditability during a regulatory review.

### Phase 31 — PDF report export

- Render the `ReportDocument` to a client-branded PDF
  (client logo + colour scheme).
- Store in Supabase Storage; signed-URL download via existing
  `export.downloaded` audit path.
- Deferred at MVP because the structured JSON/CSV formats are
  sufficient for design-partner pilots.

### Phase 32 — Email notifications

- Transactional email for: upload received, processing started,
  report ready for review, report approved, report delivered.
- Provider: Supabase or Resend.

### Phase 33 — SSO for enterprise clients

- SAML / OIDC via Supabase Auth for clients with enterprise IdP
  requirements.

### Phase 34A–D — Guided retailer workflow

- **34A** — Backend `WorkflowStatus` aggregator + zero-row run guard.
- **34B** — 9-step wizard at `/projects/{id}/workflow`, full-page content
  per step, deterministic-only fast path.
- **34C** — French NEVO matching (alias dictionary), per-product
  enrichment detail, AI-unavailable status surfaced on the classify
  response, wizard redirect from project creation.
- **34D — End-to-end stabilization (current)**:
  - Massively expanded alias dictionary covering all major French food
    families (poultry, red meat, charcuterie, fish, eggs, dairy,
    legumes, cereals, fruits, vegetables, oils, prepared dishes,
    plant-protein/mock meats).
  - `ClassifyResponse.ai_disabled_reason` lets the wizard explain
    why AI did not run (env-var checks: `ALTERA_AI_CLASSIFIER_ENABLED`,
    `ALTERA_AI_PROVIDER`, `OPENAI_API_KEY`).
  - `ApplyReferencesResponse.warning` + `nevo_total_references` so
    the wizard never shows a silent "0 matched": empty reference
    tables raise an admin-facing message.
  - New diagnostic endpoint `GET /api/v1/admin/nutrition-references/stats`
    reports NEVO/CIQUAL table row counts + sample names.
  - Step 8 (Calcul) renders TWO distinct blocker panels:
    "Catégorisation incomplète" (codes: `classification_required`,
    `review_pending`, `no_eligible_products`) vs.
    "Données protéiques manquantes" (`nutrition_required`), each
    linking back to the correct wizard step.
  - Step 9 (Résultat) displays plant_kg / animal_kg / total inline.
  - Wizard remains the only normal user path; legacy pages
    (`/upload`, `/review`, `/runs/{runId}`) are kept for admin/debug.

The wizard supports sparse retailer CSVs (product name + unit weight +
volume only — no `external_product_id` required; stable internal IDs
are generated). Generalisable matching means a 15k-row file is handled
the same way as the 5-row Phase 34 sample.

**Phase 34E** — Fully inlined upload, review, and run result inside the
wizard. The normal user flow stays on `/projects/{id}/workflow`
end-to-end. Legacy `/upload`, `/review`, `/runs/{runId}` pages remain
as admin/debug only.

### Phase 34F — High-coverage AI categorisation + validation table

Root cause of the "14 attempted / 0 classified / 14 failed" report:
the AI path made one OpenAI call per product with a weak user prompt
(`json.dumps(product_card)` only, no instruction), no JSON mode, and a
strict-extras Pydantic schema that rejected any extra field the model
added. The wizard's Step 4 saw 14 failures and routed every product
to manual review.

**Fix shipped in Phase 34F:**

- Batched classification: 50 products per OpenAI call (configurable).
  ``response_format={"type": "json_object"}`` forces valid JSON;
  ``temperature=0`` for stability; ``max_tokens`` scales with batch
  size. Per-row parse failures no longer poison the whole batch — only
  the affected row goes to manual review.
- Prompt: explicit French food-family examples (pommes, poulet, tofu,
  saumon, yaourt, steak végétal, lasagnes, huile, sel, vin, etc.) plus
  every PT enum value with a one-line domain hint. Confidence guidance
  ("0.95+ for unambiguous food names, ≥0.85 for one-disambiguating-word
  cases"). System message names *only* the stable internal enum values
  so the parser cannot drift.
- Privacy: unchanged allowlist. Only product_name, brand,
  retailer_category, retailer_subcategory, ingredients_text, labels,
  language, country leave the process. The batch user message is the
  concatenation of per-product payloads, every one validated by
  ``assert_payload_allowed`` before assembly. Volumes, weights,
  items_purchased/sold, prices, margins, protein values are never sent.
- Diagnostics: ``ClassifyResponse`` now exposes ``ai_parse_failures``,
  ``ai_unsupported_category_failures``, ``ai_provider_errors``,
  ``ai_batch_count``, and ``ai_sample_errors[]``. Step 4 surfaces all
  of these in a French banner so the user knows exactly why a
  classification failed.
- ``GET /api/v1/projects/{id}/classifications`` — paginated,
  filterable (source, pt_group, confidence range, review_status,
  product_search). Returns aggregate counts so the wizard can show
  "153 déterministe / 78 IA / 5 manuel" without paging through
  the whole list. Designed to scale to 10k–15k rows.
- Wizard Step 5 — new inline category validation table renders product
  name + brand + retailer category + assigned PT category + source +
  confidence + review status, with one-click "Accepter" / "Changer".
  Manual override supersedes AI but classifications retain the audit
  trail (source=ai, ai_model, ai_prompt_version stay on the
  classification record; the manual decision is recorded separately in
  the review/audit log).

Coverage target: >95% of obvious French retailer products get
classified deterministically + AI; only genuinely ambiguous SKUs
(promotions, lots, brand-only labels) fall back to manual review.

### Phase 34G–H — OpenAI parameter/parsing compatibility

- **34G** — Token-parameter compatibility: provider sends
  `max_completion_tokens` by default; retries once with `max_tokens`
  if the server rejects the newer name. Per-model cache so a 15k-row
  run only pays the detection cost on the first call.
- **34H** — Tolerant parsing + repair retry: `extract_json_object`
  recovers from markdown fences, leading prose, bare JSON arrays,
  alternative envelope keys, BOM/zero-width chars. French category
  labels ("Végétal — cœur") normalise to internal enum values. Single
  repair retry per batch when parsing fails. Per-row tolerance so a
  missing id / unsupported category only fails that row, not the
  whole batch. Provider prefers Structured Outputs (json_schema
  strict mode) with json_object fallback.

### Phase 34I — AI becomes the primary classifier

Deterministic rule engine produces too many false positives on
retailer products with mixed-keyword names ("Poulet végétal",
"Nuggets façon poulet", "Salade Poulet César", "Burger Végétal &
Emmental"). The wizard now has 8 steps instead of 9: the
deterministic step is removed from the user-facing flow and AI is
the primary classifier.

New 8-step flow:
1. Import
2. Méthodologie
3. Classification IA (was Step 4)
4. Validation des classifications (was Step 5)
5. NEVO
6. CIQUAL + IA
7. Calcul
8. Résultat

Backend changes:
- `ClassifyRequest.skip_deterministic` flag (mutually exclusive with
  `deterministic_only`). When true, the orchestrator bypasses the
  rule engine entirely and routes every eligible non-manually-locked
  product directly to batched AI classification.
- Products whose current classification has `source=MANUAL_REVIEW`
  are skipped during re-classification — the user's manual choice
  is never overwritten.
- `workflow.py` no longer emits the `deterministic_classification`
  step in the normal flow.
- The classifications endpoint already supports `min_confidence` /
  `max_confidence` query params (added in Phase 34F); the wizard's
  validation table now exposes them with one-click presets:
  "&lt; 0.60 (à examiner)", "0.60–0.80 (à vérifier)",
  "≥ 0.80 (auto-accept)", "Tous les produits".

Legacy deterministic code stays in the repo and is still reachable
through `/uploads/{uid}/classify` with `deterministic_only=true`
(used by tests + admin/debug). The normal-user CTA in the wizard
sets `skip_deterministic=true` so AI is the sole classifier.

### Phase 34J — Tolerant batch JSON parsing

- Pydantic `BatchClassificationResponse` schema.
- `client.beta.chat.completions.parse(response_format=…)` typed path,
  with json_schema → json_object fallback.
- `_repair_missing_commas` rewrites three observed malformed patterns
  before JSON parse.
- `extract_rows_partial` salvages individual rows when the envelope
  is unrecoverable.
- The orchestrator uses repair → repair-retry → per-row salvage so a
  33-row response with comma drops in every row classifies ≥30/33
  instead of 0/33.

### Phase 34K — Progress, NEVO matching v2, classification-assumption split, partial calc

- **Progress bar fix** — a brand-new project was reporting ~65%
  because the workflow aggregator counted `locked` downstream steps
  as 100% complete. The new rule counts only the 8 user-visible
  wizard steps and only those whose status is `complete` or
  `not_needed`. Methodology is now gated on the first upload so a
  brand-new project shows 0%.
- **NEVO matching v2** — `clean_product_name()` strips packaging /
  marketing tokens (1.5kg, x4, bio, sachet, tranché, rôti, etc.) but
  preserves nutritionally meaningful tokens (0% MG, demi-écrémé,
  soja, blé, etc.). Cleaned + original tokens are merged so the
  cleaner cannot accidentally drop a relevant word. Wired into
  `candidates_for_product` so the shortlist used by the deterministic
  matcher and the AI matcher gets dramatically better recall on real
  retailer CSV names.
- **Classification-assumption split** — when NEVO returns total
  protein but no plant/animal split, the apply-references route
  derives the split from the product's PT classification:
  `plant_based_*` → 100% plant; `animal_core` → 100% animal.
  Composite / unknown classifications are left untouched (no silent
  invention) so they end up in the CIQUAL+AI fallback / manual
  review path. The records carry `classification_assumption` in their
  rationale so the audit log distinguishes them from official splits.
- **Partial calculation** — `RunCreateRequest.allow_partial=true`
  lets the run through when the ONLY blocker is `nutrition_required`.
  Classification / review / zero-eligible blockers still hard-block.
  The calculation engine drops products without usable nutrition;
  the response decorates the summary with a `coverage` block
  (total_products_start, products_included_in_calculation,
  products_excluded_missing_nutrition, product_coverage_pct,
  volume_total_start, volume_included_in_calculation,
  volume_coverage_pct, is_partial). The wizard's Step 7 shows a
  secondary "Calculer sur les données disponibles" CTA when only
  nutrition is missing, and Step 8 renders a colour-coded coverage
  disclosure banner (<50% red, 50–80% amber, ≥80% neutral).

**Deferred to Phase 34L** — explicitly NOT in 34K:
- Full nutrition validation table with manual edit (Phase 34K
  Section H) — a major UI piece requiring a new endpoint, new
  component, and per-product nutrition diagnostics.
- Per-product NEVO/CIQUAL diagnostics endpoint (Section J).
- Dedup/cache for AI nutrition matching across identical normalized
  names (Section L scalability bullet 1/2).
- Batched AI nutrition matching (Section D point 1) — currently
  per-product via `propose_match`; the architecture is in place
  (BatchClassifierProvider) and just needs the prompt + parser.
- AI-estimated split for composites with explicit provenance
  (Section G).
The 34K cut is the minimum required to unblock partial calculation
end-to-end and give the user honest coverage disclosure.

### Phase 34L — Nutrition validation + category override fix + CIQUAL removal

Five connected fixes that unblock end-to-end retailer flow:

1. **Manual category override now persists on AI-classified
   products.** Root cause: `submit_decision` required an open review
   item; high-confidence AI classifications never enter the queue,
   so the validation table's "Changer" button 404'd silently. Fix:
   when no review item exists, `submit_decision` enqueues a
   synthetic one with reason=`REQUESTED` and immediately resolves
   it through the normal pipeline. The audit trail captures the
   manual override exactly like a genuine review decision.

2. **CIQUAL removed from the normal flow.** CIQUAL provides total
   protein only (no plant/animal split) which is fundamentally
   insufficient for Protein Tracker. The wizard now has 8 steps
   with "Validation nutritionnelle" replacing "CIQUAL + IA". The
   CIQUAL endpoint stays in the backend for admin/debug; the
   `nutrition_enrichment_ciqual` step is still emitted by
   workflow.py but marked `not_needed` + `accessible=false` so it
   doesn't show in the wizard.

3. **NEVO deterministic fuzzy fallback.** Root cause for NEVO
   matching 1/33: the matcher required an EXACT case-insensitive
   name match, and retailer names like "Filets de Saumon Atlantique"
   never exact-match "Salmon, raw". Fix: when exact match fails,
   `NevoProvider._fuzzy_match` scores every entry against the
   cleaned + alias-expanded query tokens (reusing the same
   vocabulary that powers the AI shortlist) and returns the top
   candidate when score ≥ 2 tokens. "Blanc de Poulet" now finds
   "Chicken breast" without AI. Confidence = 0.75 for fuzzy
   matches (vs 0.85 for exact).

4. **Zero-row partial-run guard.** Phase 34K's `allow_partial=true`
   route let runs through even when 0 products had usable nutrition
   — producing a 0-row run with the misleading "calculé sur 0%"
   banner. Fix: after `run_calculation` returns, if `rows_count==0`
   the run record is deleted and the route returns 400 with
   `error_code: zero_usable_nutrition`.

5. **Nutrition validation table.** New endpoint
   `GET /api/v1/projects/{id}/nutrition-validations` returns one
   row per PT product with: final protein_pct / plant / animal,
   retailer original, source (retailer_csv | nevo | ciqual | manual
   | missing), match_method, split_source, confidence, status (ready
   | needs_review | missing), and a human-readable reason. Filters:
   status, source, product_search. Pagination via the existing
   PaginationParams. Manual override endpoint
   `POST /nutrition-validations/{pid}/manual` persists three
   enrichment records (protein/plant/animal) with source=manual,
   match_method=manual, confidence=1.0. Body is validated for
   numeric ranges and plant+animal sum vs total (2pp tolerance).
   The wizard's new Step 6 renders this table inline with a
   per-row "Modifier" CTA that opens three numeric inputs.

**Still deferred to Phase 34M** — documented gap:
- "Exclude from calculation" UI + persistence (the route layer
  doesn't model an "excluded" flag yet; today the user achieves
  the same effect by leaving classification empty).
- AI-estimated split for composite products with explicit
  provenance disclosure.
- Batched AI nutrition matching (still per-product).
- Dedup/cache for identical normalised names.
- Per-product retry-NEVO button.

### Phase 34M — High-coverage NEVO + eligibility alignment + NEVO-attempted state

Four targeted fixes for the staging report (7/33 NEVO matches +
"Lignes éligibles 7 / Aucun produit ne dispose de données protéiques"
contradiction):

1. **Eligibility/run contradiction root-caused.** The wizard counted
   products with NEVO enrichment records as eligible, but
   `RunCreateRequest.use_enriched_nutrition` defaulted to `False` and
   was Altera-only gated. Non-Altera users (and the wizard's default
   POST body without the flag) ran with `use_enriched_nutrition=False`
   → calculation engine ignored NEVO records → 0 rows in the run,
   while the workflow status reported 7 eligible. Phase 34M:
   `use_enriched_nutrition` defaults to `True` and the Altera-only
   gate is dropped. Workflow eligibility and the run engine now pull
   from the exact same nutrition source.

2. **NEVO-attempted state.** The workflow aggregator detected
   "complete" only when every product had nutrition (unreachable on
   real CSVs). Now Step 5 flips to `complete` once apply-references
   has been called for the project — irrespective of how many
   products actually matched. To make this signal reliable across
   stores, the apply route writes a FAILED enrichment record on
   every no-match product (was previously silent), so the
   aggregator can detect "NEVO ran" via the presence of any
   enrichment record on any product. The "all products already have
   retailer protein" case still maps to `not_needed` so the legacy
   pt_tiny flow continues to skip NEVO.

3. **Tiered fuzzy NEVO matching.** `_FUZZY_MIN_SCORE` dropped from
   2 to 1, with three confidence tiers based on overlap:
   - 3+ token overlap → 0.82 (close proxy)
   - 2 tokens → 0.72 (mid)
   - 1 token → 0.55 (broad food-family proxy)
   "Lasagnes Bolognaise" now matches "Lasagne meat" at confidence
   0.55 instead of returning no_match. The nutrition validation
   table surfaces the proxy quality via the new status palette so
   the analyst can confirm, override, or exclude.

4. **Confidence-tier statuses.** The nutrition validation row
   builder maps confidence to one of:
   - `ready` (≥ 0.85)
   - `ready_medium_confidence` (0.70–0.85)
   - `needs_review_low_confidence` (0.50–0.70)
   - `suggested_very_low_confidence` (0.30–0.50)
   - `needs_review` / `missing` otherwise
   The frontend Status column renders each with a Pill in
   appropriate tone; the aggregate panel shows the count of each
   status so the user knows at a glance how much manual work
   remains.

UI:
- Step 5 ("NEVO") shows "Continuer vers la validation nutritionnelle"
  + secondary "Relancer NEVO" after the first run. Removes the
  ambiguity of the previous "Enrichir avec NEVO" wording that
  implied the first run hadn't happened.

**Still deferred to Phase 34N**:
- AI-estimated split for composite products with explicit
  provenance.
- Explicit "Exclure du calcul" persistence + undo.
- Batched AI nutrition matching.
- AI-broad-proxy mode (currently the AI prompt asks for "closest
  match", not specifically "closest food-family proxy").

### Phase 34N — Full NEVO + calculation preflight

Three connected fixes addressing "Table NEVO : 1000 référence(s)
chargée(s)" plus the "0 lignes traitées" paradox:

1. **Supabase `.select()` 1000-row default cap removed.**
   `PostgresRepository.list_nevo_entries` issued one `.select("*")`
   call; PostgREST silently caps that at 1000 rows. NEVO 2025 v9.0
   ships ~2,328 rows so ~60% of the table was invisible to matching.
   Replaced with `_fetch_all_rows` that loops `.range(N, N+999)`
   windows until a short chunk arrives. `list_ciqual_entries` got
   the same treatment.

2. **Importer row-count floor + `--limit` escape hatch.**
   `scripts/import_nevo.py` now fails with exit 2 when fewer than
   2000 entries are parsed UNLESS the operator explicitly passes
   `--limit N`. Prevents a future truncated source file from
   silently shipping 1000 rows again.

   Exact production import command:
   ```
   uv run --directory apps/api python scripts/import_nevo.py \
     --path /path/to/NEVO2025_v9.0.csv --verbose
   ```
   With `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` set. The
   `.xlsx` variant works the same way (auto-detected by extension).
   `NEVO_DRY_RUN=1` prints rows without writing.

3. **`GET /api/v1/projects/{id}/calculation-preflight` endpoint.**
   Single source of truth for "what the next run will include":
   walks each PT-eligible product and returns `total_products`,
   `classified_products`, `products_with_volume` / `_weight` /
   `_total_protein` / `_plant_animal_split`,
   `products_ready_for_calculation` (= upcoming `rows_count`),
   `products_missing_nutrition` / `_volume_or_weight` /
   `_classification`, `products_out_of_scope`,
   `sample_exclusion_reasons[]` (first 10), plus `nevo_total_references`
   and `nevo_attempted`. The wizard's Step 7 reads this and uses
   `products_ready_for_calculation` to enable/disable the
   "Calculer sur les données disponibles" button — so the eligibility
   panel and the run engine can no longer disagree.

   Backend invariant:
   `TestCalculationPreflight.test_preflight_ready_count_matches_run_rows`
   asserts that the preflight's `products_ready_for_calculation`
   equals the subsequent `/runs` call's `rows_count`. If they drift,
   the wizard panel and the run engine are out of sync and this test
   catches it.

### Phase 34O — Bundled NEVO reference data (no local Supabase creds)

Phase 34N exposed two follow-ups: Romain couldn't run the NEVO
importer locally (the `.env` shipped in the repo carries example
secrets only, not real Supabase credentials), and the Excel-saved
CSV variant of NEVO uses commas while the official RIVM export
uses pipes. Phase 34O makes the canonical seeding path:
**bundle the file in the repo and run the importer from Render
Shell against Render's existing environment.**

Three changes:

1. **`apps/api/altera_api/data/reference/nevo2025.csv`** — the
   full NEVO 2025 v9.0 reference data is now committed (~1.4 MB,
   2,328 rows). Deployed automatically with every Render build.

2. **`--bundled` flag on `scripts/import_nevo.py`** — resolves to
   the bundled path and seeds NEVO without requiring any
   `--path` argument. Auto-detects pipe vs comma vs semicolon
   delimiter so both the official RIVM export and the "Save as
   CSV from Excel" variant work without conversion. Mutually
   exclusive with `--path`.

3. **`nevo_sanity_pass` flag on `GET /admin/nutrition-references/stats`**
   — surfaces whether the table is fully populated (row count
   ≥ 2000) so the wizard can warn about a truncated import
   without forcing the user to recall the threshold.

**Exact production seed command (Render Shell)**:
```bash
uv run --directory apps/api python scripts/import_nevo.py \
  --bundled --verbose
```
No `--path`, no local `.env` mucking. Render's existing
`SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` are picked up
automatically. The script prints `Upserted 2328/2328 rows…` and
`Done. 2328 rows upserted into nevo_reference.`

**Verifying row count after seed**:
```bash
curl -s https://<api-host>/api/v1/admin/nutrition-references/stats \
  -H "Authorization: Bearer <altera-internal-token>" | jq .
```
Look for `"nevo_total": 2328` and `"nevo_sanity_pass": true`. Or
in the wizard, navigate to a project and open Step 5 — the
"Table NEVO" panel now shows the actual row count from the DB
(no longer capped at 1000 since Phase 34N's pagination fix).

**Local dry-run** (no Supabase needed):
```bash
NEVO_DRY_RUN=1 uv run --directory apps/api python \
  scripts/import_nevo.py --bundled --verbose
```
Prints the first three parsed rows + total entry count so the
operator can sanity-check the bundle before pushing to staging.

The legacy `--path /some/file.csv` mode still works (e.g. for
testing a fresh RIVM release before bundling it).

### Phase 35 — GDPR data retention

- Configurable retention period per organisation.
- Client-driven export-and-delete for their own data.

### Phase 36 — Pilot hardening

- Load tests at expected pilot volumes (target: 100k product rows,
  10 concurrent classification jobs).
- External pen-test.
- DPA / data-processing-agreement templates reviewed by legal.

### Phase 37 — Pilot rollout

- Onboard 1–2 design-partner retailers.
- Run a full cycle: upload → classify → approve → deliver.
- Collect structured feedback; triage into Phase 38+.

### Phase 38 — Pilot readiness review

- Decision gate for GA based on pilot feedback and SLO data.

