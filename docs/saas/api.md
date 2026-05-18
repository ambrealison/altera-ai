# API design

The FastAPI backend exposes a JSON HTTP API used by the Next.js frontend.
At MVP the API is **internal** — there is no public/programmatic
contract — but it is structured and versioned so a public API can be
added without restructuring.

## Conventions

- Base path: `/api/v1`.
- Authentication: `Authorization: Bearer <jwt>` header. In local dev with
  `ALTERA_DEV_AUTH_ENABLED=true` the header is optional and a fixed dev
  user is used.
- Correlation: every request should send an `X-Request-ID` header; if
  absent the backend generates a UUID. The value is echoed back in the
  `X-Request-ID` response header and is included in all structured log
  lines for that request.
- All bodies are JSON. Pydantic v2 models on both sides define the
  request and response schemas.
- Times are ISO 8601 with UTC `Z` suffix.
- IDs are UUIDs.

## Error responses

All 4xx responses carry a standard JSON envelope. FastAPI wraps the error
object under a `"detail"` key:

```json
{
  "detail": {
    "error_code": "not_found",
    "message": "project abc not found",
    "details": null
  }
}
```

| Field        | Type              | Description                                     |
|--------------|-------------------|-------------------------------------------------|
| `error_code` | string            | Machine-readable code (see table below)         |
| `message`    | string            | Human-readable description                      |
| `details`    | any \| null       | Extra context (validation errors, etc.)         |

### Common error codes

| `error_code`    | HTTP status | Meaning                                              |
|-----------------|-------------|------------------------------------------------------|
| `not_found`     | 404         | Resource does not exist or is not visible to caller  |
| `forbidden`     | 403         | Caller lacks permission for this action              |
| `conflict`      | 409         | State conflict (e.g. approve already-delivered)      |
| `bad_request`   | 400         | Malformed input                                      |
| `unprocessable` | 422         | Semantically invalid input                           |

### Common status codes

- `200 OK` — read succeeded
- `201 Created` — resource created
- `302 Found` — export download redirect to signed URL
- `400 Bad Request` — validation failure
- `401 Unauthorized` — missing or invalid JWT
- `403 Forbidden` — authenticated but insufficient role
- `404 Not Found` — resource not found (also used to hide cross-org resources from clients)
- `409 Conflict` — state machine or uniqueness conflict
- `422 Unprocessable Entity` — semantically invalid request
- `429 Too Many Requests` — rate limit exceeded

## Role rules

| Role                       | Org type        | Can do                                                         |
|----------------------------|-----------------|----------------------------------------------------------------|
| `client_viewer`            | `gms_client`    | Read own org's approved/delivered reports                      |
| `client_admin`             | `gms_client`    | Above + upload CSVs, create projects                           |
| `client_owner`             | `gms_client`    | Above + manage members                                         |
| `altera_analyst`           | `altera`        | Read all orgs, classify, trigger runs, create projects         |
| `altera_methodology_lead`  | `altera`        | Above (except create projects) + approve/reject/deliver exports and recs |
| `altera_admin`             | `altera`        | All of the above + create projects, deliver exports            |

Cross-org access: Altera-internal roles can read any org's resources.
Client users get `404` (not `403`) for another org's resources to avoid
disclosing their existence.

Draft and under-review exports are **never** visible to client users; they
receive `403` if no approved or delivered export exists.

## Pagination

High-cardinality list endpoints accept `limit` and `offset` query
parameters and return a `Page` envelope:

```json
{
  "items": [...],
  "total": 150,
  "limit": 50,
  "offset": 0
}
```

| Parameter | Default | Max | Description                   |
|-----------|---------|-----|-------------------------------|
| `limit`   | 50      | 200 | Maximum items to return       |
| `offset`  | 0       | —   | Items to skip before returning |

Endpoints that return a `Page` envelope:

- `GET /api/v1/projects`
- `GET /api/v1/projects/{project_id}/uploads`
- `GET /api/v1/projects/{project_id}/runs`
- `GET /api/v1/projects/{project_id}/runs/{run_id}/exports`
- `GET /api/v1/projects/{project_id}/runs/{run_id}/recommendations`
- `GET /api/v1/projects/{project_id}/scenarios`
- `GET /api/v1/projects/{project_id}/review`
- `GET /api/v1/projects/{project_id}/jobs`

## Resources

### Projects
- `GET /api/v1/projects` — list projects visible to the caller. Returns
  `Page[Project]`. Client users see only their own org's projects.
- `POST /api/v1/projects` — create project (`client_admin`, `client_owner`,
  `altera_analyst`, `altera_admin` only).
