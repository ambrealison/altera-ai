-- 003_role_permissions.sql
--
-- Role-gated mutations: only owner/admin can delete projects; only
-- analyst+ can create uploads; reviewers can write to review_queue and
-- classifications but cannot create projects.

begin;
select plan(7);

create extension if not exists pgtap;

-- Fixtures
delete from public.products where organisation_id = '77777777-7777-7777-7777-777777777777';
delete from public.uploads where organisation_id = '77777777-7777-7777-7777-777777777777';
delete from public.projects where organisation_id = '77777777-7777-7777-7777-777777777777';
delete from public.memberships where organisation_id = '77777777-7777-7777-7777-777777777777';
delete from public.organisations where id = '77777777-7777-7777-7777-777777777777';
delete from auth.users where id in (
  '00000000-0000-0000-0000-000000000b01',
  '00000000-0000-0000-0000-000000000b02',
  '00000000-0000-0000-0000-000000000b03',
  '00000000-0000-0000-0000-000000000b04'
);

insert into auth.users (id, email) values
  ('00000000-0000-0000-0000-000000000b01', 'owner@test.local'),
  ('00000000-0000-0000-0000-000000000b02', 'analyst@test.local'),
  ('00000000-0000-0000-0000-000000000b03', 'reviewer@test.local'),
  ('00000000-0000-0000-0000-000000000b04', 'viewer@test.local');

insert into public.organisations (id, name, slug)
  values ('77777777-7777-7777-7777-777777777777', 'Org Roles', 'org-roles');

insert into public.memberships (user_id, organisation_id, role) values
  ('00000000-0000-0000-0000-000000000b01', '77777777-7777-7777-7777-777777777777', 'owner'),
  ('00000000-0000-0000-0000-000000000b02', '77777777-7777-7777-7777-777777777777', 'analyst'),
  ('00000000-0000-0000-0000-000000000b03', '77777777-7777-7777-7777-777777777777', 'reviewer'),
  ('00000000-0000-0000-0000-000000000b04', '77777777-7777-7777-7777-777777777777', 'viewer');

insert into public.projects (id, organisation_id, name, methodologies_enabled, reporting_period_label)
values
  ('eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee',
   '77777777-7777-7777-7777-777777777777',
   'Roles project',
   array['protein_tracker'],
   'FY 2024');

-- ---------------------------------------------------------------------
-- Viewer cannot create projects (analyst+ required).
-- ---------------------------------------------------------------------
set local role authenticated;
select set_config('request.jwt.claim.sub', '00000000-0000-0000-0000-000000000b04', true);

select throws_ok(
  $$insert into public.projects (organisation_id, name, methodologies_enabled, reporting_period_label)
    values ('77777777-7777-7777-7777-777777777777', 'viewer-attempt',
            array['protein_tracker'], 'FY 2024')$$,
  '42501',
  null,
  'Viewer cannot create projects'
);

-- ---------------------------------------------------------------------
-- Viewer cannot upload (analyst+ required).
-- ---------------------------------------------------------------------
select throws_ok(
  $$insert into public.uploads (organisation_id, project_id, storage_path, original_filename, status)
    values ('77777777-7777-7777-7777-777777777777',
            'eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee',
            'organisations/77777777-7777-7777-7777-777777777777/uploads/x.csv',
            'x.csv',
            'pending')$$,
  '42501',
  null,
  'Viewer cannot create uploads'
);

-- ---------------------------------------------------------------------
-- Analyst can create uploads.
-- ---------------------------------------------------------------------
select set_config('request.jwt.claim.sub', '00000000-0000-0000-0000-000000000b02', true);
select lives_ok(
  $$insert into public.uploads (organisation_id, project_id, storage_path, original_filename, status)
    values ('77777777-7777-7777-7777-777777777777',
            'eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee',
            'organisations/77777777-7777-7777-7777-777777777777/uploads/y.csv',
            'y.csv',
            'pending')$$,
  'Analyst can create uploads'
);

-- ---------------------------------------------------------------------
-- Reviewer cannot delete projects (owner/admin only).
-- ---------------------------------------------------------------------
select set_config('request.jwt.claim.sub', '00000000-0000-0000-0000-000000000b03', true);
select is(
  (with d as (
     delete from public.projects
     where id = 'eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee' returning 1
   ) select count(*)::int from d),
  0,
  'Reviewer cannot delete projects (RLS denies)'
);

-- ---------------------------------------------------------------------
-- Owner can delete projects.
-- ---------------------------------------------------------------------
select set_config('request.jwt.claim.sub', '00000000-0000-0000-0000-000000000b01', true);
-- First drop the upload (FK cascade would do this, but we test the
-- two-step explicit-delete path that admins typically use).
delete from public.uploads where project_id = 'eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee';
select is(
  (with d as (
     delete from public.projects
     where id = 'eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee' returning 1
   ) select count(*)::int from d),
  1,
  'Owner can delete projects'
);

-- ---------------------------------------------------------------------
-- Viewer cannot see audit_events (analyst+).
-- ---------------------------------------------------------------------
insert into public.audit_events (id, organisation_id, action, metadata)
values (
  'ffffffff-ffff-ffff-ffff-ffffffffffff',
  '77777777-7777-7777-7777-777777777777',
  'organisation.created',
  '{}'::jsonb
);

select set_config('request.jwt.claim.sub', '00000000-0000-0000-0000-000000000b04', true);
select is(
  (select count(*)::int from public.audit_events
   where organisation_id = '77777777-7777-7777-7777-777777777777'),
  0,
  'Viewer cannot read audit_events'
);

-- Analyst can.
select set_config('request.jwt.claim.sub', '00000000-0000-0000-0000-000000000b02', true);
select cmp_ok(
  (select count(*)::int from public.audit_events
   where organisation_id = '77777777-7777-7777-7777-777777777777'),
  '>=', 1,
  'Analyst can read audit_events'
);

select finish();
rollback;
