-- 002_audit_immutability.sql
--
-- audit_events and classification_events must reject UPDATE and DELETE,
-- regardless of role. RLS hides the row from non-members; the trigger
-- in 0012_audit_immutability.sql ensures even the service role cannot
-- mutate history.

begin;
select plan(6);

create extension if not exists pgtap;

-- Fixture org + audit row.
delete from public.audit_events where organisation_id = '33333333-3333-3333-3333-333333333333';
delete from public.classification_events where organisation_id = '33333333-3333-3333-3333-333333333333';
delete from public.organisations where id = '33333333-3333-3333-3333-333333333333';
insert into public.organisations (id, name, slug)
  values ('33333333-3333-3333-3333-333333333333', 'Org Audit', 'org-audit');

insert into public.audit_events (id, organisation_id, action, metadata)
values (
  'cccccccc-cccc-cccc-cccc-cccccccccccc',
  '33333333-3333-3333-3333-333333333333',
  'organisation.created',
  '{}'::jsonb
);

-- UPDATE rejected
select throws_ok(
  $$update public.audit_events set action = 'auth.signed_in'
    where id = 'cccccccc-cccc-cccc-cccc-cccccccccccc'$$,
  '42501',
  null,
  'audit_events rejects UPDATE'
);

-- DELETE rejected
select throws_ok(
  $$delete from public.audit_events
    where id = 'cccccccc-cccc-cccc-cccc-cccccccccccc'$$,
  '42501',
  null,
  'audit_events rejects DELETE'
);

-- INSERT works (audit logs are append-only, not write-protected).
select lives_ok(
  $$insert into public.audit_events (organisation_id, action, metadata)
    values ('33333333-3333-3333-3333-333333333333', 'auth.signed_in', '{}'::jsonb)$$,
  'audit_events accepts INSERT'
);

-- Same shape for classification_events. Skip if no product exists.
do $$
declare
  upload_id uuid := '44444444-4444-4444-4444-444444444444';
  project_id uuid := '55555555-5555-5555-5555-555555555555';
  product_id uuid := '66666666-6666-6666-6666-666666666666';
begin
  insert into public.projects (id, organisation_id, name, methodologies_enabled, reporting_period_label)
    values (project_id, '33333333-3333-3333-3333-333333333333',
            'audit', array['protein_tracker'], 'FY 2024')
    on conflict (id) do nothing;
  insert into public.uploads (id, organisation_id, project_id, storage_path, original_filename, status, row_count)
    values (upload_id, '33333333-3333-3333-3333-333333333333', project_id,
            'x', 'x.csv', 'valid', 0)
    on conflict (id) do nothing;
  insert into public.products (id, upload_id, project_id, organisation_id, row_number,
                                external_product_id, product_name, weight_per_item_kg)
    values (product_id, upload_id, project_id, '33333333-3333-3333-3333-333333333333',
            1, 'P-A1', 'audit fixture', 0.4)
    on conflict (id) do nothing;
end
$$;

insert into public.classification_events
  (id, product_id, methodology, organisation_id, from_category, to_category, source, confidence)
values
  ('dddddddd-dddd-dddd-dddd-dddddddddddd',
   '66666666-6666-6666-6666-666666666666', 'protein_tracker',
   '33333333-3333-3333-3333-333333333333', null, 'plant_based_core',
   'deterministic', 1);

select throws_ok(
  $$update public.classification_events set to_category = 'animal_core'
    where id = 'dddddddd-dddd-dddd-dddd-dddddddddddd'$$,
  '42501',
  null,
  'classification_events rejects UPDATE'
);

select throws_ok(
  $$delete from public.classification_events
    where id = 'dddddddd-dddd-dddd-dddd-dddddddddddd'$$,
  '42501',
  null,
  'classification_events rejects DELETE'
);

select pass('audit immutability triggers wired');

select finish();
rollback;
