-- 0012_audit_immutability.sql
--
-- Append-only enforcement for audit_events and classification_events.
-- The RLS policies in 0011 already omit UPDATE/DELETE for these
-- tables, but a database-level trigger gives belt-and-braces: even a
-- direct service-role write cannot mutate or delete history.

create or replace function public.reject_audit_mutation()
returns trigger
language plpgsql
as $$
begin
  raise exception '% is append-only; UPDATE/DELETE not permitted', tg_table_name
    using errcode = '42501';
end
$$;

create trigger trg_audit_events_no_update
before update on public.audit_events
for each row execute function public.reject_audit_mutation();

create trigger trg_audit_events_no_delete
before delete on public.audit_events
for each row execute function public.reject_audit_mutation();

create trigger trg_classification_events_no_update
before update on public.classification_events
for each row execute function public.reject_audit_mutation();

create trigger trg_classification_events_no_delete
before delete on public.classification_events
for each row execute function public.reject_audit_mutation();
