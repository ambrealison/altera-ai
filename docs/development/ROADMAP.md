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
