# @altera-ai/web

Next.js + TypeScript + Tailwind frontend for Altera AI.

## Status

Phase 13 complete (13A–13D). Supabase Auth + SSR session refresh + Storage upload flow.

The shell carries the full pipeline:

- **/login** Email + password sign-in (renders a "Supabase not
  configured" banner when env vars are missing).
- **/** Dashboard with project / review / run counts.
- **/projects** List of projects + inline create form.
- **/projects/[id]** Project overview (uploads, review, runs).
- **/projects/[id]/upload** CSV upload — two-step Storage flow when
  Supabase is configured; multipart fallback for dev mode.
- **/projects/[id]/review** Manual review queue with accept / change / defer.
- **/projects/[id]/runs** Trigger calculation + list of past runs.
- **/projects/[id]/runs/[runId]** PT or WWF summary + CSV/JSON/Markdown downloads.

Every protected page renders inside `<AuthGate>` which checks the
session via `useAuth()`; unauthenticated users are redirected to
`/login?next=<path>`. API calls go through `createApi(accessToken)`
which attaches `Authorization: Bearer <token>` on every request.

## Auth setup

### Local development with Supabase

```bash
# In one terminal: start the local Supabase stack.
supabase start

# In apps/web/.env.local:
NEXT_PUBLIC_SUPABASE_URL=http://127.0.0.1:54321
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=<anon key from supabase status>
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

`NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` is the new Supabase name for the
anon/public key. The legacy `NEXT_PUBLIC_SUPABASE_ANON_KEY` is still
accepted as a fallback.

Then `pnpm --filter @altera-ai/web dev` and open
http://localhost:3000. Sign in at `/login` using a Supabase Auth user
(`supabase auth users create ...`).

### Local development without Supabase (dev fallback)

If the backend is running with `ALTERA_DEV_AUTH_ENABLED=true` and the
Supabase env vars are blank, the frontend still works — the `/login`
page shows a banner explaining the situation and every API call goes
through the dev-auth path on the backend.

```bash
# apps/web/.env.local:
# leave NEXT_PUBLIC_SUPABASE_* blank
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

In dev-auth mode the "Sign out" button in the user menu is hidden
(there's no session to invalidate).

### Production

`NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`
must be set at build time. They are baked into the bundle and are
**public values** — the service role key (`SUPABASE_SERVICE_ROLE_KEY`)
never goes here. The backend must run with `ALTERA_DEV_AUTH_ENABLED`
unset or `false`.

## Upload flow (13D)

When Supabase is configured (`isSupabaseConfigured()` returns true):

1. Frontend calls `POST /api/v1/projects/{id}/uploads/prepare` to get
   a signed upload URL and an upload ID.
2. Browser PUTs the file directly to Supabase Storage.
3. Frontend calls `POST /api/v1/projects/{id}/uploads/{id}/ingest` to
   trigger ingestion from storage.

When Supabase is not configured, the upload page falls back to posting
multipart directly to the API (`POST /uploads`).

## Session refresh (13D)

`middleware.ts` at the project root runs on every request to refresh
the Supabase session cookie via `@supabase/ssr`.

## Setup

```bash
pnpm install
```

## Run dev server

```bash
pnpm --filter @altera-ai/web dev
```

Then open http://localhost:3000.

## Lint / typecheck

```bash
pnpm --filter @altera-ai/web lint
pnpm --filter @altera-ai/web typecheck
```

## Vercel deployment

This is a pnpm workspace, so Vercel must run install from the repo root
to see `pnpm-lock.yaml` and `pnpm-workspace.yaml`. A `vercel.json` at the
repo root pins the install/build commands; Vercel only needs the Root
Directory pointed at the repo root.

Because Vercel's framework detection scans the Root Directory's
`package.json` for `next`, the root `package.json` declares
`next` as a devDependency mirroring `apps/web/package.json`. pnpm
deduplicates the install across the workspace — there is no extra cost
beyond the framework hint Vercel needs.

1. Connect the repository in the Vercel dashboard.
2. Project settings:
   - **Root Directory**: `.` (repo root — leave blank or set to `./`).
   - **Framework Preset**: Next.js (set automatically by `vercel.json`).
   - **Node.js Version**: 22.x (matches CI; required by `pnpm@11.1.2`).
   - **Install Command**, **Build Command**, **Output Directory**:
     leave the dashboard fields blank — they are pinned in `vercel.json`:

     ```json
     {
       "installCommand": "corepack enable && pnpm install --frozen-lockfile",
       "buildCommand": "pnpm --filter @altera-ai/web build",
       "outputDirectory": "apps/web/.next"
     }
     ```
3. Set environment variables in the Vercel project settings:

   | Variable | Description |
   |---|---|
   | `NEXT_PUBLIC_API_BASE_URL` | Deployed backend URL |
   | `NEXT_PUBLIC_SUPABASE_URL` | Supabase project URL |
   | `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` | Supabase anon/publishable key |

   Do **not** add `SUPABASE_SERVICE_ROLE_KEY` or any backend-only secrets
   here — they would be baked into the client bundle.

5. Add Auth redirect URLs in the Supabase dashboard → Authentication → URL Configuration:

   ```
   https://<vercel-deployment-url>/auth/callback
   https://staging.altera-ai.com/auth/callback
   ```
