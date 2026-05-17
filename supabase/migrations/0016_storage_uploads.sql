-- 0016_storage_uploads.sql
--
-- Tighten the uploads bucket config that 0010 created without constraints.
-- 0011 already added the RLS policies for storage.objects; no new policies
-- needed here.

update storage.buckets
set
  file_size_limit = 52428800,  -- 50 MB
  allowed_mime_types = array['text/csv', 'application/csv', 'application/octet-stream', 'text/plain']
where id = 'uploads';
