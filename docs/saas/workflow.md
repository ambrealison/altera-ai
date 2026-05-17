# End-to-end workflow

Altera AI is a managed-service SaaS. The client (a GMS — Carrefour,
Lidl, Auchan, etc.) supplies a product catalogue and downloads an
approved report. Altera operates everything in between: validation,
classification, manual review of ambiguous items, calculation, draft
report, and the final methodology approval.

This document narrates the workflow end-to-end, the responsibilities
of the two audiences (client vs. Altera-internal), and the project
lifecycle state machine that drives it.

## Audiences

| Audience            | Organisation type   | What they do |
|---------------------|---------------------|--------------|
| **GMS client**      | `gms_client`        | Upload catalogue, see simplified status, download approved report. |
| **Altera staff**    | `altera_internal`   | Operate the pipeline, work the review queue, approve the report, mark delivery. |

The two audiences use distinct UIs over the same backend. The client
UI is intentionally narrow; the internal-operator UI exposes the full
lifecycle, the review queue, and the approval surface.

## Project lifecycle (internal `project_status`)

```
created
  → waiting_for_client_upload
  → uploaded
  → validation
  → classification
  → altera_review_required        (entered when items hit the review queue)
  → calculation
  → report_draft
  → report_under_altera_review
  → report_approved
  → delivered_to_client
  → archived
```

Allowed transitions are enforced in a pure domain function and
re-checked at the API. Invalid transitions return `409 Conflict`.

Client-facing simplified status (derived, not stored):

| Internal status                                                   | Client sees             |
|-------------------------------------------------------------------|-------------------------|
| `created`, `waiting_for_client_upload`                            | Waiting for upload      |
| `uploaded`, `validation`, `classification`,                       | Processing              |
| `altera_review_required`, `calculation`, `report_draft`           |                         |
| `report_under_altera_review`                                      | Under Altera review     |
| `report_approved`, `delivered_to_client`                          | Report ready            |
| `archived`                                                        | Archived                |

## 1. Onboarding (Altera-internal)

An `altera_admin`:

1. Creates the `gms_client` organisation (name, slug, billing
   metadata, contact).
2. Invites the first `client_owner` by email. Supabase Auth handles
   the invite flow.
3. Optionally creates the first project as a stub in state `created`
   so the client lands directly on "upload your catalogue" rather
   than an empty dashboard.

The client never self-signs-up in v1. Onboarding is a deliberate
Altera-driven step.

## 2. Project creation

Either party can transition `created → waiting_for_client_upload`:

- **Client side.** A `client_admin` opens the project in the client
  UI and confirms the methodology scope (PT, WWF, or both) and the
  reporting period.
- **Altera side.** An `altera_analyst` or `altera_methodology_lead`
  configures the project on the client's behalf (methodology version
  pin, taxonomy version pin, channel facets).

## 3. Upload (client)

The `client_admin` uploads the catalogue CSV (and, for WWF Step 2,
the optional companion ingredient JSON). The API issues a signed
upload URL for Supabase Storage and creates a `pending` `uploads`
row. After the upload completes, the client (or the UI on their
behalf) confirms — the project transitions to `uploaded`.

The client UI never shows row-by-row validation errors as a wall of
detail; it shows a summary count and "Altera is reviewing this." The
detailed report goes to Altera staff.

## 4. Validation (Altera-internal, automated)

The project transitions `uploaded → validation`:

- The file is streamed from Storage.
- Headers are normalised; forbidden commercial columns are dropped
  (an audit event is written).
- Each row is validated and normalised; protein units are converted
  to `g/100g` per [docs/data/unit-conversion.md](../data/unit-conversion.md).
- Valid rows are persisted as `products`.
- Invalid rows are recorded with a row-level error code in the
  upload's data-quality report.

If validation surfaces hard failures (missing required columns,
unreadable file), the project routes to `altera_review_required`
with a human task: "contact client, request corrected upload." Soft
failures (a fraction of unparsable rows) flow through to
classification.

## 5. Classification (Altera-internal, automated)

`validation → classification`:

- The **deterministic rules engine** runs over all in-scope products
  for each enabled methodology. Matched products are assigned a
  category with `confidence = 1.0`.
- The **AI classifier** runs over the residual (pass-through products
  plus rule collisions flagged for review). Only allowed inputs are
  included; the strict JSON output is validated.
- Products that the AI classified with low confidence, that failed
  parsing, or that produced rule collisions are routed to the
  **Altera-internal manual review queue**. The project transitions
  to `altera_review_required`.
- An audit `classification.batch_finished` is written with counts.

For a project with both methodologies, the two pipelines run
independently; they do not share rule files or AI calls.

## 6. Manual review (Altera-internal)

`altera_review_required → calculation` after the queue is cleared.

An `altera_reviewer` (or `altera_methodology_lead`) works the queue:

- Items are filtered by methodology, reason, retailer category,
  brand, and assigned reviewer.
- The reviewer accepts, changes, or defers each item, optionally
  with a reason.
- Every decision writes a `classification_events` row and updates
  the `classifications` row to `source = 'manual_review'`.
- Every manual review row carries `owner_type = 'altera_internal'`.

**Clients do not see this queue.** The client UI shows only
"Processing" during this phase.

## 7. Calculation (Altera-internal, automated)

`calculation → report_draft`:

