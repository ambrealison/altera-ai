-- 0007_manual_reviews.sql
--
-- Manual-review queue: one row per (product, methodology) awaiting (or
-- being worked on by) a human reviewer. The 15-minute soft lock is
-- enforced by CHECK constraints; lock expiry is query-based (no
-- background timers).

create table public.manual_reviews (
  product_id           uuid not null references public.products(id) on delete cascade,
  methodology          text not null check (methodology in ('protein_tracker', 'wwf')),
  organisation_id      uuid not null references public.organisations(id) on delete cascade,
  status               text not null default 'in_queue'
                       check (status in ('in_queue', 'reviewing', 'accepted', 'changed', 'deferred')),
  reason               text not null
                       check (reason in ('low_confidence', 'ai_parse_failed', 'rule_collision', 'requested')),
  soft_lock_user_id    uuid references auth.users(id),
  soft_lock_expires_at timestamptz,
  queued_at            timestamptz not null default now(),

  primary key (product_id, methodology),

  constraint manual_reviews_lock_paired check (
    (soft_lock_user_id is null) = (soft_lock_expires_at is null)
  ),
  constraint manual_reviews_lock_only_when_reviewing check (
    status = 'reviewing' or soft_lock_user_id is null
  )
);

create index manual_reviews_org_idx on public.manual_reviews (organisation_id);
create index manual_reviews_status_idx on public.manual_reviews (status);

comment on table public.manual_reviews is
  'Products awaiting manual review. One row per (product, methodology). Carries the 15-minute soft-lock.';
