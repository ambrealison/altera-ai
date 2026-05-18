-- Phase 26A: scenario modelling foundation
--
-- Scenarios are deterministic, read-only projections against a base
-- calculation run. Only Protein Tracker is supported in Phase 26A.
-- WWF scenario modelling is deferred to a future phase.
--
-- Three tables:
--   scenarios             — header / metadata
--   scenario_operations   — ordered list of transformation steps
--   scenario_results      — latest projection output (upserted on /run)

create table if not exists scenarios (
    id                  uuid primary key default gen_random_uuid(),
    organisation_id     uuid not null references organisations(id) on delete cascade,
    project_id          uuid not null references projects(id) on delete cascade,
    base_run_id         uuid not null,  -- references runs(id) when runs table exists
    name                text not null check (char_length(name) between 1 and 200),
    description         text not null default '',
    status              text not null default 'draft'
                            check (status in ('draft', 'active', 'archived')),
    methodology         text not null default 'protein_tracker'
                            check (methodology = 'protein_tracker'),  -- Phase 26A: PT only
    created_by          uuid references auth.users(id),
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now()
);

create index if not exists scenarios_project_id_idx
    on scenarios(project_id);

create index if not exists scenarios_organisation_id_idx
    on scenarios(organisation_id);


create table if not exists scenario_operations (
    id              uuid primary key default gen_random_uuid(),
    scenario_id     uuid not null references scenarios(id) on delete cascade,
    operation_type  text not null check (operation_type in (
                        'shift_protein_between_groups',
                        'increase_plant_core_protein',
                        'reduce_animal_core_protein',
                        'improve_composite_split'
                    )),
    parameters      jsonb not null default '{}',
    rationale       text not null default '',
    "order"         integer not null default 0,
    created_at      timestamptz not null default now()
);

create index if not exists scenario_operations_scenario_id_idx
    on scenario_operations(scenario_id);


create table if not exists scenario_results (
    scenario_id     uuid primary key references scenarios(id) on delete cascade,
    base_run_id     uuid not null,
    methodology     text not null,
    result_payload  jsonb not null,   -- serialised ScenarioResult
    created_at      timestamptz not null default now()
);


-- Row-Level Security
alter table scenarios enable row level security;
alter table scenario_operations enable row level security;
alter table scenario_results enable row level security;

-- Altera internal users can read/write all scenarios in any organisation.
create policy "altera_full_access_scenarios"
    on scenarios for all
    using (
        exists (
            select 1 from organisation_members om
            join organisations o on o.id = om.organisation_id
            where om.user_id = auth.uid()
              and o.organisation_type = 'altera_internal'
        )
    )
    with check (
        exists (
            select 1 from organisation_members om
            join organisations o on o.id = om.organisation_id
            where om.user_id = auth.uid()
              and o.organisation_type = 'altera_internal'
        )
    );

-- Clients can only read active scenarios for their own organisation.
create policy "clients_see_active_scenarios"
    on scenarios for select
    using (
        status = 'active'
        and exists (
            select 1 from organisation_members om
            where om.user_id = auth.uid()
              and om.organisation_id = scenarios.organisation_id
        )
    );

-- Operations and results inherit from parent scenario via Altera policy.
create policy "altera_full_access_scenario_operations"
    on scenario_operations for all
    using (
        exists (
            select 1 from scenarios s
            join organisation_members om on om.organisation_id = s.organisation_id
            join organisations o on o.id = om.organisation_id
            where s.id = scenario_operations.scenario_id
              and om.user_id = auth.uid()
              and o.organisation_type = 'altera_internal'
        )
    )
    with check (
        exists (
            select 1 from scenarios s
            join organisation_members om on om.organisation_id = s.organisation_id
            join organisations o on o.id = om.organisation_id
            where s.id = scenario_operations.scenario_id
              and om.user_id = auth.uid()
              and o.organisation_type = 'altera_internal'
        )
    );

create policy "altera_full_access_scenario_results"
    on scenario_results for all
    using (
        exists (
            select 1 from scenarios s
            join organisation_members om on om.organisation_id = s.organisation_id
            join organisations o on o.id = om.organisation_id
            where s.id = scenario_results.scenario_id
              and om.user_id = auth.uid()
              and o.organisation_type = 'altera_internal'
        )
    )
    with check (
        exists (
            select 1 from scenarios s
            join organisation_members om on om.organisation_id = s.organisation_id
            join organisations o on o.id = om.organisation_id
            where s.id = scenario_results.scenario_id
              and om.user_id = auth.uid()
              and o.organisation_type = 'altera_internal'
        )
    );

create policy "clients_see_active_scenario_results"
    on scenario_results for select
    using (
        exists (
            select 1 from scenarios s
            join organisation_members om on om.organisation_id = s.organisation_id
            where s.id = scenario_results.scenario_id
              and s.status = 'active'
              and om.user_id = auth.uid()
        )
    );
