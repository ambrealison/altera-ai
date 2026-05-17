-- 0010_storage_buckets.sql
--
-- Storage buckets for uploads and exports. The bucket-policy SQL in
-- 0011_rls_policies.sql enforces the path-prefix-based access control
-- described in docs/saas/rls.md.

insert into storage.buckets (id, name, public)
values ('uploads', 'uploads', false)
on conflict (id) do nothing;

insert into storage.buckets (id, name, public)
values ('exports', 'exports', false)
on conflict (id) do nothing;
