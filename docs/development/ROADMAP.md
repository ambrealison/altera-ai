# Roadmap

This roadmap captures the path from the current codebase to a
production pilot with one or two design-partner retailers. It runs
through **Phase 35 (pilot readiness)**.

The roadmap reflects the managed-service direction set in
[../project/vision.md](../project/vision.md): Altera operates the
platform on behalf of grocery clients, owns the methodology review,
and approves reports before client download.

## Current status (verified 2026-05-19)

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

## Roadmap

### Foundation completion (finish before product workflow)

#### Phase 13C-polish — Close out the Auth chapter
- Update `apps/api/version.py` phase string to `phase_13c_supabase_auth`.
- Update `tests/api/test_health.py` phase assertion.
- Backfill `apps/api/.env.example`, `apps/web/.env.example` with
  `SUPABASE_*`, `ALTERA_DEV_*`, `NEXT_PUBLIC_SUPABASE_*`.
- Update `apps/api/README.md`, `apps/web/README.md`, `supabase/README.md`.
- Run `pytest`, `ruff`, frontend `tsc --noEmit`, `eslint`.
- Browser smoke: sign in via Supabase, hit `/me`, see dashboard.

#### Phase 13B — Postgres persistence
- Replace `InMemoryStore` with a repository layer talking to Supabase
  Postgres (asyncpg or supabase-py). Keep `InMemoryStore` only as a
  test/dev fallback under a feature flag.
- Migrate all route handlers off the singleton store.
- Add integration tests against a local Supabase Postgres.
- RLS tests under `supabase/tests/rls/`.

#### Phase 13D — Supabase Storage
- Signed-URL uploads from the client UI; raw CSVs stored under
  `organisations/<org_id>/uploads/<upload_id>/<filename>`.
- Storage policies mirroring RLS.
- Report exports (CSV / JSON / MD) also stored in Storage with
  signed-URL download.
- Remove the in-memory CSV bytes path.

### Product workflow separation

#### Phase 14 — Organisation type + role namespace split ✓
**Complete.** See ROADMAP status table above for summary.

#### Phase 15 — Production upload pipeline ✓
**Complete.** See ROADMAP status table above for summary.

#### Phase 16 — Background jobs + async processing ✓
**Complete.** See ROADMAP status table above for summary.

#### Phase 16B — Storage-first job resolution ✓
**Complete.** See ROADMAP status table above for summary.

#### Phase 17 — AI classifier integration ✓
**Complete.** See ROADMAP status table above for summary.

#### Phase 17+ — Internal-operator UI: review queue and lifecycle
- Internal-only Next.js routes under `app/(altera)/` rendered when
  `organisation_type = 'altera_internal'`.
- Manual review queue UI: filter, assign, decide, bulk actions.
- Lifecycle dashboard: list projects by `project_status`, trigger
  transitions, see who is assigned.
- Client routes (under `app/(client)/`) hidden from Altera staff
  unless impersonating; client surface unchanged for clients.

#### Phase 18 — Advanced deterministic classification + taxonomy ✓
**Complete.** See ROADMAP status table above for summary.

#### Phase 18B — Report delivery workflow + client-facing UI
- Add `report_exports.delivered_to_client_at` + `deliver` endpoint
  (Altera staff only) for the final hand-off step after approval.
- Client download gate already enforces `approval_status = approved`
  since Phase 14; this phase adds the explicit delivery event.
- Client surface: dashboard with simplified status, upload widget,
  approved-report download, no review queue, no internal states.
- Status-mapping helper in domain + a shared TypeScript type for the
  client status enum.
- Visual differentiation from the internal UI to prevent
  cross-context confusion when Altera staff impersonate.
- Tests: deliver gate, delivery audit event.

### Auditability and trust

#### Phase 19 — Audit log surfacing
- Internal UI surface for `audit_events`: who approved, when,
  methodology version, manual-review decisions.
- Client-facing audit summary on the approved report header.

#### Phase 20 — Multi-catalogue per project (YoY)
- Allow multiple uploads per project keyed by `period`.
- Compare runs across periods; YoY trend in the report.

#### Phase 21 — Methodology version pinning + replay
- Pin methodology, taxonomy, and rules versions per project at
  approval time.
- Replay endpoint: re-run a project against its pinned versions to
  reproduce an old number byte-for-byte.

### Client experience

#### Phase 22 — Email notifications
- Upload received, processing started, report ready, project
  archived. Transactional email via Supabase or a provider.

#### Phase 23 — Billing
- Stripe integration for client billing. Seat-based +
  per-project-completion model (subject to commercial decisions).

#### Phase 24 — SSO for enterprise clients
- SAML / OIDC via Supabase Auth.

#### Phase 25 — GDPR data retention
- Configurable retention per organisation; client-driven export
  and delete operations on their own data.

### Operations and scale

#### Phase 26 — Observability
- Sentry for errors; structured JSON logs; SLO dashboards (latency,
  error rate, queue depth) on Grafana.

#### Phase 27 — Bulk client onboarding (internal tooling)
- CLI or internal UI for `altera_admin` to provision a new client
  org + invite users + create stub project in one operation.

#### Phase 28 — Localisation (client UI)
- FR, EN, DE, ES, IT for the client UI. Internal UI stays English.

#### Phase 29 — Report PDF branding per client
- Client-supplied logo + colour on the approved PDF report.
- Bring PDF export into scope (was deferred at MVP).

### Recommendation engine (post-measurement)

See [../future/recommendation-engine.md](../future/recommendation-engine.md)
for the design intent.

#### Phase 30 — Design + data plumbing
- Substitution graph extension to the taxonomy.
- Domain types for `RecommendationSet`, `Recommendation`.
- Internal-only generation; not yet client-visible.

#### Phase 31 — Rules-based substitution ranking
- Generate ranked actions from approved runs.
- Internal preview surface; an `altera_methodology_lead` reviews
  before any client release.

#### Phase 32 — LLM-assisted explanations (opt-in)
- Optional natural-language explanation per recommendation.
- LLM never sees commercial data; explanation is generated from the
  structured recommendation only.

### Pilot

#### Phase 33 — Pilot hardening
- Load tests at expected pilot volumes.
- Pen-test (external).
- DPA / data-processing-agreement templates.

#### Phase 34 — Pilot rollout
- Onboard 1–2 design-partner retailers.
- Run a full cycle: upload → approve → deliver.
- Collect feedback.

#### Phase 35 — Pilot readiness review
- Decision gate for GA.

## Recommended next implementation phase

**13C-polish, then 13B, then 13D, then 14.**

Reasoning:

- 13C-polish is ~30 minutes and closes the auth chapter cleanly so
  the version string and READMEs match reality.
- 13B (Postgres persistence) **must** land before any of the product
  workflow phases (14–17). Building the org-type/role split, the
  lifecycle state machine, and the approval gate on top of
  `InMemoryStore` would create state that vanishes on restart and
  cannot be tested under real RLS. Doing 13B after 14–17 would mean
  re-migrating the same surfaces twice.
- 13D (Storage) is needed before client-visible uploads in 18 and
  before client-downloadable approved reports in 17.
- 14–17 then deliver the product-workflow differentiation that the
  managed-SaaS direction requires.

Phases 14–17 can be sequenced as a single "Product workflow"
milestone delivered in one pass once the foundation is in place,
since they share schema migrations.
