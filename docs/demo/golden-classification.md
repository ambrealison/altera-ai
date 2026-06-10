# Demo golden classification (`DEMO-50produits`)

A **demo-only, flag-gated, deterministic** classification path so the retailer
demo on the `DEMO-50produits` catalogue is perfectly predictable:

- 50/50 Protein Tracker products categorised,
- 50/50 WWF products categorised,
- exactly **two** products surfaced for manual validation,
- no dependency on live LLM variability for this exact catalogue.

It is **off by default** and **only** affects an upload that is recognised as
the exact demo catalogue. Production and every normal upload are unchanged.

> Status: demo aid. Designed to be deleted after the demo with no residue —
> remove `altera_api/demo/` and the two flag-guarded branches that call it.

## How to enable

Set the environment variable (backend / Render):

```
ALTERA_DEMO_GOLDEN_CLASSIFICATION_ENABLED=true
```

With the flag **off** (default) the platform behaves exactly as before.

## What activates it

Three independent gates, **all** required:

1. the flag above is truthy;
2. the classification job methodology is Protein Tracker or WWF;
3. the upload is recognised as the demo catalogue.

### Recognition (no raw CSV committed)

The raw `DEMO-50produits.csv` is **not** committed (it is treated as private
commercial data). Recognition uses a **content fingerprint built from stable
identifiers only**: an upload matches iff it contains *exactly* the 50 demo
`external_product_id`s (`PTWWF001`…`PTWWF050`) **and** every product name
matches the demo catalogue after normalisation (accent-, apostrophe- and
case-insensitive). This is a SHA-256 over the sorted `id=normalised_name`
pairs — effectively a file-free content checksum. Any extra / missing id or a
changed name means "not the demo catalogue", so a real retailer catalogue can
never be mistaken for it.

An optional `ALTERA_DEMO_GOLDEN_SHA256` env var is reserved for callers that
also want to pin the raw file's checksum; the id+name fingerprint is the
primary mechanism and needs no file on disk.

## What it does

For a recognised upload, the orchestrator **skips the AI provider entirely**
and writes pre-approved classifications from
`altera_api/demo/golden_classification.py` (keyed by `external_product_id`):

- **Protein Tracker**: all 50 products → a `ProteinTrackerProductClassification`.
- **WWF**: all 50 products → a `WWFProductClassification`.
- Provenance is **honest**: `source=deterministic`, `confidence=1`,
  `rule_id=demo.golden.pt` / `demo.golden.wwf`. It is **never** stored as
  `source=ai` — we do not fake AI provenance.

### Exactly two products in validation

Both methodologies are active, so flagging both products under both
methodologies would create four review rows. Instead the two validation
items are attached to a **single** methodology (**WWF** — composites are a
first-class WWF Step-1 concept), so the validation experience shows exactly
**two product rows**:

| External id | Product | WWF | Review |
|-------------|---------|-----|--------|
| `PTWWF048`  | Curry de poulet avec riz | composite · meat-based | ✅ on WWF |
| `PTWWF049`  | Pizza fromage tomate | composite · vegetarian | ✅ on WWF |

Both products still receive **both** a PT and a WWF classification (so both
methodologies report 50/50 categorised). The Protein Tracker review queue
stays empty; only WWF carries the two items. Re-running classification clears
any stale review item on the other 48 products.

The review reason is `requested` with an explicit rationale note ("Demo golden
classification — composite/prepared product deliberately routed to human
validation"), so the queue is auditable.

`PTWWF050` (Curry de lentilles végan) is a clearly-vegan composite and is
**auto-accepted** — the demo story is "obvious vegan composite → automatic;
meat & cheese composites → human validation".

## Loading the demo catalogue

1. Enable the flag (above) on the backend.
2. Create a project with **both** Protein Tracker and WWF enabled.
3. Upload `DEMO-50produits.csv` (the CSV must contain both methodologies'
   required columns so all 50 products are eligible for both jobs).
4. Run both classifications from the wizard's "Launch classification"
   buttons. Each completes cleanly (`completed`, never
   `completed_with_errors`); no AI call is made for this catalogue.

## Invariants preserved

- Protein Tracker and WWF stay **strictly separate** (own tables, own
  methodology-scoped review queues; no merged states/counts/calculations).
- **No commercial fields** are sent to AI — in fact no AI call happens for
  the recognised catalogue.
- No production classification behaviour changes for normal uploads.
- No calculation logic changes.

## Where the code lives

- `apps/api/altera_api/demo/golden_classification.py` — fixture + recognition
  + apply helpers.
- `apps/api/altera_api/api/classification_job_orchestrator.py` — one
  flag-guarded branch in `advance_classification_job` (the wizard's path).
- `apps/api/altera_api/api/orchestrator.py` — one flag-guarded branch in
  `classify_upload` (the direct/synchronous path), for parity.
- Tests: `apps/api/tests/demo/test_golden_classification.py`.

To remove the feature after the demo: delete the `demo/` package, the two
branches above, and this doc.
