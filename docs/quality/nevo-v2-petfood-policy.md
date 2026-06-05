# Pet food is food — NEVO V2 nutrition-enrichment policy (Quality-V2-AA)

## Decision

In this business context, **pet food is treated as food for nutrition
enrichment.** A pet-food product may receive a NEVO V2 total-protein value and a
plant/animal split, exactly like a human-food product, **when its Protein
Tracker classification is clear.**

Worked example (acceptable, NOT a cleanup error):

| product | pt_group | total protein | plant | animal |
|---|---|---|---|---|
| Croquettes Chat Saumon 1.5kg | `animal_core` | 21.8 | 0 | 21.8 |

## Rules

- Pet food is **excluded from human-food matching only when the product is
  outside the retailer's nutrition scope** (e.g. accessories: litter, collars,
  toys) — not because it is "pet".
- A pet-food product that the matcher matched and a reviewer approved is a valid
  nutrition source.
- Pet food must **not be automatically classified as non-food** for protein
  enrichment.
- If the pet-food PT group is `animal_core` (or a `plant_based_*` group), the
  plant/animal split is **allowed** and is derived the same way as for any other
  product.
- If the pet-food PT group is `composite_products` / `unknown` /
  `out_of_scope`, **route to review** (no automatic split) — same as any
  composite product.

## How this is already enforced (no behaviour change)

The split pipeline is **PT-group driven and pet-agnostic**:

- `nevo_v2_protein_split.split_proposal()` keys only on `pt_group` + manual
  override. It never looks at pet markers, so `Croquettes Chat`
  (`animal_core`) → `would_split` (animal = total, plant = 0).
- `audit_nevo_v2_protein_split` verifies tags / sums / duplicates / conflicts —
  also pet-agnostic. A correctly-split pet-food row audits as `pass` and is
  **never flagged as an anomaly**.

The only place pet markers appear is the **review-stage** hint in
`nevo_review_workflow._PET_MARKERS` (it sets `suggested_action =
reject_policy_excluded`, priority `P3`). That is a triage convenience for the
human reviewer — it does NOT block enrichment of in-scope pet food, and it is
left unchanged here to avoid destabilising the established review-stage safety
gates (a deliberate choice per the Quality-V2-AA brief). A reviewer who confirms
a pet-food item is in nutrition scope simply approves it, and the rest of the
pipeline treats it as food.

## Net effect

- The 39 `would_split` products (including any pet food classified
  `animal_core`) get a valid plant/animal split.
- The 10 `needs_review` products (including any composite pet food) get **no**
  automatic split.
- Pet food is documented and handled as valid food for protein enrichment when
  the classification is clear. No production behaviour change; no new writes.
