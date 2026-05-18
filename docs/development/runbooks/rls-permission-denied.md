# Runbook: RLS permission denied

**Severity**: P1 (data access failure or potential data leak)
**Oncall trigger**: API returns unexpected 403/404, or Postgres logs `ERROR: new row violates row-level security policy`

---

## Background

Every multi-tenant table has Row Level Security (RLS) enforced at the Postgres layer. The policies are defined in `supabase/migrations/0011_rls_policies.sql` and `0019_phase16_jobs.sql`. The app uses per-request JWT-scoped Supabase clients so Postgres sees the authenticated user's `auth.uid()` and enforces org isolation automatically.

All tables in scope: `organisations`, `memberships`, `projects`, `uploads`, `products`, `product_composite_ingredients`, `classifications`, `classification_events`, `manual_reviews`, `calculation_runs`, `calculation_rows`, `audit_events`, `report_exports`, `jobs`.

## Symptoms

- API returns 403 where 200 is expected.
- Postgres logs contain: `ERROR: new row violates row-level security policy for table "<table>"`
- A query returns 0 rows when rows should exist.
- An INSERT fails silently (no rows written, no error — this is the Postgres default for RLS INSERT violations unless `USING` is explicit).

## Triage steps

### 1. Reproduce the failure with service-role key

Use the service-role key (bypasses RLS) to confirm the row exists:

```sql
-- Connect with service-role credentials
SELECT * FROM <table> WHERE id = '<id>';
```

If the row exists under service-role but not under the user's JWT, it is an RLS issue.

### 2. Check the user's org membership

```sql
SELECT organisation_id, role
FROM memberships
WHERE user_id = '<user_id>';
```

If the user is not a member of the expected organisation, they will see empty results — not an error.

### 3. Check the JWT claims

Log the `auth.uid()` and `auth.jwt()` values for the failing request by temporarily adding a debug query. The JWT must contain:

- `sub` = the user's UUID (maps to `auth.uid()`)
- `role` = `authenticated`

If the JWT is malformed or expired, Supabase auth middleware returns a 401 before RLS is evaluated.

### 4. Check the RLS policy for the specific operation

```sql
-- Show all policies on a table:
SELECT polname, polcmd, polqual, polwithcheck
FROM pg_policy
WHERE polrelid = 'public.<table>'::regclass;
```

Compare the policy USING / WITH CHECK expressions against the user's membership role.

### 5. Audit events

Check whether the operation emitted an `access_denied` audit event:

```sql
SELECT * FROM audit_events
WHERE event_type = 'access.denied'
  AND actor_user_id = '<user_id>'
ORDER BY created_at DESC
LIMIT 10;
```

## Common causes

| Cause | Fix |
|---|---|
| User not a member of the org | Add membership; check invite flow |
| Role too low (e.g. `viewer` trying to INSERT) | Elevate role or adjust policy if intentional |
| Service-role key used in production client | Never use service-role in client code |
| Migration not applied | Run `supabase db push` / check `supabase migration list` |
| JWT sub does not match `memberships.user_id` | Check Supabase Auth user record |

## Resolution

1. For a legitimate membership issue: add the user to the organisation in Supabase Auth dashboard or via the membership API.
2. For a policy bug: write a failing test in `tests/observability/test_rls_audit.py`, fix the migration, and run `supabase db push` on staging.
3. For a production outage: temporarily allow the operation with a service-role key patch while the policy is fixed — but log this action and revert immediately.

## Prevention

- `tests/observability/test_rls_audit.py` validates that RLS is enabled and at least one policy exists on all required tables. It runs in the standard unit test suite (no live DB required).
- Integration tests in `tests/integration/` validate actual policy behaviour with a real Postgres instance.
- The `test_rls_audit.py` check will catch a new migration that adds a table without enabling RLS.
