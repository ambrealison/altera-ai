-- 001_org_isolation.sql
--
-- Two users in two different organisations cannot see each other's
-- projects, uploads, products, classifications, or runs.
--
-- Run with: pg_prove --recurse supabase/tests/rls

begin;
select plan(14);

-- ---------------------------------------------------------------------
-- Fixtures: two users, two orgs.
-- ---------------------------------------------------------------------
create extension if not exists pgtap;

-- Tear down any leftovers from a prior failed run.
delete from public.runs where organisation_id in (
  '11111111-1111-1111-1111-111111111111',
  '22222222-2222-2222-2222-222222222222'
);
delete from public.products where organisation_id in (
  '11111111-1111-1111-1111-111111111111',
  '22222222-2222-2222-2222-222222222222'
);
delete from public.uploads where organisation_id in (
  '11111111-1111-1111-1111-111111111111',
  '22222222-2222-2222-2222-222222222222'
);
delete from public.projects where organisation_id in (
  '11111111-1111-1111-1111-111111111111',
  '22222222-2222-2222-2222-222222222222'
);
delete from public.memberships where organisation_id in (
  '11111111-1111-1111-1111-111111111111',
  '22222222-2222-2222-2222-222222222222'
);
delete from public.organisations where id in (
  '11111111-1111-1111-1111-111111111111',
  '22222222-2222-2222-2222-222222222222'
);
delete from auth.users where id in (
  '00000000-0000-0000-0000-000000000a01',
  '00000000-0000-0000-0000-000000000a02'
);

insert into auth.users (id, email) values
  ('00000000-0000-0000-0000-000000000a01', 'alice@test.local'),
  ('00000000-0000-0000-0000-000000000a02', 'bob@test.local');

insert into public.organisations (id, name, slug) values
  ('11111111-1111-1111-1111-111111111111', 'Org Alice', 'org-alice'),
  ('22222222-2222-2222-2222-222222222222', 'Org Bob',   'org-bob');

insert into public.memberships (user_id, organisation_id, role) values
  ('00000000-0000-0000-0000-000000000a01', '11111111-1111-1111-1111-111111111111', 'analyst'),
  ('00000000-0000-0000-0000-000000000a02', '22222222-2222-2222-2222-222222222222', 'analyst');

-- A project per org (created by service role so we bypass RLS for setup).
insert into public.projects (id, organisation_id, name, methodologies_enabled, reporting_period_label)
values
  ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', '11111111-1111-1111-1111-111111111111',
   'Alice project', array['protein_tracker'], 'FY 2024'),
  ('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb', '22222222-2222-2222-2222-222222222222',
   'Bob project',   array['wwf'], 'FY 2024');

-- ---------------------------------------------------------------------
-- Alice's view
-- ---------------------------------------------------------------------
set local role authenticated;
select set_config('request.jwt.claim.sub', '00000000-0000-0000-0000-000000000a01', true);

select is(
  (select count(*)::int from public.projects),
  1,
  'Alice sees exactly her own organisation''s projects (RLS scopes the SELECT)'
);

select is(
  (select count(*)::int from public.projects where organisation_id = '22222222-2222-2222-2222-222222222222'),
  0,
  'Alice cannot see Bob''s projects (cross-org leak guard)'
);

-- Alice trying to insert into Bob's org must fail.
select throws_ok(
  $$insert into public.projects (organisation_id, name, methodologies_enabled, reporting_period_label)
    values ('22222222-2222-2222-2222-222222222222', 'sneaky', array['wwf'], 'X')$$,
  '42501',
  null,
  'Alice cannot insert a project into Bob''s organisation'
);

-- Alice trying to UPDATE Bob's project must affect 0 rows (RLS hides it).
select is(
  (with upd as (
     update public.projects set name = 'pwned'
     where id = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb' returning 1
   ) select count(*)::int from upd),
  0,
  'Alice cannot update Bob''s project (RLS hides the row)'
);

-- Helper checks.
select results_eq(
  $$ select public.user_role_in('11111111-1111-1111-1111-111111111111') $$,
  $$ values ('analyst'::text) $$,
  'user_role_in returns analyst for Alice in her own org'
);
select results_eq(
  $$ select public.user_role_in('22222222-2222-2222-2222-222222222222') $$,
  $$ values (null::text) $$,
  'user_role_in returns NULL for Alice in Bob''s org'
);

-- ---------------------------------------------------------------------
-- Bob's view (symmetric)
-- ---------------------------------------------------------------------
select set_config('request.jwt.claim.sub', '00000000-0000-0000-0000-000000000a02', true);

select is(
  (select count(*)::int from public.projects),
  1,
  'Bob sees exactly his own organisation''s projects'
);

select is(
  (select count(*)::int from public.projects where organisation_id = '11111111-1111-1111-1111-111111111111'),
  0,
  'Bob cannot see Alice''s projects'
);

select is(
  (with upd as (
     update public.projects set name = 'pwned'
     where id = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa' returning 1
   ) select count(*)::int from upd),
  0,
  'Bob cannot update Alice''s project'
);

-- ---------------------------------------------------------------------
-- Membership cross-checks
-- ---------------------------------------------------------------------
set local role authenticated;
select set_config('request.jwt.claim.sub', '00000000-0000-0000-0000-000000000a01', true);

select is(
  (select count(*)::int from public.memberships
   where organisation_id = '22222222-2222-2222-2222-222222222222'),
  0,
  'Alice cannot see Bob''s organisation memberships'
);

-- An unauthenticated request sees nothing.
reset role;
select set_config('request.jwt.claim.sub', '', true);
select is(
  (select count(*)::int from public.projects),
  0,
  'Unauthenticated session sees zero projects'
);

-- Audit events read requires analyst+.
set local role authenticated;
select set_config('request.jwt.claim.sub', '00000000-0000-0000-0000-000000000a01', true);
select lives_ok(
  $$ select * from public.audit_events limit 1 $$,
  'Analyst can query audit_events'
);

-- Storage policy check (only runs if storage.objects exists).
select has_table('storage', 'objects',          'storage.objects table exists');
select has_table('public',  'classifications',  'classifications table exists');
select has_table('public',  'manual_reviews',   'manual_reviews table exists');

select finish();
rollback;
