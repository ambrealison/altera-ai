# API design

The FastAPI backend exposes a JSON HTTP API used by the Next.js frontend.
At MVP the API is **internal** — there is no public/programmatic
contract — but it is structured and versioned so a public API can be
added without restructuring.

## Conventions

- Base path: `/api/v1`.
- Authentication: bearer JWT (see [auth.md](auth.md)).
- Resources are nested under their organisation where relevant:
  `/api/v1/orgs/{org_slug}/projects/{project_id}/uploads`.
- All bodies are JSON. Pydantic v2 models on both sides define the
  request and response schemas.
- Times are ISO 8601 with UTC `Z` suffix.
- IDs are UUIDs.
- Errors return RFC 7807 `application/problem+json` documents.

## Resources

### Organisations
- `POST /api/v1/orgs` — create.
- `GET /api/v1/orgs/{slug}` — read.
- `PATCH /api/v1/orgs/{slug}` — update name.

### Members
- `GET /api/v1/orgs/{slug}/members`
- `POST /api/v1/orgs/{slug}/members/invite`
- `PATCH /api/v1/orgs/{slug}/members/{user_id}` — change role.
- `DELETE /api/v1/orgs/{slug}/members/{user_id}`

### Projects
- `GET /api/v1/orgs/{slug}/projects`
- `POST /api/v1/orgs/{slug}/projects`
- `GET /api/v1/orgs/{slug}/projects/{id}` — returns the full internal
  `project_status` for Altera users; for `gms_client` users the
  response substitutes the client-facing simplified status (Waiting
  for upload / Processing / Under Altera review / Report ready /
  Archived).
- `POST /api/v1/orgs/{slug}/projects/{id}/transitions` — body
  `{"to": "<status>"}` requests a lifecycle transition. The pure
  domain function validates the transition; invalid transitions
  return `409 Conflict`. Available only to Altera-internal roles.
- `DELETE /api/v1/orgs/{slug}/projects/{id}` — Altera-internal only.

### Uploads
- `POST /api/v1/orgs/{slug}/projects/{id}/uploads` — returns a signed
  Supabase Storage upload URL plus a created `uploads` row.
- `POST /api/v1/orgs/{slug}/projects/{id}/uploads/{upload_id}/parse` —
  trigger parse/validate.
- `GET /api/v1/orgs/{slug}/projects/{id}/uploads/{upload_id}` — status,
  including counts and any data-quality flags.

### Classification
- `POST /api/v1/orgs/{slug}/projects/{id}/uploads/{upload_id}/classify`
  — body picks methodology(ies) and runs deterministic → AI.
  Altera-internal only.
- `GET /api/v1/orgs/{slug}/projects/{id}/uploads/{upload_id}/review_queue?methodology=...`
  — Altera-internal only (`altera_reviewer`,
  `altera_methodology_lead`, `altera_admin`). Returns `404` for
  client users to avoid disclosing the queue's existence.
- `POST /api/v1/orgs/{slug}/projects/{id}/products/{product_id}/classify`
  — manual review decision (single). Altera-internal only.
- `POST /api/v1/orgs/{slug}/projects/{id}/review/bulk` — bulk review
  decision. Altera-internal only.

### Runs
- `POST /api/v1/orgs/{slug}/projects/{id}/runs` — body picks
  methodology and the upload (or "current upload set").
- `GET /api/v1/orgs/{slug}/projects/{id}/runs`
- `GET /api/v1/orgs/{slug}/projects/{id}/runs/{run_id}` — figures and
  per-row breakdown (paginated).

### Exports and report approval
- `POST /api/v1/orgs/{slug}/projects/{id}/runs/{run_id}/exports` —
  body picks format (`csv`, `json`, `markdown`). Creates a
  `report_exports` row in state `draft`. Altera-internal only.
- `POST /api/v1/orgs/{slug}/projects/{id}/exports/{export_id}/approve`
  — body `{"release_note": "..."}`. Restricted to
  `altera_methodology_lead`. Stamps `approval_status='approved'`,
  `approved_by`, `approved_at`. Transitions project to
  `report_approved`.
- `POST /api/v1/orgs/{slug}/projects/{id}/exports/{export_id}/reject`
  — body `{"reason": "..."}` (required). Restricted to
  `altera_methodology_lead`. Stamps `approval_status='rejected'`;
  transitions project back to `report_draft`.
- `POST /api/v1/orgs/{slug}/projects/{id}/exports/{export_id}/deliver`
  — marks `delivered_to_client_at` and transitions project to
  `delivered_to_client`. Altera-internal only.
- `GET /api/v1/orgs/{slug}/projects/{id}/exports/{export_id}/download`
  — returns a signed URL. For `gms_client` users, returns `403` if
  `approval_status != 'approved'`.

### Audit
- `GET /api/v1/orgs/{slug}/audit_logs` — paginated, filterable by
  action and target.

## Versioning

The path includes `/v1`. A breaking change is `/v2`, with `/v1`
retained for at least the deprecation window. Within a major version,
additive changes are non-breaking; removing fields requires a major
version.

## Idempotency

- Mutating endpoints accept an optional `Idempotency-Key` header.
- Repeating a request with the same key returns the original response.
- Keys live for 24 hours.

## Pagination

List endpoints return:

```json
{
  "items": [...],
  "page_size": 50,
  "next_cursor": "..."
}
```

Cursor pagination is opaque. There is no offset-based pagination.

## Rate limits

Per-organisation rate limits (MVP defaults; configurable per
organisation):

- Reads: 200 req/min.
- Writes: 60 req/min.
- AI-triggering classification: 10 batches/min.

Rate-limited responses are `429` with a `Retry-After` header.
