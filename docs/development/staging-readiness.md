# Staging deployment readiness

> **Status: green — staging deployed 2026-05-19. Phase 33B deployed.**
>
> | Component | URL | Notes |
> |---|---|---|
> | Backend (Render) | https://altera-ai.onrender.com | `/health`, `/version`, `/api/v1/me` all 2xx |
> | Frontend (Vercel) | https://altera-ai-web.vercel.app | Login + create-project verified end-to-end |
> | Supabase staging | (project-internal) | All 29 migrations applied; `uploads` + `exports` buckets private; first Altera admin bootstrapped |
> | GitHub Actions smoke | `staging-smoke.yml` | Green on commit `1cd9a20` (Phase 32A) |
> | Admin page | `/admin` | Available; org creation + invite flow verified; member list/resend/role-change/remove **pending end-to-end test** |
> | Data Requirements page | `/data-requirements` | Available; template download buttons require authentication; CIQUAL table requires migration `0029` + importer run |
>
> **Phase 33A post-deploy checklist:**
> - Run `scripts/import_ciqual.py` against staging DB with the CIQUAL 2025 Excel file to populate `ciqual_reference`.
> - Verify `/api/v1/templates/protein-tracker.csv` returns `200` with correct headers.
> - Verify `/data-requirements` page loads and download buttons work in browser.
>
> **Phase 33G post-deploy checklist (NEVO):**
> 1. Apply migration `0032_phase33g_nevo_reference.sql`:
>    ```bash
>    supabase db push --linked
>    ```
> 2. Confirm the table exists and is Altera-RLS-gated (SQL editor):
>    ```sql
>    select count(*) from public.nevo_reference;       -- expect 0 pre-import
>    \d public.nevo_reference                          -- columns, indexes, constraints
>    ```
> 3. Import NEVO 2025 v9.0 (operator-side, service-role; **never** commit
>    the workbook):
>    ```bash
>    SUPABASE_URL=https://<project>.supabase.co \
>    SUPABASE_SERVICE_ROLE_KEY=<service_role_key> \
>      uv run python apps/api/scripts/import_nevo.py \
>        --path "/path/to/NEVO2025_v9.0 (1).xlsx" -v
>    ```
>    Expected import summary: **2328 entries parsed**.
> 4. Verify row counts in SQL editor:
>    ```sql
>    select count(*) from public.nevo_reference;
>    -- expected: 2328
>
>    select count(*) from public.nevo_reference
>    where plant_protein_g_per_100g is not null
>      and animal_protein_g_per_100g is not null;
>    -- expected: 2327 (one entry lacks the PROTPL/PROTAN split)
>    ```
> 5. Trigger a Render redeploy of the API service so the new caveat text
>    in `exports/coverage.py` is live, then run a PT calculation on any
>    classified project and confirm the report's coverage section starts
>    with the plant/animal-split provenance caveat citing NEVO and noting
>    "CIQUAL provides total protein only".
> 6. RLS sanity (recommended): as a non-Altera user, `select * from
>    public.nevo_reference limit 1` must return zero rows / permission
>    denied — never raw rows.
>
> Use this checklist as the playbook for *future* environments
> (production, secondary regions). The fixes shipped while bringing
> staging up are catalogued in the Phase 31H entry of
> [ROADMAP.md](ROADMAP.md).

Follow this checklist top-to-bottom on first staging deployment.
Each section is self-contained; earlier sections must complete before later ones.

Related docs:
- [deployment.md](deployment.md) — env var reference and security checklist
- [ci.md](ci.md) — CI jobs, Node version, remote URL
- [runbooks/bootstrap-first-admin.md](runbooks/bootstrap-first-admin.md) — first admin setup

---

## 0. Pre-deployment gate

```bash
# Verify your local branch is in sync with origin before expecting
# Render/Vercel to pick up the commit.
git log --oneline --decorate -3
```

Expected output includes `HEAD -> main, origin/main` on the latest commit.
If `origin/main` is absent, the commit has not been pushed — run `git push`
before triggering a deploy.

```bash
# Must be green before proceeding.
gh run list --repo ambrealison/altera-ai --limit 3
```

