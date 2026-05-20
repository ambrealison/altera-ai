-- Phase 33F — uploads DELETE RLS policy.
--
-- An upload can be deleted by any user who can write data in the upload's
-- organisation (same gate as INSERT/UPDATE). The cascade on
-- products.upload_id and downstream FKs handles cleanup of products,
-- classifications, manual reviews, enrichment records and audit events.
--
-- Calculation runs (calculation_runs) do not reference uploads, so
-- historical run records survive the upload deletion.

drop policy if exists uploads_delete on public.uploads;
create policy uploads_delete on public.uploads
  for delete
  using (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_can_write_org_data(organisation_id)
  );
