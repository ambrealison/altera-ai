-- Phase 25B: recommendation lifecycle and persistence
--
-- Stores deterministic recommendations generated from run data, with a
-- full lifecycle (draft → proposed → accepted; dismiss/archive side paths).
-- Altera staff manage the lifecycle; clients see only proposed/accepted rows.

create table if not exists recommendations (
    id                  uuid primary key default gen_random_uuid(),
    organisation_id     uuid not null references organisations(id) on delete cascade,
    project_id          uuid not null references projects(id) on delete cascade,
    run_id              uuid not null,  -- references runs(id) when runs table exists
    methodology         text not null check (methodology in ('protein_tracker', 'wwf')),
    action_type         text not null,
    category            text not null,
    title               text not null,
    description         text not null,
    rationale           text not null,
    expected_direction  text not null,
    priority            text not null check (priority in ('low', 'medium', 'high', 'critical')),
    confidence          text not null check (confidence in ('low', 'medium', 'high')),
    evidence            jsonb not null default '[]',
    caveats             jsonb not null default '[]',
    status              text not null default 'draft'
                            check (status in ('draft', 'proposed', 'accepted', 'dismissed', 'archived')),
    client_facing       boolean not null default true,
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now(),
    created_by          uuid references auth.users(id),
    updated_by          uuid references auth.users(id),

    -- Each (run_id, action_type) pair is unique — upsert key.
    unique (run_id, action_type)
);

-- Indexes for common access patterns
create index if not exists recommendations_run_id_idx
    on recommendations(run_id);

create index if not exists recommendations_project_id_idx
    on recommendations(project_id);

create index if not exists recommendations_organisation_id_idx
    on recommendations(organisation_id);

create index if not exists recommendations_status_idx
    on recommendations(status);

-- Row-Level Security
alter table recommendations enable row level security;

-- Altera internal users see all rows in their organisation
create policy "altera_can_read_all_recommendations"
    on recommendations for select
    using (
        exists (
            select 1 from organisation_members om
            join organisations o on o.id = om.organisation_id
            where om.user_id = auth.uid()
              and om.organisation_id = recommendations.organisation_id
              and o.organisation_type = 'altera_internal'
        )
    );

-- Clients only see proposed/accepted recommendations for their organisation
create policy "clients_see_proposed_and_accepted"
    on recommendations for select
    using (
        status in ('proposed', 'accepted')
        and exists (
            select 1 from organisation_members om
            where om.user_id = auth.uid()
              and om.organisation_id = recommendations.organisation_id
        )
    );

-- Only Altera internal users can insert/update
create policy "altera_can_write_recommendations"
    on recommendations for insert
    with check (
        exists (
            select 1 from organisation_members om
            join organisations o on o.id = om.organisation_id
            where om.user_id = auth.uid()
              and o.organisation_type = 'altera_internal'
        )
    );

create policy "altera_can_update_recommendations"
    on recommendations for update
    using (
        exists (
            select 1 from organisation_members om
            join organisations o on o.id = om.organisation_id
            where om.user_id = auth.uid()
              and o.organisation_type = 'altera_internal'
        )
    );
