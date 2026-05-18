# Staging deployment readiness

Follow this checklist top-to-bottom on first staging deployment.
Each section is self-contained; earlier sections must complete before later ones.

Related docs:
- [deployment.md](deployment.md) — env var reference and security checklist
- [ci.md](ci.md) — CI jobs, Node version, remote URL
- [runbooks/bootstrap-first-admin.md](runbooks/bootstrap-first-admin.md) — first admin setup

---

## 0. Pre-deployment gate

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

# Apply all 26 migrations.
supabase db push --project-ref <STAGING_REF>
```

Migrations applied (in order):
```
0001_extensions_and_helpers.sql  →  0026_phase28a3_audit_actions.sql
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
- [ ] All 26 migrations applied (`supabase db push`)
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
   - **Dockerfile path**: `apps/api/Dockerfile` ← relative to repo root
   - **Docker build context**: `apps/api`
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

1. In the Vercel dashboard → **Add New → Project**.
2. Import from GitHub: `ambrealison/altera-ai`.
3. **Root Directory**: `apps/web`
4. Framework: **Next.js** (auto-detected).
5. Build command: leave blank (uses `next build` from `package.json`).
6. Install command: `pnpm install --frozen-lockfile` (or leave blank if Vercel detects pnpm).
7. Output directory: `.next` (auto-detected).
8. Set the three env vars above.
9. Deploy.

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
- [ ] Root directory set to `apps/web`
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

- [ ] CI green (`gh run list --limit 3`)
- [ ] `verify_no_tracked_secrets.sh` passes
- [ ] Supabase project created, region matches Render
- [ ] All 26 migrations applied
- [ ] RLS audit pytest passes
- [ ] `uploads` bucket created and PRIVATE
- [ ] `exports` bucket created and PRIVATE
- [ ] Auth redirect URLs configured in Supabase
- [ ] Backend secrets set in Render (6 `sync: false` vars)
- [ ] `CORS_ALLOWED_ORIGINS` updated with real Vercel URL
- [ ] Render service deployed and health check green
- [ ] `staging_smoke.sh` passes (backend)
- [ ] `ALTERA_DEV_AUTH_ENABLED=false` in Render logs
- [ ] Vercel project connected, root dir `apps/web`
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
