# Exports

An **export** is a downloadable artefact produced from a run. This
document specifies how exports are generated, stored, and served.

## Triggering an export

A user with `analyst`, `admin`, `owner`, or `viewer` role can request an
export. The request specifies:

- The run id.
- The format (`csv`, `json`, `markdown`).

The API enqueues a job and returns a job id. The frontend polls until
the export is ready and then downloads it.

Exports are not generated synchronously even when small, to keep the
request layer free of long operations.

## Generation

A worker picks up the job and:

1. Reads `calculation_rows` for the run, joined to product and
   classification fields needed by the format.
2. Streams the formatted output to Supabase Storage at
   `organisations/<org_id>/exports/<run_id>/<format>/<file_name>`.
3. Records the export row in an `exports` table with status, format,
   storage path, size, and the requesting user.
4. Writes an `export.generated` audit event.

The worker uses paginated reads with a stable order key
(`calculation_rows.product_id`) so generation memory is bounded.

## Download

The frontend asks the API for a short-lived signed Supabase Storage URL
(15 minutes) and redirects the browser to it. The frontend never sees
or stores the Supabase service role key.

## Retention

Exports are retained for 30 days, then garbage-collected. The export
row is retained for audit even after the file is deleted (the storage
path is preserved for trace purposes).

## Re-exports

A user can re-request the same export. If a recent (under 1 hour)
export of the same `(run_id, format)` exists, the worker returns the
existing artefact instead of regenerating. Older exports always
regenerate to ensure the file matches the current state of any
data that downstream consumers might rely on (though calculation rows
are immutable, the join to product master fields can change if a
product is renamed; the file is regenerated to reflect the latest
display fields, while the underlying numbers remain identical).

## Failures

If a generation job fails:

- The `exports` row is set to `failed` with an error code.
- The audit event is written with the same code.
- The user sees a generic "export failed, please try again" with a
  reference id; engineers can locate the failure by reference id in
  operational logs.

## Future formats

Excel (`.xlsx`) and PDF use the same job mechanism; only the worker
needs additional rendering code. The API contract is unchanged.
