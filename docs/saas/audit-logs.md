# Audit logs

Altera AI maintains two kinds of immutable audit trail:

1. **`audit_logs`** — general events (auth, role changes, exports,
   organisation lifecycle, run lifecycle).
2. **`classification_events`** — every classification decision made
   against a product, by any source (deterministic engine, AI, or a
   reviewer).

Both are append-only. Neither has `UPDATE` or `DELETE` grants for any
application role.

## What is logged in `audit_logs`

| Action                       | Actor                  | Target                |
|------------------------------|------------------------|-----------------------|
| `organisation.created`       | `auth.users.id`        | `organisations.id`    |
| `organisation.member_invited`| inviter                | invitee email + role  |
| `organisation.role_changed`  | changer                | member, old & new role|
| `project.created`            | creator                | `projects.id`         |
| `upload.created`             | uploader               | `uploads.id`          |
| `upload.dropped_columns`     | uploader               | upload + dropped names|
| `classification.batch_started`| triggering user       | upload + methodology  |
| `classification.batch_finished`| triggering user      | upload + methodology + counts|
| `run.created`                | triggerer              | `runs.id`             |
| `run.succeeded` / `failed`   | system                 | `runs.id`             |
| `export.generated`           | requester              | run + export format   |
| `export.submitted_for_review`| submitter              | export id             |
| `export.approved`            | methodology lead       | export id             |
| `export.rejected`            | methodology lead       | export id + reason    |
| `export.delivered`           | lead / admin           | export id             |
| `export.downloaded`          | client user            | export id             |
| `review.decision_made`       | reviewer               | product + methodology |
| `review.bulk_action`         | reviewer               | count + action        |
| `recommendation.generated`   | Altera user            | run id + count        |
| `recommendation.proposed`    | methodology lead/admin | recommendation id     |
| `recommendation.accepted`    | methodology lead/admin | recommendation id     |
| `recommendation.dismissed`   | Altera user            | recommendation id     |
| `recommendation.archived`    | Altera user            | recommendation id     |
| `commercial_data_block`      | system (high-severity) | upload id + field name|
| `auth.signed_in`             | the user               | the session id        |

`audit_logs.metadata` carries event-specific JSON. Every row carries
`organisation_id` so RLS scopes correctly.

## What is logged in `classification_events`

Every classification write — including the initial determination by the
deterministic engine, every AI call (success or parse failure), and
every reviewer decision — appends a row. The row records:

- `product_id`, `methodology`.
- `from_category` (nullable for the initial determination).
- `to_category`.
- `source` (`deterministic`, `ai`, `manual_review`).
- `confidence`.
- `reviewer_user_id` (nullable).
- `reason` (nullable).
- `created_at`.

Two AI calls in a row (the original and the retry on parse failure)
produce two events; the failed parse event has a `null` `to_category`
and a `reason` like `ai_parse_failed`.

## Retention

- `audit_logs` rows are retained for the life of the organisation, plus
  the 30-day soft-delete window.
- `classification_events` rows are retained as long as the parent
  product exists.
- On hard purge of an organisation, both tables are deleted; the purge
  itself is logged in a system-level `purge_logs` table that lives
  outside any organisation's RLS scope (writable only by the service
  role).

## Access

- `owner`, `admin`, and `analyst` roles can read `audit_logs` and
  `classification_events` for their organisation.
- `reviewer` and `viewer` roles can read `classification_events` for
  products they can otherwise see, but not the general `audit_logs`.
- No application role can write to either table directly; writes go
  through the application layer.

## What audit logs are not

They are not application telemetry (which lives in operational logs).
They are not a debugging tool for engineers; they are a customer-facing
record. Their schema is part of the public contract of the platform.