- The run reads the active `classifications` for that methodology.
- Pulls per-row protein values and the weighting basis.
- Computes per-row plant and animal grams (PT) or per-food-group
  weights (WWF).
- Stores `calculation_rows` and writes a `runs` row stamped with the
  versions in use.

Two methodologies produce two runs. Reports treat them as parallel
sections, never merged.

## 8. Draft report (Altera-internal)

`report_draft → report_under_altera_review`:

An `altera_analyst` generates the draft report. The Altera UI shows
the full report block (see
[../outputs/report-structure.md](../outputs/report-structure.md))
including all data-quality flags and methodology-specific
interpretation notes. Drafts are visible only to Altera staff.

## 9. Approval (Altera-internal — methodology lead only)

`report_under_altera_review → report_approved` *or* back to
`report_draft` on rejection.

An `altera_methodology_lead`:

- Reviews the draft report against the methodology version pinned on
  the project.
- Either:
  - **Approves**: writes `approval_status = 'approved'`, stamps
    `approved_by` (their user id) and `approved_at`. The project
    transitions to `report_approved`. An optional release note is
    captured.
  - **Rejects**: writes `approval_status = 'rejected'` with a
    required reason. The project transitions back to `report_draft`
    so an analyst can rework (rerun review, recompute, regenerate).

`altera_admin` cannot approve; the role separation is intentional.

## 10. Delivery (Altera-internal → client)

`report_approved → delivered_to_client`:

An `altera_analyst`, `altera_methodology_lead`, or `altera_admin`
clicks "release to client." This:

- Stamps `delivered_to_client_at` on the `report_exports` row.
- Sends an email notification to the client contacts.
- Flips the client-facing status from "Under Altera review" to
  "Report ready."

The client can now download the approved exports (CSV, JSON,
Markdown) from the client UI. The download endpoint refuses anything
where `approval_status != 'approved'`.

## 11. Closing out

`delivered_to_client → archived`:

An `altera_methodology_lead` or `altera_admin` archives the project
once the client confirms receipt (or after a configurable retention
period). Archived projects are read-only; the approved report
remains downloadable.

## Re-running

A run can be repeated for cause (e.g. corrected upload, methodology
version bump):

- The project transitions back to an earlier internal state.
- Each re-run creates a new `runs` row; prior runs are retained for
  audit.
- A re-issued report requires a fresh approval; the prior approval
  is not inherited.

## What the workflow does not include

- **Client self-service over methodology decisions.** The client
  uploads and downloads; everything else is Altera's job. This is a
  deliberate product principle, not a missing feature.
- **Live edits to methodology rules from the UI.** Methodology and
  taxonomy changes are deliberate releases, not knobs in the user
  interface.
- **Cross-organisation visibility of any kind** between client
  organisations.
- **Sending any commercial data to the AI, ever.** See
  [../classification/ai-inputs-policy.md](../classification/ai-inputs-policy.md).
- **Releasing a draft or under-review report to the client.** The
  approval gate is non-bypassable.

## Background jobs (Phase 16)

Long-running pipeline steps are tracked as **Jobs** in the `jobs` table
rather than executing inline in HTTP request handlers.

| Job type          | Triggered by                                     |
|-------------------|--------------------------------------------------|
| `validate_upload` | `POST /uploads/{id}/jobs/validate`               |
| `ingest_upload`   | `POST /uploads/{id}/jobs/ingest`                 |
| `classify_upload` | `POST /uploads/{id}/jobs/classify`               |
| `run_calculation` | `POST /projects/{id}/jobs/calculate`             |
| `generate_export` | `POST /runs/{id}/jobs/export`                    |
| `generate_report` | Placeholder — Phase 18+                          |

**Job lifecycle**: `queued → running → succeeded | failed | cancelled | retrying`

Each lifecycle event is written to the audit log. Idempotency keys prevent
duplicate active jobs of the same type for the same upload/project.

**Current implementation**: `SyncDevRunner` executes jobs synchronously in
the calling thread (suitable for dev/test). The `WorkerBackend` protocol
makes this swappable for Celery, RQ, or Dramatiq without touching routes
or task handlers. The frontend polls `GET /jobs/{id}` for async status.

The original synchronous HTTP endpoints remain available for backwards
compatibility and for the direct multipart upload flow.

## AI classifier (Phase 17)

The `classify_upload` job integrates an optional AI classifier between
the deterministic rules engine and the manual review queue:

1. Deterministic rules run first; matched products are classified with
   `confidence = 1.0` and do not reach the AI.
2. Pass-through products (no deterministic match) are sent to the AI
   provider if `ALTERA_AI_CLASSIFIER_ENABLED=true`.
3. AI results are routed:
   - `confidence >= 0.8` → accepted, stored as AI classification.
   - `confidence < 0.8` → `ManualReviewQueueReason.LOW_CONFIDENCE`.
   - JSON parse failure → `ManualReviewQueueReason.AI_PARSE_FAILED`.
   - Provider error → `ManualReviewQueueReason.AI_PROVIDER_ERROR`.
4. Rule-collision products bypass the AI and go directly to
   `ManualReviewQueueReason.RULE_COLLISION`.

The `classify_upload` job result includes `ai_attempted`, `ai_accepted`,
`ai_review`, and `ai_failed` counts (zero when AI is disabled).

The AI never receives commercial data. See
[../classification/ai-inputs-policy.md](../classification/ai-inputs-policy.md).
