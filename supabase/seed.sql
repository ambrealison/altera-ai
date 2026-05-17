-- supabase/seed.sql
--
-- Local-dev seed. Inserts:
--   * the canonical methodology / taxonomy / rules versions Altera AI
--     ships with — required by every project,
--   * a demo organisation,
--   * a demo user profile + owner membership (only when the
--     corresponding auth.users row exists; create it via
--     `supabase auth users create demo@altera-ai.local --password demo-password`).
--
-- Run `supabase db reset` to apply. The script is idempotent.

-- ---------------------------------------------------------------------
-- Version registries (global; not tenant-scoped)
-- ---------------------------------------------------------------------
insert into public.methodology_versions
  (id, methodology, version, source_edition, source_citation, year)
values
  ('00000000-0000-0000-0000-00000000aa01',
   'protein_tracker', '1.0.0',
   'GPA & ProVeg Foodservice 2024-08',
   'The Protein Tracker — Foodservice, Green Protein Alliance & ProVeg, Aug 2024',
   2024),
  ('00000000-0000-0000-0000-00000000aa02',
   'wwf', '1.0.0',
   'WWF Food Practice 2024',
   'Achieving a Planet-Based Diet, WWF Food Practice (Meyer et al.), 2024',
   2024)
on conflict (methodology, version) do nothing;

insert into public.taxonomy_versions (id, version)
values ('00000000-0000-0000-0000-00000000bb01', '1.0.0')
on conflict (version) do nothing;

insert into public.rules_versions (id, version)
values ('00000000-0000-0000-0000-00000000cc01', '0.1.0')
on conflict (version) do nothing;

-- ---------------------------------------------------------------------
-- Demo tenant + user (only when the auth.users row exists)
-- ---------------------------------------------------------------------
do $$
declare
  demo_org_id  uuid := '00000000-0000-0000-0000-0000000000a0';
  demo_user_id uuid := '00000000-0000-0000-0000-0000000000a1';
begin
  insert into public.organisations (id, name, slug)
  values (demo_org_id, 'Demo Organisation', 'demo')
  on conflict (id) do nothing;

  if exists (select 1 from auth.users where id = demo_user_id) then
    -- handle_new_user() will have created the profile already, but we
    -- upsert defensively so the seed is fully self-contained.
    insert into public.user_profiles (user_id, email, display_name)
    values (demo_user_id, 'demo@altera-ai.local', 'Demo User')
    on conflict (user_id) do nothing;

    insert into public.memberships (user_id, organisation_id, role)
    values (demo_user_id, demo_org_id, 'owner')
    on conflict (user_id, organisation_id) do nothing;
  end if;
end
$$;