Expected: latest CI run shows `success` for all three jobs (backend, frontend, security).

```bash
# Must pass.
bash scripts/verify_no_tracked_secrets.sh

# Optional full-history scan (requires gitleaks):
gitleaks detect --source . --config .gitleaks.toml
```

---

## 1. Supabase staging project

### 1a. Create the project

In the [Supabase dashboard](https://supabase.com/dashboard):

1. **New project** → choose your organisation.
2. Name: `altera-staging` (or similar).
3. Region: match your Render region (`Oregon` = `us-west-2`).
4. Note the following — you will need them later:
   - **Project reference ID** (`<STAGING_REF>`)
   - **Project URL** (`https://<ref>.supabase.co`)
   - **Anon/publishable key**
   - **Service role key** (keep secret)
   - **JWT secret** (Settings → API → JWT Settings)
   - **Database password** (needed for DATABASE_URL if used)

### 1b. Link the CLI and apply migrations

```bash
# One-time: link your local Supabase CLI to the staging project.
supabase link --project-ref <STAGING_REF>

# Apply all 27 migrations.
supabase db push --project-ref <STAGING_REF>
```

Migrations applied (in order):
```
0001_extensions_and_helpers.sql  →  0027_phase14b_write_role_namespaces.sql
```

Verify:
```bash
supabase db diff --project-ref <STAGING_REF>
# Expected: no diff (all migrations applied)
```

### 1c. Verify RLS policies (no live DB required)

```bash
cd apps/api
uv run pytest tests/observability/test_rls_audit.py -v
# Expected: all tables have RLS enabled and at least one policy
```

### 1d. Create storage buckets

In the Supabase dashboard → **Storage**, or via CLI:

```bash
# Both buckets must be PRIVATE (not public).
supabase storage create-bucket uploads --project-ref <STAGING_REF> --private
supabase storage create-bucket exports --project-ref <STAGING_REF> --private
```

Verify in the dashboard: both buckets show as **Private**.

Signed URL expiry configured in `StorageService`:
- uploads: 300 s
- exports: 600 s

### 1e. Configure Auth redirect URLs

In the Supabase dashboard → **Authentication → URL Configuration**:

Add redirect URLs:
```
https://<your-vercel-deployment>.vercel.app/auth/callback
https://staging.altera-ai.com/auth/callback
```

Replace `<your-vercel-deployment>` with the actual Vercel preview URL once
known; update again after pointing a custom domain.

### 1f. Confirm checklist

- [ ] Project created and project ref noted
- [ ] All 27 migrations applied (`supabase db push`)
- [ ] `supabase db diff` shows no pending changes
- [ ] RLS audit pytest passes
- [ ] `uploads` bucket created and PRIVATE
- [ ] `exports` bucket created and PRIVATE
- [ ] Auth redirect URLs configured

---

## 2. Backend — Render

### 2a. Required secrets

Set the following in the **Render dashboard → Environment** for the service
(or in a [Secret Group](https://render.com/docs/secret-groups)). These are
marked `sync: false` in `apps/api/render.yaml` — they are never stored in the
repo.

| Variable | Where to find it |
|---|---|
| `SUPABASE_URL` | Supabase dashboard → Settings → API → Project URL |
| `SUPABASE_JWT_SECRET` | Supabase dashboard → Settings → API → JWT Settings |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase dashboard → Settings → API → Service role key |
| `SUPABASE_ANON_KEY` | Supabase dashboard → Settings → API → Anon/publishable key |
| `OPENAI_API_KEY` | OpenAI dashboard → API keys |
| `SENTRY_DSN` | Sentry dashboard → Project Settings → Client Keys (optional) |

Variables in `render.yaml` with hardcoded values (no action needed):

| Variable | Value | Note |
|---|---|---|
| `CORS_ALLOWED_ORIGINS` | `https://staging.altera-ai.com` | Update with real Vercel URL |
| `ALTERA_USE_IN_MEMORY_STORE` | `false` | Postgres-backed |
| `ALTERA_DEV_AUTH_ENABLED` | `false` | Required in staging |
| `ALTERA_AI_CLASSIFIER_ENABLED` | `true` | Enables AI classification |
| `ALTERA_AI_PROVIDER` | `openai` | |
| `RATE_LIMIT_ENABLED` | `true` | Single-process rate limiting |
| `LOG_LEVEL` | `INFO` | |
| `SENTRY_ENVIRONMENT` | `staging` | |

Optional rate-limit overrides (defaults shown):
```
RATE_LIMIT_UPLOADS_PER_MINUTE=20
RATE_LIMIT_CLASSIFY_PER_MINUTE=10
RATE_LIMIT_EXPORTS_PER_MINUTE=30
RATE_LIMIT_COMPUTE_PER_MINUTE=5
RATE_LIMIT_DEFAULT_PER_MINUTE=200
RATE_LIMIT_MAX_BUCKETS=100000
```

### 2b. Create the Render service

1. In the Render dashboard → **New → Web Service**.
2. Connect the GitHub repo: `ambrealison/altera-ai`.
3. Select **Deploy from Render YAML** if prompted, or configure manually:
   - **Runtime**: Docker
   - **Root Directory**: `apps/api` ← the service builds from this subdirectory
   - **Dockerfile path**: `Dockerfile` ← relative to Root Directory
   - **Health check path**: `/health`
   - **Plan**: Starter (upgrade later)
   - **Region**: Oregon (match Supabase)
4. Set all `sync: false` secrets in the environment tab.
5. Update `CORS_ALLOWED_ORIGINS` to the real Vercel URL.
6. Leave **Auto-deploy: off** until first deployment is verified.
7. Click **Deploy**.

### 2c. Verify backend

Once the service is running (green health indicator):

```bash
# Replace with your actual Render service URL.
API_BASE_URL=https://altera-api.onrender.com \
  bash scripts/staging_smoke.sh
```

Expected output:
```
=== Backend: https://altera-api.onrender.com ===
  OK    GET /health (HTTP 200)
  OK    GET /version (HTTP 200)
  OK    GET /api/v1/me (expect 401) (HTTP 401)
```

### 2d. Confirm checklist

- [ ] All secrets set in Render environment
- [ ] `CORS_ALLOWED_ORIGINS` updated with real Vercel URL
- [ ] Service deployed and health check green
- [ ] `staging_smoke.sh` passes
- [ ] `ALTERA_DEV_AUTH_ENABLED=false` visible in Render logs

---

## 3. Frontend — Vercel

### 3a. Required env vars

Set these in Vercel → Project Settings → Environment Variables (all environments
or staging only):

| Variable | Value | Note |
|---|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | `https://altera-api.onrender.com` | Backend URL |
| `NEXT_PUBLIC_SUPABASE_URL` | `https://<ref>.supabase.co` | From Supabase dashboard |
| `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` | `sb_publishable_...` | Anon key — safe to expose |

These are baked into the client bundle at build time. Do **not** add
`SUPABASE_SERVICE_ROLE_KEY` or any backend-only secrets here.

### 3b. Create the Vercel project

This is a pnpm workspace — install must run from the repo root so pnpm
can resolve `pnpm-workspace.yaml` and the root `pnpm-lock.yaml`. The
exact commands are committed at [vercel.json](../../vercel.json); the
dashboard only needs the Root Directory and Node version.

1. In the Vercel dashboard → **Add New → Project**.
2. Import from GitHub: `ambrealison/altera-ai`.
3. **Root Directory**: `.` (repo root — leave the field blank or set `./`).
4. **Framework Preset**: Next.js (`vercel.json` already sets this).
5. **Node.js Version**: 22.x — required by `pnpm@11.1.2`.
6. **Install Command** / **Build Command** / **Output Directory**:
   leave blank in the dashboard. `vercel.json` pins them to:
   - install: `corepack enable && pnpm install --frozen-lockfile`
   - build: `pnpm --filter @altera-ai/web build`
   - output: `apps/web/.next`
7. Set the three env vars above.
8. Deploy.

### 3c. After deploy — update Auth redirect URLs

Once you have the Vercel deployment URL (e.g. `altera-ai-git-main-xxx.vercel.app`):

1. Go to Supabase → **Authentication → URL Configuration**.
2. Add: `https://altera-ai-git-main-xxx.vercel.app/auth/callback`
3. Update `CORS_ALLOWED_ORIGINS` in Render to include the Vercel URL.

### 3d. Verify frontend

```bash
WEB_BASE_URL=https://altera-ai-git-main-xxx.vercel.app \
API_BASE_URL=https://altera-api.onrender.com \
  bash scripts/staging_smoke.sh
```

### 3e. Confirm checklist

- [ ] Vercel project connected to `ambrealison/altera-ai`
- [ ] Root directory left at `.` (repo root) so `vercel.json` is honoured
- [ ] Node.js version set to 22.x
- [ ] All three env vars set
- [ ] Deployment succeeded
- [ ] Auth redirect URL added in Supabase
- [ ] `CORS_ALLOWED_ORIGINS` on Render updated with Vercel URL
- [ ] Login page loads at `/login`

---

## 4. Bootstrap first Altera admin

After both backend and Supabase are running. Full runbook:
[`docs/development/runbooks/bootstrap-first-admin.md`](runbooks/bootstrap-first-admin.md)

### 4a. Create the Supabase Auth user

In the Supabase dashboard → **Authentication → Users → Invite user**.

Enter the admin email (e.g. `admin@altera-ai.com`). The invited user sets
their own password via the invite link.

Copy the **User UID** from the Users table.

### 4b. Run the bootstrap script

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

Expected output:
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

### 4c. Verify

```bash
# Get a JWT by logging in via the frontend, then:
TOKEN="<jwt-from-login>"
curl -s \
    -H "Authorization: Bearer $TOKEN" \
    https://altera-api.onrender.com/api/v1/me \
    | python3 -m json.tool
```

Expected:
```json
{
  "user_id": "<uuid>",
  "email": "admin@altera-ai.com",
  "organisation_id": "<org-uuid>",
  "role": "altera_admin",
  "organisation_type": "altera_internal"
}
```

### 4d. Confirm checklist

- [ ] Supabase Auth user created and invite accepted
- [ ] Bootstrap script ran with `--confirm`
- [ ] `GET /api/v1/me` returns `role=altera_admin`

---

## 5. Smoke test via GitHub Actions

After both services are live:

1. Go to **Actions → Staging smoke test → Run workflow**.
2. Enter:
   - **Backend URL**: `https://altera-api.onrender.com`
   - **Frontend URL**: `https://altera-ai-git-main-xxx.vercel.app` (optional)
3. Run — all checks should show `OK`.

Or run locally:

```bash
API_BASE_URL=https://altera-api.onrender.com \
WEB_BASE_URL=https://altera-ai-git-main-xxx.vercel.app \
  bash scripts/staging_smoke.sh
```

---

## 6. Full staging deployment checklist

- [ ] `git log --oneline --decorate -3` shows `HEAD -> main, origin/main` on latest commit
- [ ] CI green (`gh run list --limit 3`)
- [ ] `verify_no_tracked_secrets.sh` passes
- [ ] Supabase project created, region matches Render
- [ ] All 27 migrations applied
- [ ] RLS audit pytest passes
- [ ] `uploads` bucket created and PRIVATE
- [ ] `exports` bucket created and PRIVATE
- [ ] Auth redirect URLs configured in Supabase
- [ ] Backend secrets set in Render (6 `sync: false` vars)
- [ ] `CORS_ALLOWED_ORIGINS` updated with real Vercel URL
- [ ] Render service deployed and health check green
- [ ] `staging_smoke.sh` passes (backend)
- [ ] `ALTERA_DEV_AUTH_ENABLED=false` in Render logs
- [ ] Vercel project connected, root dir `.` (repo root) so `vercel.json` is honoured
- [ ] Frontend env vars set in Vercel (3 vars)
- [ ] Frontend deployed, login page loads
- [ ] Supabase Auth redirect URL includes Vercel domain
- [ ] First Altera admin bootstrapped
- [ ] `GET /api/v1/me` returns `role=altera_admin`
- [ ] GitHub Actions smoke test passes
- [ ] Sentry events visible (if `SENTRY_DSN` configured)
- [ ] `autoDeploy: false` confirmed — do not enable until all checks pass

---

## 7. Enabling auto-deploy

Once the full checklist is green and at least one test upload has been
processed successfully:

1. In Render: Service settings → **Auto-Deploy: Yes** (or update render.yaml
   and push).
2. In Vercel: auto-deploy is on by default for main branch pushes.
3. Update `autoDeploy: false` → `true` in `apps/api/render.yaml` and commit.

---

## 8. Known limitations

| Limitation | Detail | Workaround |
|---|---|---|
| Rate limiter is in-memory, single-process | One Render instance only | Use Render zero-downtime deploys; scale to Redis/Upstash for multi-instance |
| Integration tests skipped in CI | `--ignore=tests/integration` | Set `SUPABASE_URL` + secrets as GitHub repo secrets; add a separate `integration` job |
| Auto-deploy disabled | `autoDeploy: false` in render.yaml | Enable after first verified deployment |
| `TRUSTED_PROXIES` not set | `X-Forwarded-For` not trusted | Add Render's static egress IPs when known — see `render.yaml` comment |
| Sentry optional | No alerting by default | Set `SENTRY_DSN` to enable error tracking |
| No CDN in front of API | HSTS not set at app layer | Configure HSTS at Render's custom domain / Cloudflare |

---

## 9. Rollback

**Backend**: In Render → Deploys → click any prior successful deploy → **Redeploy**.

**Frontend**: In Vercel → Deployments → click any prior successful deployment → **Redeploy**.

**Database**: Migrations are forward-only. To roll back a migration:

```sql
-- Run in the Supabase SQL editor with service role.
-- Check each migration file for its reverse; there is no auto-rollback.
-- See docs/development/deployment.md → Migrations for the two-release strategy.
```

For a full reset of a staging environment with no real data:

```sql
-- Deletes the Altera org and all related data (cascade).
-- Use only on staging with no real client data.
DELETE FROM organisations WHERE slug = 'altera-ai';
```

---

## 10. GitHub secrets for CI smoke workflow

The staging smoke test workflow (`staging-smoke.yml`) is `workflow_dispatch` only
and takes URLs as inputs — it does **not** require any GitHub secrets.

If you later add a scheduled smoke test that auto-reads the staging URL, add
these as **repository secrets** (`Settings → Secrets and variables → Actions`):

| Secret name | Value |
|---|---|
| `STAGING_API_URL` | `https://altera-api.onrender.com` |
| `STAGING_WEB_URL` | `https://staging.altera-ai.com` |

---

## 11. Client onboarding flow (Phase 32A)

Phase 32A shipped the Altera-admin org creation and invite flow. Use the
steps below to verify it end-to-end in staging after deployment.

### Prerequisites

- The first `altera_admin` user is bootstrapped and you can log in as them.
- `SUPABASE_SERVICE_ROLE_KEY` is set in the Render environment.
- A valid redirect URL (`https://altera-ai-web.vercel.app/auth/callback`)
  is registered in Supabase → Authentication → URL Configuration.

### 11a. Create a client organisation

1. Log in as `altera_admin` at `https://altera-ai-web.vercel.app`.
2. Navigate to **Admin** in the sidebar.
3. In **Create Organisation**, enter a name (e.g. `Acme Retail`) and slug
   (`acme-retail`). Click **Create**.
4. Expected: the org appears in the list below.

Or via API:

```bash
TOKEN="<altera-admin-jwt>"
curl -s -X POST https://altera-ai.onrender.com/api/v1/admin/organisations \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "Acme Retail", "slug": "acme-retail"}' | python3 -m json.tool
# Expected: 201 with id, name, slug, organisation_type="gms_client"
```

### 11b. Invite a client user

1. On the `/admin` page, click **Invite user** next to the new org.
2. Enter an email (e.g. `client@acme.com`) and role (`client_owner`).
3. Click **Send invite**.
4. Expected response includes `invite_sent: true`.

The invited user receives a Supabase magic link at their email.

Or via API:

```bash
ORG_ID="<uuid-from-step-11a>"
curl -s -X POST "https://altera-ai.onrender.com/api/v1/admin/organisations/${ORG_ID}/invite" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"email": "client@acme.com", "role": "client_owner"}' | python3 -m json.tool
# Expected: 201 with user_id, email, organisation_id, role, invite_sent=true
```

### 11c. Accept invite and set password

1. The invited user clicks the link in their email.
2. The browser navigates to `/auth/callback#type=invite&access_token=...`.
3. The callback page detects `type=invite`, waits for the Supabase session,
   and redirects to `/reset-password`.
4. The user enters a new password (≥ 8 chars) and submits.
5. Expected: redirected to `/projects`.

### 11d. Verify first login

```bash
# After the invited user sets their password, log in and call /me.
NEW_USER_TOKEN="<jwt-for-client-user>"
curl -s https://altera-ai.onrender.com/api/v1/me \
  -H "Authorization: Bearer $NEW_USER_TOKEN" | python3 -m json.tool
# Expected:
# {
#   "user_id": "<uuid>",
#   "email": "client@acme.com",
#   "organisation_id": "<org-uuid>",
#   "role": "client_owner",
#   "organisation_type": "gms_client"
# }
```

### 11e. Forgot password flow

1. Visit `/login` while not logged in.
2. Click **Forgot password?** (visible only when Supabase is configured).
3. Enter the email; click **Send reset link**.
4. Expected: "Reset link sent" confirmation.
5. Click the link in the email → `/auth/callback#type=recovery` → `/reset-password`.
6. Set a new password; expect redirect to `/projects`.

### 11f. Confirm checklist (Phase 32A)

- [ ] `/admin` page loads for `altera_admin` user
- [ ] Client org creation returns 201 and org appears in list
- [ ] Invite endpoint returns `invite_sent: true` and invite email arrives
- [ ] Clicking invite link → `/auth/callback` → `/reset-password`
- [ ] Password set → redirect to `/projects`
- [ ] `GET /api/v1/me` for the new user returns correct org + `client_owner` role
- [ ] Non-admin user receives 403 on all `/api/v1/admin/` endpoints
- [ ] Forgot password email flow works (reset link → `/reset-password`)

---

## 12. Client account management (Phase 32B)

Phase 32B adds member management to the admin page. Verify after deployment.

### 12a. List members

1. Log in as `altera_admin`.
2. Navigate to **Admin** → find a client org → click **Manage members**.
3. Expected: members table appears with email, display name, role, and action buttons.

```bash
ORG_ID="<uuid>"
curl -s "https://altera-ai.onrender.com/api/v1/admin/organisations/${ORG_ID}/members" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
# Expected: array of {user_id, email, display_name, role, organisation_id}
```

### 12b. Resend invite

```bash
USER_ID="<uuid>"
curl -s -X POST \
  "https://altera-ai.onrender.com/api/v1/admin/organisations/${ORG_ID}/members/${USER_ID}/resend-invite" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
# Expected: {user_id, email, organisation_id, invite_sent: true}
# Invited user should receive a password-reset email.
```

### 12c. Change role

```bash
curl -s -X PATCH \
  "https://altera-ai.onrender.com/api/v1/admin/organisations/${ORG_ID}/members/${USER_ID}" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"role": "client_admin"}' | python3 -m json.tool
# Expected: 200 with updated role field.
```

### 12d. Remove member

```bash
curl -s -X DELETE \
  "https://altera-ai.onrender.com/api/v1/admin/organisations/${ORG_ID}/members/${USER_ID}" \
  -H "Authorization: Bearer $TOKEN"
# Expected: 204 No Content.
# Member should no longer appear in GET /members.
```

### 12e. Confirm checklist (Phase 32B)

- [ ] Member list loads in UI for `altera_admin`
- [ ] Role change dropdown saves and reflects new role on reload
- [ ] Resend invite email arrives (recovery link works)
- [ ] Remove member → member disappears from list; auth user preserved
- [ ] `PATCH` with `altera_admin` role returns 400
- [ ] All four endpoints return 403 for non-admin users
- [ ] migration `0028` applied (check Supabase → SQL editor → `\d audit_events`)
