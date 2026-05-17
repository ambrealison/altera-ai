-- 0013_user_profiles.sql
--
-- One profile row per auth.users row. Holds the bits of identity the
-- application needs to render — display name, avatar, cached email —
-- without joining to auth.users in every request. The trigger keeps
-- the profile in sync with auth.users on signup.
--
-- user_profiles itself is NOT tenant-scoped — a user has one profile
-- across all the organisations they belong to. RLS scopes reads to
-- (a) the profile's own user, or (b) users sharing an organisation.

create table public.user_profiles (
  user_id        uuid primary key references auth.users(id) on delete cascade,
  email          citext not null,
  display_name   text not null check (length(display_name) between 1 and 120),
  avatar_url     text,
  locale         text check (locale is null or locale ~ '^[a-z]{2}(-[A-Z]{2})?$'),
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);

create index user_profiles_email_idx on public.user_profiles (email);

comment on table public.user_profiles is
  'One profile per auth.users row. Synced on signup via the trigger below.';

alter table public.user_profiles enable row level security;

-- A user can always read and update their own profile.
create policy user_profiles_select_self on public.user_profiles
  for select using (user_id = auth.uid());

create policy user_profiles_update_self on public.user_profiles
  for update using (user_id = auth.uid()) with check (user_id = auth.uid());

-- A user can also see profiles of users who share at least one
-- organisation with them (so the manual-review UI can show reviewer
-- names, etc.).
create policy user_profiles_select_shared_org on public.user_profiles
  for select using (
    exists (
      select 1
      from public.memberships m_self
      join public.memberships m_other
        on m_self.organisation_id = m_other.organisation_id
      where m_self.user_id = auth.uid()
        and m_other.user_id = user_profiles.user_id
    )
  );

-- Direct INSERT is restricted to the service role; the trigger below
-- auto-creates profiles on signup so users don't need explicit
-- insert rights.
--
-- Trigger: when a row lands in auth.users, mirror its identity into
-- public.user_profiles. The Supabase Auth schema's metadata column
-- carries optional display_name / locale.
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public, auth
as $$
declare
  raw jsonb := coalesce(new.raw_user_meta_data, '{}'::jsonb);
  default_display text := coalesce(
    nullif(raw->>'display_name', ''),
    split_part(new.email, '@', 1)
  );
begin
  insert into public.user_profiles (user_id, email, display_name, locale)
  values (
    new.id,
    coalesce(new.email, '')::citext,
    default_display,
    nullif(raw->>'locale', '')
  )
  on conflict (user_id) do nothing;
  return new;
end
$$;

create trigger trg_handle_new_user
after insert on auth.users
for each row execute function public.handle_new_user();

-- Keep updated_at fresh on update.
create or replace function public.touch_user_profile()
returns trigger
language plpgsql
as $$
begin
  new.updated_at := now();
  return new;
end
$$;

create trigger trg_touch_user_profile
before update on public.user_profiles
for each row execute function public.touch_user_profile();
