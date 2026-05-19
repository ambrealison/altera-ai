-- Phase 33A: CIQUAL 2025 reference table
-- ---------------------------------------------------------------------------
-- Source: Anses. 2025. Ciqual French food composition table.
-- https://ciqual.anses.fr/
--
-- This table stores imported entries from the CIQUAL food composition
-- reference. It is used as a fallback nutrition source when a retailer
-- has not provided protein_pct for a product. Usage must be disclosed
-- in any report that uses CIQUAL-derived values.
--
-- Do NOT commit the raw CIQUAL Excel file to the repository.
-- Run apps/api/scripts/import_ciqual.py locally to populate this table.
-- ---------------------------------------------------------------------------

create table if not exists ciqual_reference (
    id                  uuid        primary key default gen_random_uuid(),
    source              text        not null default 'ciqual',
    source_version      text        not null,
    source_food_code    text        not null,
    food_name_en        text        not null,
    food_group          text        not null,
    food_subgroup       text,
    food_subsubgroup    text,
    protein_g_per_100g  numeric,           -- null = not analysed
    is_below_detection  boolean     not null default false,
    created_at          timestamptz not null default now(),

    unique (source_version, source_food_code)
);

create index if not exists ciqual_reference_food_group_idx
    on ciqual_reference (food_group);

create index if not exists ciqual_reference_food_name_lower_idx
    on ciqual_reference (lower(food_name_en));

-- Full-text search on food name (English)
create index if not exists ciqual_reference_food_name_fts_idx
    on ciqual_reference
    using gin (to_tsvector('english', food_name_en));

-- RLS: Altera internal users only; clients never see reference data directly.
alter table ciqual_reference enable row level security;

create policy "altera_read_ciqual"
    on ciqual_reference for select
    using (public.current_user_is_altera());

create policy "altera_write_ciqual"
    on ciqual_reference for all
    using (public.current_user_is_altera())
    with check (public.current_user_is_altera());
