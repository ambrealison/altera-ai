# Runbook: bootstrap the first Altera admin

## Overview

This runbook covers the one-time setup required to get a staging or
production environment to the point where an Altera employee can log in
and act as `altera_admin`.

There are three steps:

1. Create a Supabase Auth user (done in Supabase, not in application code)
2. Run the bootstrap script to create the Altera organisation, user profile,
   and `altera_admin` membership
3. Verify the login and role via the `/api/v1/me` endpoint

---

## Step 1 — Create the Supabase Auth user

The bootstrap script does **not** create Supabase Auth users. Auth user
creation must happen via one of the following methods. Do not create a
user with a weak password.

### Option A — Supabase dashboard (recommended for first user)

1. Open the Supabase dashboard → **Authentication → Users**.
2. Click **Invite user**.
3. Enter the admin email address.
4. Supabase sends an invite link; the admin sets their own password.
5. Copy the **User UID** from the Users table — you will need it in Step 2.

### Option B — Supabase CLI

```bash
# Requires the Supabase CLI and your staging project reference.
supabase auth user create \
    --email admin@altera-ai.com \
    --project-ref <PROJECT_REF>
```

This creates the user without a password. They must set one via a
password-reset flow or magic link.

### Option C — Auth admin API (service role)

```bash
# Uses the service role key — keep this command in a secure shell session.
curl -sX POST \
    "$SUPABASE_URL/auth/v1/admin/users" \
    -H "apikey: $SUPABASE_SERVICE_ROLE_KEY" \
    -H "Authorization: Bearer $SUPABASE_SERVICE_ROLE_KEY" \
    -H "Content-Type: application/json" \
    -d '{"email": "admin@altera-ai.com", "email_confirm": true}' \
    | python3 -m json.tool | grep '"id"'
```

The `id` field in the response is the user UUID to pass to the bootstrap script.

---

## Step 2 — Run the bootstrap script

### Prerequisites

- Python 3.11 and `uv` installed (already part of the backend setup)
- `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` env vars set
- Supabase migrations already applied (`supabase db push`)
- Auth user UUID from Step 1

### Run

```bash
cd apps/api

SUPABASE_URL=https://<ref>.supabase.co \
SUPABASE_SERVICE_ROLE_KEY=<service-role-key> \
uv run python scripts/bootstrap_altera_admin.py \
    --user-id <auth-user-uuid> \
    --email admin@altera-ai.com \
    --org-name "Altera AI" \
    --org-slug "altera-ai" \
    --confirm
```

### Options

| Option | Default | Description |
|---|---|---|
| `--user-id` | required | UUID of the existing Supabase Auth user |
| `--email` | required | Email matching the Auth account |
| `--org-name` | `Altera AI` | Organisation display name |
| `--org-slug` | `altera-ai` | URL slug (lowercase alphanumeric-dash, max 80 chars) |
| `--org-id` | auto-generated UUID | Pin org to a specific UUID (optional) |
| `--display-name` | local part of email | User profile display name |
| `--confirm` | — | Required safety flag |

`BOOTSTRAP_CONFIRM=true` can replace `--confirm` in CI-style environments.

### Expected output

```
Bootstrap starting…
  Organisation : 'Altera AI'  (slug='altera-ai')
  User         : 'admin@altera-ai.com'  (user_id=<uuid>)
  Role         : altera_admin

  [org]     CREATED  id=<uuid>
  [profile] UPSERTED  user_id=<uuid>
  [member]  UPSERTED  org_id=<uuid>  role=altera_admin

Bootstrap complete.
  Verify: log in as 'admin@altera-ai.com' and call GET /api/v1/me
  Expected: role='altera_admin', organisation_type='altera_internal'
```

If the organisation already exists (slug match), `[org]` shows `EXISTS`
instead of `CREATED`. The script is idempotent — running it again
produces the same database state.

---

## Step 3 — Verify

After the script completes:

1. Log in via the frontend or Supabase Auth invite link.
2. Call the API to confirm the role:

```bash
TOKEN="<jwt-from-login>"
curl -s \
    -H "Authorization: Bearer $TOKEN" \
    https://api.staging.altera-ai.com/api/v1/me \
    | python3 -m json.tool
```

Expected response:
```json
{
  "user_id": "<uuid>",
  "email": "admin@altera-ai.com",
  "organisation_id": "<org-uuid>",
  "role": "altera_admin",
  "organisation_type": "altera_internal"
}
```

---

## Idempotency

The script is safe to run multiple times:

- **Organisation**: identified by `slug` (unique constraint). If an org
  with that slug exists, its ID is returned without modification.
- **User profile**: upserted on `user_id`. Re-running updates
  `display_name` and `email` if they changed.
- **Membership**: upserted on `(user_id, organisation_id)`. Re-running
  updates the `role` if it changed.

---

## Troubleshooting

### "foreign key constraint" error

The Supabase Auth user does not exist. Go back to Step 1.

```
ERROR: Database operation failed: ...
foreign key constraint...
```

### Slug guard error (reserved slug)

The database has a trigger blocking reserved slugs (e.g. `admin`, `api`,
`app`). Choose a different `--org-slug`.

### "SUPABASE_SERVICE_ROLE_KEY is not set"

Export the variable before running:
```bash
export SUPABASE_SERVICE_ROLE_KEY=<key>
```
Never save it in a file that might be committed.

---

## Rollback / manual fix

If the bootstrap created incorrect data:

```sql
-- Undo membership (run in Supabase SQL editor with service role):
DELETE FROM memberships
WHERE user_id = '<user-uuid>' AND organisation_id = '<org-uuid>';

-- Undo user profile (only if you want to remove the profile):
DELETE FROM user_profiles WHERE user_id = '<user-uuid>';

-- Undo organisation (only if no other data references it):
DELETE FROM organisations WHERE slug = 'altera-ai';
```

Deleting the organisation will cascade-delete all related data (projects,
uploads, runs, etc.) — only do this on a fresh staging environment with
no real data.

---

## Adding more Altera users

Once the first admin is bootstrapped, additional Altera users can be
added via the same script with different `--user-id` and `--email` values:

```bash
uv run python scripts/bootstrap_altera_admin.py \
    --user-id <second-user-uuid> \
    --email analyst@altera-ai.com \
    --org-slug "altera-ai" \
    --confirm
```

This adds a second `altera_admin` membership to the same Altera
organisation without re-creating the org.

To assign a non-admin role (e.g. `altera_analyst`), edit
`bootstrap_altera_admin.py` before running — the `--role` option is
not yet exposed as a CLI flag.
