-- Phase 28A: persistence parity
--
-- Creates tables for review decisions (Phase 19C) and nutrition
-- enrichment records (Phase 23A) which were previously only held in
-- InMemoryStore.  No schema changes to existing tables.

-- ---------------------------------------------------------------------------
-- review_decisions (Phase 19C)
-- ---------------------------------------------------------------------------
-- Each manual review action (accept, defer, change, bulk-accept, etc.) writes
-- an immutable row here.  Used for audit and analytics; never mutated.

create table if not exists review_decisions (
    id                  uuid        primary key default gen_random_uuid(),
    product_id          uuid        not null references products(id) on delete cascade,
    methodology         text        not null check (methodology in ('protein_tracker', 'wwf')),
    decision            text        not null,
    reviewer_user_id    uuid        not null,
    from_category       text,
    to_category         text,
    reason              text,
    created_at          timestamptz not null default now()
);

create index if not exists review_decisions_product_id_idx
    on review_decisions(product_id);

create index if not exists review_decisions_reviewer_idx
    on review_decisions(reviewer_user_id);

alter table review_decisions enable row level security;

-- Altera internal: full access
create policy "altera_full_access_review_decisions"
    on review_decisions for all
    using (
        exists (
            select 1 from user_profiles
            where user_id = auth.uid()
              and organisation_type = 'altera_internal'
        )
    )
    with check (
        exists (
            select 1 from user_profiles
            where user_id = auth.uid()
              and organisation_type = 'altera_internal'
        )
    );

-- ---------------------------------------------------------------------------
-- nutrition_enrichment_records (Phase 23A)
-- ---------------------------------------------------------------------------
-- One row per enrichment observation (nutrient × product × source).
-- Multiple records may exist per product (manual + category average).

create table if not exists nutrition_enrichment_records (
    id              uuid        primary key default gen_random_uuid(),
    product_id      uuid        not null references products(id) on delete cascade,
    nutrient        text        not null,
    original_value  numeric,
    enriched_value  numeric,
    unit            text        not null,
    source          text        not null,
    confidence      numeric     check (confidence between 0 and 1),
    status          text        not null,
    rationale       text        not null default '',
    created_at      timestamptz not null default now(),
    created_by      uuid
);

create index if not exists nutrition_enrichment_records_product_id_idx
    on nutrition_enrichment_records(product_id);

alter table nutrition_enrichment_records enable row level security;

-- Altera internal: full access
create policy "altera_full_access_enrichment"
    on nutrition_enrichment_records for all
    using (
        exists (
            select 1 from user_profiles
            where user_id = auth.uid()
              and organisation_type = 'altera_internal'
        )
    )
    with check (
        exists (
            select 1 from user_profiles
            where user_id = auth.uid()
              and organisation_type = 'altera_internal'
        )
    );
