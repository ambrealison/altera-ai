# Scope

This document defines what is in and out of scope for the MVP and for
identified later phases. It is the canonical reference for any
"should we build X?" question.

Altera AI is delivered as a **managed-service SaaS**: the client (a
grocery chain) uploads a catalogue and downloads an approved report;
Altera operates the platform and owns the methodology review and the
report approval gate. The scope below reflects that split.

## MVP — in scope

### Tenancy and access
- Multi-tenant: organisations, users, projects.
- **Two organisation types:**
  - `gms_client` — a retailer (e.g. Carrefour). Sees only its own
    projects in a simplified, client-facing UI.
  - `altera_internal` — the Altera team operating the platform.
    Sees all client projects in the internal-operator UI.
- Supabase Auth for sign-in.
- Row-Level Security so a `gms_client` organisation can never read
  another organisation's data, and `gms_client` users can never read
  Altera-internal-only tables (review queues, approval state for
  drafts).
- **Two role namespaces** (mutually exclusive per organisation):
  - GMS-client roles: `client_owner`, `client_admin`, `client_viewer`.
  - Altera-internal roles: `altera_admin`, `altera_analyst`,
    `altera_reviewer`, `altera_methodology_lead`.
  - See [roles.md](roles.md) for the full permission matrix.

### Data pipeline
- CSV upload of retailer / foodservice product data by a
  `client_admin` (or by Altera staff on the client's behalf).
- Optional companion JSON for WWF Step 2 ingredient-level composite
  breakdowns (own-brand only).
- Stateful upload object with validation status, classification
  status, review status, and calculation status — surfaced to Altera
  staff in full and to clients via a **simplified status mapping**.
- Input validation: required columns per enabled methodology, value
  ranges, unit normalisation (kg, g, lb, oz; protein-pct, g/100g,
  g/serving with serving size).
- Deterministic classifier driven by versioned rules and a versioned
  taxonomy that mirrors the methodologies' own category mappings (PT
  Appendix 1; WWF FG1–FG7 with subgroups).
- AI classifier (OpenAI by default, behind a provider abstraction)
  for residual / ambiguous items, with strict JSON schema validation.
- **Altera-owned manual review workflow** for low-confidence and
  AI-failed items. Clients do not see or act on the review queue.

### Project lifecycle (internal `project_status`)

```
created
  → waiting_for_client_upload
  → uploaded
  → validation
  → classification
  → altera_review_required
  → calculation
  → report_draft
  → report_under_altera_review
  → report_approved
  → delivered_to_client
  → archived
```

Client-facing simplified status (derived, never stored):

| Internal status                                                   | Client sees             |
|-------------------------------------------------------------------|-------------------------|
| `created`, `waiting_for_client_upload`                            | Waiting for upload      |
| `uploaded`, `validation`, `classification`,                       | Processing              |
| `altera_review_required`, `calculation`, `report_draft`           |                         |
| `report_under_altera_review`                                      | Under Altera review     |
| `report_approved`, `delivered_to_client`                          | Report ready            |
| `archived`                                                        | Archived                |

State transitions are enforced in a pure function in the domain layer
and surfaced via the API; invalid transitions are rejected.

### Methodologies
- **Protein Tracker** — full implementation of the four-group
  methodology (plant-based core, plant-based non-core, composite
  products, animal core) with the published 50/50 composite default
  at the group level. The per-product composite split is supported
  as a forward-compatible extension that can be disabled per
  project.
- **WWF Planet-Based Diets Retailer Methodology** — full
  implementation of FG1–FG7, animal/plant subgroups within FG1, FG2
  with dairy equivalents (cheese ×10, other ×1, plant alternatives
  ×1), FG3 plant/animal fat split, FG5 whole vs refined grains, FG7
  plant/animal snack split, composite handling at Step 1 (Meat-/
  Seafood-/Vegetarian-/Vegan-based whole-weight assignment) and Step
  2 for own-brand composites (ingredient-level food-group
  attribution).
- WWF Planetary Health Diet (PHD) reference shares for FG1–FG6 are
  included as comparison values.
- A project may run either methodology or both; results are reported
  separately and never blended.

### Outputs
- CSV export of classified and calculated rows.
- JSON export of the full result set including versions and audit
  metadata.
- Markdown report summarising the run.
- **Mandatory approval gate**: clients can download exports only when
  `report_exports.approval_status = 'approved'`. Drafts and
  under-review reports are visible to Altera staff only.

### Governance
- Project state machine for PT validation (orthogonal to the project
  lifecycle): `draft` → `submitted` → `validated`. PT figures may
  only be communicated externally as "Protein Tracker approved" after
  the `validated` state. Validation itself is an external process by
  GPA & ProVeg.
- Internal report approval: every `report_exports` row carries
  `approval_status` (`draft|approved|rejected`), `approved_by`
  (Altera user id), `approved_at`, and `delivered_to_client_at`.

### Audit
- Every calculation row stores `methodology_version`,
  `methodology_source_edition`, `taxonomy_version`, `rules_version`,
  `run_id`, `created_at`, and the identity of any reviewer who
  touched the row.
- Manual review decisions are logged as immutable events and stamped
  with `owner_type = 'altera_internal'`.
- Approval decisions are logged with the approving user, timestamp,
  and methodology version pinned.

## MVP — out of scope (explicitly deferred)

- Excel (`.xlsx`) export.
- PDF report.
- Real-time collaboration on review queues (simultaneous editing).
- SSO / SAML for clients (planned post-MVP for enterprise contracts).
- Custom methodology editor in the UI.
- Custom PHD reference shares per project.
- Whole-food-basket variant of WWF (not part of the 2024 retailer
  methodology).
- Multi-language UI (the canonical app language is English; taxonomy
  hints support multiple languages already).
- Connectors to retailer ERPs / data warehouses.
- Public API for programmatic ingestion (the API exists but is
  internal to the web app at MVP).
- Client-driven self-service manual review (the `owner_type` column
  on reviews exists so a future tier can opt in, but v1 is
  Altera-owned only).
- **Recommendation engine** — see
  [docs/future/recommendation-engine.md](../future/recommendation-engine.md).
  Out of scope until after pilot.

## Out of scope — permanent

- Sending commercially sensitive data — `sales_value`, `revenue`,
  `margin`, store-level performance, supplier terms, confidential
  strategy — to any third-party LLM. Enforced at the prompt
  construction layer; see
  [../classification/ai-inputs-policy.md](../classification/ai-inputs-policy.md).
- Producing a "blended Protein Tracker / WWF" methodology as a
  default deliverable. Experimental blended views are allowed only
  behind an internal feature flag and must be visibly labelled as
  experimental.
- Altering the published methodologies' defaults (e.g. the 50/50 PT
  composite default, the dairy ×10 factor, the PHD reference shares)
  per project. A change implies a methodology version bump in the
  codebase, not a project setting.
- Releasing a draft report to a client before Altera approval. The
  approval gate is non-bypassable.

## Definition of done for MVP

A GMS-client user can sign in, upload a catalogue, and (after Altera
processing and approval) download an approved report on a small
(~5,000 row) dataset. The Altera-internal operator can drive the full
lifecycle for that project in the internal UI — validation,
classification, manual review, calculation, draft report, approval,
delivery — without methodology drift. Every classification and
calculation is traceable to a versioned methodology, a versioned
taxonomy, versioned rules, a named reviewer where manual review
applied, and a named Altera approver on the final report.
