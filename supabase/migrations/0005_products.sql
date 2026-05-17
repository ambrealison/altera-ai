-- 0005_products.sql
--
-- Normalised products + WWF Step-2 composite ingredients. The shared
-- identity block lives in this table; PT-specific and WWF-specific
-- columns sit alongside. Per the schema doc, a PT-only project may
-- leave WWF columns null and vice versa.

create table public.products (
  id                      uuid primary key default gen_random_uuid(),
  upload_id               uuid not null references public.uploads(id) on delete cascade,
  project_id              uuid not null references public.projects(id) on delete cascade,
  organisation_id         uuid not null references public.organisations(id) on delete cascade,
  row_number              integer not null check (row_number >= 1),
  external_product_id     text not null check (length(external_product_id) between 1 and 200),
  product_name            text not null check (length(product_name) between 1 and 400),
  brand                   text,
  is_own_brand            boolean,
  retailer_category       text,
  retailer_subcategory    text,
  ingredients_text        text,
  labels                  text[] not null default '{}',
  language                text check (language is null or language ~ '^[a-z]{2}$'),
  country                 text check (country is null or country ~ '^[A-Z]{2}$'),
  retail_channel          text check (retail_channel is null or retail_channel in ('fresh', 'grocery_ambient', 'frozen')),

  weight_per_item_kg      numeric not null check (weight_per_item_kg > 0 and weight_per_item_kg <= 50),
  items_purchased         numeric check (items_purchased is null or items_purchased >= 0),
  items_sold              numeric check (items_sold is null or items_sold >= 0),

  -- PT-specific
  protein_pct             numeric check (protein_pct is null or (protein_pct >= 0 and protein_pct <= 100)),
  protein_source          text check (protein_source is null or protein_source in ('label', 'reference_db')),
  plant_protein_pct       numeric check (plant_protein_pct is null or (plant_protein_pct >= 0 and plant_protein_pct <= 100)),
  animal_protein_pct      numeric check (animal_protein_pct is null or (animal_protein_pct >= 0 and animal_protein_pct <= 100)),

  created_at              timestamptz not null default now(),

  -- Per-product split: both or neither.
  constraint products_pt_split_paired check (
    (plant_protein_pct is null) = (animal_protein_pct is null)
  ),

  -- A product belongs to one upload and one project in the same org;
  -- a deferred trigger checks the org match because the FKs alone
  -- don't enforce equality between upload.org and project.org.
  unique (upload_id, row_number)
);

create index products_project_idx on public.products (project_id);
create index products_upload_idx on public.products (upload_id);
create index products_org_idx on public.products (organisation_id);
-- Trigram index for product_name search in the manual-review UI.
create index products_name_trgm_idx on public.products using gin (product_name gin_trgm_ops);

comment on table public.products is
  'Normalised products from CSV uploads. Carries shared identity, PT-only, and WWF-only fields.';

create or replace function public.guard_product_org_consistency()
returns trigger
language plpgsql
as $$
declare
  upload_org uuid;
  project_org uuid;
begin
  select organisation_id into upload_org from public.uploads where id = new.upload_id;
  select organisation_id into project_org from public.projects where id = new.project_id;
  if upload_org is null or project_org is null then
    raise exception 'product references missing upload/project';
  end if;
  if upload_org <> new.organisation_id or project_org <> new.organisation_id then
    raise exception 'product organisation_id mismatch (upload=%, project=%, product=%)',
      upload_org, project_org, new.organisation_id;
  end if;
  return new;
end
$$;

create trigger trg_product_org_consistency
before insert or update on public.products
for each row execute function public.guard_product_org_consistency();

-- WWF Step-2 composite ingredients (own-brand only).
create table public.product_composite_ingredients (
  id                              uuid primary key default gen_random_uuid(),
  product_id                      uuid not null references public.products(id) on delete cascade,
  organisation_id                 uuid not null references public.organisations(id) on delete cascade,
  food_group                      text not null check (food_group in ('FG1', 'FG2', 'FG3', 'FG4', 'FG5', 'FG6')),
  subgroup                        text,
  ingredient_weight_kg_per_item   numeric not null check (ingredient_weight_kg_per_item > 0),
  dairy_class                     text check (dairy_class is null or dairy_class in ('cheese', 'other')),
  created_at                      timestamptz not null default now()
);

create index pci_product_idx on public.product_composite_ingredients (product_id);
create index pci_org_idx on public.product_composite_ingredients (organisation_id);
