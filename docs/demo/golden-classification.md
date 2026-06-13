# Demo golden classification

A **demo-only, recognition-gated, deterministic** classification path so the
retailer demo on a recognised demo catalogue is perfectly predictable: every
product categorised by both methodologies, exactly **two** products surfaced
for manual validation, and no dependency on live LLM variability. It defaults
**ON** and is gated by catalogue recognition; the env flag is only an emergency
kill switch (see below).

It is gated by **strict catalogue recognition** and **only** affects an upload
recognised as an exact demo catalogue. Production and every normal upload are
unchanged.

> Status: demo aid. Designed to be deleted after the demo with no residue —
> remove `apps/api/altera_api/demo/` and the flag-guarded branches that call
> it.

## How it activates (no config needed)

The demo path now defaults **ON** and is gated by recognition alone — there is
**nothing to set** for the demo to work. It activates only when an upload's
external ids are exactly a demo catalogue's (`PTWWF001..`, unique to the demo;
no real retailer catalogue uses them).

`ALTERA_DEMO_GOLDEN_CLASSIFICATION_ENABLED` is retained only as an **emergency
kill switch**: set it to `false` / `0` / `no` / `off` to force the demo path
off everywhere. Any other value (or unset) leaves it ON.

A cheap pre-filter (upload product count must be a demo size, e.g. 25 or 50)
means normal-sized production uploads never even run the recognition check.

## Recognised catalogues

Two catalogues are recognised (`apps/api/altera_api/demo/golden_classification.py`):

| Key | File | Products | Review products | Review methodologies |
|-----|------|---------:|-----------------|----------------------|
| `demo25` | `DEMO.csv` | 25 | `PTWWF019` Ratatouille de légumes, `PTWWF025` Pizza fromage tomate vegan | **Protein Tracker + WWF** (same products) |
| `demo50` | `DEMO-50produits.csv` | 50 | `PTWWF048` Curry de poulet avec riz, `PTWWF049` Pizza fromage tomate | WWF only |

`demo25` is the **current live demo file**. In `demo25` the pizza is
`Pizza fromage tomate vegan` — an all-plant dish, so it is **PT
`plant_based_non_core`** (not a PT composite); on the WWF side it maps to its
dominant component, the wheat base, i.e. **WWF `FG5` (grains/cereals)** — one
of the 7 Planet-Based-Diets food groups ("Composite" is not a WWF food group).
Both catalogues reuse the `PTWWF0xx` id scheme but map the ids to *different*
products, so recognition matches on
**either** a full-catalogue fingerprint (id set **and** names) **or** the exact
demo-only id set — never on ids a real catalogue could share.

### Recognition (no raw CSV committed)

The raw CSVs are **not** committed (treated as private commercial data).
Recognition uses **stable identifiers only**: an upload matches a catalogue iff
**either** its `(external_product_id, product_name)` pairs produce that
catalogue's SHA-256 fingerprint (exactly the same id set with matching names,
normalised: accent-, apostrophe- and case-insensitive) **or** its external-id
set is exactly that catalogue's id set. Both are keyed on the demo-only
`PTWWF0xx` ids, so a real retailer catalogue can never be mistaken for one; the
id-set path simply tolerates benign product-name drift between the demo CSV and
this fixture.

## What it does

For a recognised upload, the orchestrator **skips the AI provider entirely**
and writes pre-approved classifications keyed by `external_product_id`:

- **Protein Tracker** + **WWF**: every product classified.
- The data is **100 % deterministic** golden data (the AI provider is never
  called). To make the demo *look* like a real classification run, the stored
  `source` and `confidence` are **varied with a deterministic, reproducible
  derivation from the product id** (no RNG — the demo stays byte-for-byte
  stable): `confidence` sits in **90–99 %** (never a suspicious flat 100 %);
  the two human-validated products read **`manual_review`** and of the rest
  **~75 % read `deterministic` / ~25 % read `ai`** (Gen AI). No real AI is
  involved — only the label varies — and `rule_id` stays `demo.golden.pt` /
  `demo.golden.wwf` wherever the model permits, so the data remains auditable.

### Review routing — exactly two products

Each catalogue declares which products go to review and on which
methodologies.

- **`demo25`** routes the **same two products** to **both** Protein Tracker
  and WWF review. Result — each card shows:

  | Card | State |
  |------|-------|
  | Protein Tracker | 25/25 categorised · **2 in review** |
  | WWF | 25/25 categorised · **2 in review** |

  and the two PT review products are the **same ids** as the two WWF review
  products (`PTWWF019`, `PTWWF025`).

- **`demo50`** keeps its original behaviour: WWF-only review on
  `PTWWF048` / `PTWWF049` (PT review queue empty).

The review reason is `requested` with an explicit rationale note, so the queue
is auditable.

### Validation UX note

The validation table's default **product view** shows one row per product
(with both the PT and the WWF status), so `demo25`'s two review products
appear as **two product rows**, each offering a PT and a WWF validation. The
legacy **review view** lists one row per `(product, methodology)`, so the same
two products appear there as four rows (two per product). The methodology
cards and the validation product set are what the demo asserts: PT 2, WWF 2,
same two products.

## Loading the current demo catalogue

1. Nothing to enable — the demo path defaults ON (just don't set the kill
   switch `ALTERA_DEMO_GOLDEN_CLASSIFICATION_ENABLED` to a falsy value).
2. Create a project with **both** Protein Tracker and WWF enabled.
3. Upload `DEMO.csv` (it carries both methodologies' required columns so all
   25 products are eligible for both jobs).
4. Run both classifications from the wizard's "Launch classification"
   buttons. Each completes cleanly (`completed`, never
   `completed_with_errors`); no AI call is made for this catalogue.

## Invariants preserved

- Protein Tracker and WWF stay **strictly separate** (own tables, own
  methodology-scoped review queues; no merged states/counts/calculations).
- **No commercial fields** are sent to AI — in fact no AI call happens for a
  recognised catalogue.
- No production classification behaviour changes for normal uploads.
- No calculation logic changes.

## Where the code lives

- `apps/api/altera_api/demo/golden_classification.py` — catalogues +
  recognition + apply helpers.
- `apps/api/altera_api/api/classification_job_orchestrator.py` — one
  flag-guarded branch in `advance_classification_job` (the wizard's path).
- `apps/api/altera_api/api/orchestrator.py` — one flag-guarded branch in
  `classify_upload` (the direct/synchronous path), for parity.
- Tests: `apps/api/tests/demo/test_golden_classification.py`.

To remove the feature after the demo: delete the `demo/` package, the two
branches above, and this doc.
