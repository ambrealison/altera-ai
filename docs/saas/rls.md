# Row-Level Security

RLS is the source of truth for the multi-tenant boundary. Every
multi-tenant table has policies that limit a user to rows in
organisations they belong to. This document describes the policy
patterns, the helper functions, and the test strategy.

## Helper functions

A SQL helper `current_user_organisations()` returns the set of
organisation ids the authenticated user has membership in. It reads
`auth.uid()` from the request context.

Two additional helpers support the managed-SaaS role split:

- `current_user_is_altera()` — true if any of the user's memberships
  belongs to an `organisation_type = 'altera_internal'` org.
- `altera_visible_client_projects()` — for an Altera-internal user,
  returns the set of `gms_client` project ids they are assigned to
  (or all `gms_client` project ids if the user is
  `altera_admin` / `altera_methodology_lead`).


```sql
create or replace function public.current_user_organisations()
returns setof uuid
language sql
stable
security definer
set search_path = public
as $$
  select organisation_id
  from public.memberships
  where user_id = auth.uid()
$$;
```

A helper `user_role_in(org uuid)` returns the user's role in an
organisation, or `null`.

## Policy patterns

### Read-only tables (audit logs, classification events)

```sql
create policy audit_logs_select on audit_logs
for select using (organisation_id in (select current_user_organisations()));
```

No `insert`/`update`/`delete` policies are created for application
roles. Inserts go through the service role used by the worker, scoped
per-request to the calling user's claims.

### Read-write tables (projects, uploads, products, classifications)

```sql
create policy products_select on products
for select using (organisation_id in (select current_user_organisations()));

create policy products_insert on products
for insert with check (
  organisation_id in (select current_user_organisations())
);

create policy products_update on products
for update using (organisation_id in (select current_user_organisations()))
            with check (organisation_id in (select current_user_organisations()));
```

`update` policies always re-check the `with check` clause to prevent a
user from moving a row into another organisation.

### Altera-only tables (manual_reviews, draft report_exports)

Some tables are visible only to Altera-internal users — never to
clients, regardless of project ownership:

```sql
create policy manual_reviews_select on manual_reviews
for select using (
  current_user_is_altera()
  and project_id in (select altera_visible_client_projects())
);

-- Clients see report_exports only when approval_status = 'approved'.
create policy report_exports_client_select on report_exports
for select using (
  organisation_id in (select current_user_organisations())
  and approval_status = 'approved'
);

-- Altera staff see all states of report_exports for their assigned
-- projects.
create policy report_exports_altera_select on report_exports
for select using (
  current_user_is_altera()
  and project_id in (select altera_visible_client_projects())
);
```

The download endpoint also re-checks `approval_status = 'approved'`
in application code as a second line of defence; RLS is the deeper
authority.

### Role-gated mutations

For mutations that require a specific role (e.g. deleting a project
requires `admin` or `owner`), the policy joins to `user_role_in`:

```sql
create policy projects_delete on projects
for delete using (
  organisation_id in (select current_user_organisations())
  and user_role_in(organisation_id) in ('owner', 'admin')
);
```

The route guard in the API still checks roles; RLS is the deeper, more
authoritative layer.

## Storage policies

Supabase Storage objects live under
`organisations/<org_id>/uploads/<upload_id>/<filename>`. The bucket
policy parses the first path segment and confirms membership.

```sql
create policy uploads_storage_select on storage.objects
for select using (
  bucket_id = 'uploads'
  and (split_part(name, '/', 1) = 'organisations')
  and (split_part(name, '/', 2))::uuid in (select current_user_organisations())
);
```

Similar policies cover `insert`, `update`, `delete`.

## Testing RLS

Tests run against a real Postgres instance (the local Supabase) with
two fixture users in different organisations. Each table's policies are
exercised:

- A user can read their own organisation's rows.
- A user **cannot** read another organisation's rows (asserting an
  empty result, not an error).
- A user cannot insert a row claiming a different organisation_id.
- A user cannot update a row to move it to a different organisation.

These tests sit under `supabase/tests/rls/` and run in CI on every PR
that touches `supabase/migrations/`.