- `GET /api/v1/projects/{id}` — read project. Client users receive 404
  for projects belonging to another org.

### Uploads
- `POST /api/v1/projects/{id}/uploads` — prepare a CSV upload.
- `GET /api/v1/projects/{id}/uploads` — list uploads for project. Returns
  `Page[Upload]`.
- `GET /api/v1/projects/{id}/uploads/{upload_id}` — upload status.

### Classification
- `POST /api/v1/projects/{id}/uploads/{upload_id}/classify` — trigger
  deterministic → AI classification. Altera-internal only.
- `GET /api/v1/projects/{id}/review` — review queue (`Page[ReviewItem]`).
  Requires `altera_reviewer` or higher.
- `POST /api/v1/projects/{id}/review/{product_id}/{methodology}/decision`
  — submit a manual review decision.
- `POST /api/v1/projects/{id}/review/bulk` — bulk review decisions.

### Runs
- `POST /api/v1/projects/{id}/runs` — trigger a calculation.
- `GET /api/v1/projects/{id}/runs` — list runs. Returns `Page[Run]`.
- `GET /api/v1/projects/{id}/runs/{run_id}` — run detail.

### Exports and report approval
- `GET /api/v1/projects/{id}/runs/{run_id}/exports` — list exports. Returns
  `Page[ExportRecord]`. Client users see only `approved` and `delivered` exports.
- `GET /api/v1/projects/{id}/runs/{run_id}/report` — structured
  `ReportDocument`. Client users get 403 if no approved/delivered export
  exists.
- `POST /api/v1/projects/{id}/runs/{run_id}/exports/{id}/submit-for-review`
  — Altera-internal only.
- `POST /api/v1/projects/{id}/runs/{run_id}/exports/{id}/approve`
  — `altera_methodology_lead` only.
- `POST /api/v1/projects/{id}/runs/{run_id}/exports/{id}/reject`
  — `altera_methodology_lead` only.
- `POST /api/v1/projects/{id}/runs/{run_id}/exports/{id}/deliver`
  — `altera_methodology_lead` or `altera_admin` only.
- `GET /api/v1/projects/{id}/runs/{run_id}/exports/{id}/download?format=...`
  — redirects to a signed URL. Client users get 403 if no approved export
  exists.

### Recommendations
- `GET /api/v1/projects/{id}/runs/{run_id}/recommendations` — returns
  `Page[Recommendation]`. Clients see only `proposed` and `accepted` status.
- `POST /api/v1/projects/{id}/runs/{run_id}/recommendations/generate`
  — Altera-internal only.
- `POST /api/v1/recommendations/{id}/propose` — methodology lead/admin.
- `POST /api/v1/recommendations/{id}/accept` — methodology lead/admin.
- `POST /api/v1/recommendations/{id}/dismiss` — Altera-internal only.
- `POST /api/v1/recommendations/{id}/archive` — Altera-internal only.

### Scenarios
- `POST /api/v1/projects/{id}/scenarios` — Altera-internal only.
- `GET /api/v1/projects/{id}/scenarios` — returns `Page[Scenario]`. Clients
  see only active scenarios; Altera sees all statuses.
- `POST /api/v1/scenarios/{id}/run` — Altera-internal only.
- `GET /api/v1/scenarios/{id}/result`

### Jobs
- `GET /api/v1/projects/{id}/jobs` — job list (`Page[Job]`). Accepts
  optional `?job_type=` filter.
- `GET /api/v1/jobs/{job_id}` — single job status.

### Nutrition enrichment
- `GET /api/v1/projects/{id}/products/{product_id}/enrichments`
  — Altera-internal only.
- `POST /api/v1/projects/{id}/products/{product_id}/enrichments/manual`
  — Altera-internal only.
- `POST /api/v1/projects/{id}/products/{product_id}/enrichments/category-average`
  — Altera-internal only.

## Versioning

The path includes `/v1`. A breaking change is `/v2`, with `/v1`
retained for at least the deprecation window. Within a major version,
additive changes are non-breaking; removing fields requires a major
version.

## Idempotency

- Mutating endpoints accept an optional `Idempotency-Key` header.
- Repeating a request with the same key returns the original response.
- Keys live for 24 hours.

## Rate limits

> **TODO (Phase 29B):** Implement per-organisation token-bucket rate limiting
> in middleware. Planned limits (subject to change):
>
> - Reads: 200 req/min per org
> - Writes: 60 req/min per org
> - AI-triggering classification: 10 batches/min per org
>
> Rate-limited responses will return `429 Too Many Requests` with a
> `Retry-After` header.
