-- Phase 33G: NEVO 2025 reference table
-- ---------------------------------------------------------------------------
-- Source: RIVM. 2025. NEVO-Online 2025 v9.0.
-- Rijksinstituut voor Volksgezondheid en Milieu, Bilthoven.
-- https://nevo-online.rivm.nl/
--
-- This table stores imported entries from the NEVO food composition
-- reference. Unlike CIQUAL, NEVO publishes PROT (total), PROTPL (plant)
-- and PROTAN (animal) protein per 100 g, which is what makes it the
-- higher-priority fallback for Protein Tracker plant/animal split
-- enrichment.
--
-- Usage rules (must be honoured in any report that uses NEVO values):
--   • NEVO values are reference averages, not retailer-SKU label values.
--   • Use only as a fallback when retailer-provided nutrition is absent.
--   • When PROTPL/PROTAN are blank for an entry, only the total is
--     returned and the split is reported as unavailable.
--   • Never overwrite retailer-provided protein_pct.
--   • Disclose source in any published report (source, version, count).
--
-- Do NOT commit the raw NEVO Excel/CSV file to the repository.
-- Run apps/api/scripts/import_nevo.py locally to populate this table.
-- ---------------------------------------------------------------------------

create table if not exists public.nevo_reference (
    id                          uuid        primary key default gen_random_uuid(),
    source                      text        not null default 'nevo',
    source_version              text        not null,
    nevo_code                   text        not null,
    food_name_nl                text,
    food_name_en                text,
    food_group                  text,
    quantity_basis              text,
    protein_g_per_100g          numeric,
    plant_protein_g_per_100g    numeric,
    animal_protein_g_per_100g   numeric,
    created_at                  timestamptz not null default now(),

    unique (source_version, nevo_code),

    -- Constraint: source is fixed to 'nevo' for this table.
    constraint nevo_reference_source_check
        check (source = 'nevo'),

    -- Constraint: identifiers must not be blank.
    constraint nevo_reference_source_version_not_blank
        check (length(trim(source_version)) > 0),
    constraint nevo_reference_code_not_blank
        check (length(trim(nevo_code)) > 0),

    -- Constraint: protein values may be NULL but must be non-negative
    -- when present. NEVO publishes "0" for absent fractions; "-" or
    -- empty cells become NULL.
    constraint nevo_reference_protein_non_negative
        check (protein_g_per_100g is null or protein_g_per_100g >= 0),
    constraint nevo_reference_plant_protein_non_negative
        check (plant_protein_g_per_100g is null or plant_protein_g_per_100g >= 0),
    constraint nevo_reference_animal_protein_non_negative
        check (animal_protein_g_per_100g is null or animal_protein_g_per_100g >= 0)
);

create index if not exists nevo_reference_source_version_idx
    on public.nevo_reference (source_version);

create index if not exists nevo_reference_food_group_lower_idx
    on public.nevo_reference (lower(food_group));

create index if not exists nevo_reference_food_name_en_lower_idx
    on public.nevo_reference (lower(food_name_en));

create index if not exists nevo_reference_food_name_nl_lower_idx
    on public.nevo_reference (lower(food_name_nl));

-- RLS: Altera internal users only — clients never query reference data
-- directly. The backend's service-role client bypasses RLS for the
-- enrichment lookup path; the JWT-scoped client honours these policies.
alter table public.nevo_reference enable row level security;

drop policy if exists "altera_read_nevo" on public.nevo_reference;
create policy "altera_read_nevo"
    on public.nevo_reference for select
    using (public.current_user_is_altera());

drop policy if exists "altera_write_nevo" on public.nevo_reference;
create policy "altera_write_nevo"
    on public.nevo_reference for all
    using (public.current_user_is_altera())
    with check (public.current_user_is_altera());

comment on table public.nevo_reference is
    'RIVM NEVO 2025 v9.0 food composition reference. Reference averages '
    'used to enrich missing retailer protein values, including plant/animal '
    'split (PROTPL/PROTAN). Not SKU-level data — disclose source in reports.';

comment on column public.nevo_reference.protein_g_per_100g is
    'Total protein (PROT) in grams per 100 g of edible portion.';
comment on column public.nevo_reference.plant_protein_g_per_100g is
    'Plant protein (PROTPL) in grams per 100 g. NULL when not published.';
comment on column public.nevo_reference.animal_protein_g_per_100g is
    'Animal protein (PROTAN) in grams per 100 g. NULL when not published.';
