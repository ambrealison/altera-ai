# Design principles

These are the principles that shape every architectural decision in
Altera AI. When a tradeoff is unclear, refer back to this list.

## 1. Methodologies are sacred and separate

Protein Tracker (GPA & ProVeg) and WWF (Planet-Based Diets Retailer
Methodology) are external, published methodologies. The job of Altera
AI is to implement them faithfully, not to "improve" them. The two
modules share no calculation code paths. They use different units
(protein-kg vs product-kg-as-sold) and different category structures
(4 PT groups vs 7 WWF food groups with subgroups). A blended view is
allowed only as an explicitly labelled experimental internal feature.

## 2. Deterministic first, AI second, human last

For every product, classification is attempted in this fixed order:

1. **Deterministic rules** — keyword, category, and brand-driven rules
   anchored in the methodologies' own published category mappings (PT
   Appendix 1; WWF FG1–FG7 with subgroups). Most products should be
   classified here.
2. **AI classifier** — strict-JSON-output LLM call, used only for
   items the deterministic engine could not classify. AI never sees
   commercial data, and AI never sees physical-sales-quantity fields
   (`items_purchased`, `items_sold`) either.
3. **Manual review** — for low-confidence AI outputs, AI parse
   failures, and any product the rules engine cannot place.

The human reviewer is always the final authority. The PT PDF
explicitly authorises manual reassignment of deviating products in an
otherwise-uniform category; the system implements this.

## 3. No commercial data leaves the platform to an LLM

Revenue, margin, sales value, store-level performance, supplier
terms, and confidential strategy are **never** included in any prompt
sent to an external LLM. Physical-quantity fields required by the
methodologies (`items_purchased`, `items_sold`) are also kept out of
prompts — the AI never needs them. This is enforced at the prompt
construction layer in code; see
[../classification/ai-inputs-policy.md](../classification/ai-inputs-policy.md).

## 4. Every number is reproducible

Every calculation row stores the methodology version, the methodology
source edition, the taxonomy version, and the rules version that
produced it. A run can be re-executed at any time with the same
inputs and the same versions and must produce the same output. The
system never silently upgrades methodology under existing data.

## 5. The tenant boundary is enforced in the database

Row-Level Security policies on every table are the source of truth for
multi-tenant isolation. Application code is a second line of defence,
not the first.

## 6. Schemas, not free text

All AI outputs are constrained by a JSON schema and validated before
they touch domain logic. A failed parse triggers exactly one retry,
then routes the row to manual review.

## 7. Open source, audit-friendly

The methodology implementation is the product. The repository is laid
out so an external auditor (GPA, ProVeg, WWF, academic partner) can
trace any calculation line in a report back to the code that produced
it. Inline magic numbers and unlabelled defaults are avoided.

## 8. Plain data formats by default

CSV (with an optional JSON companion for WWF Step 2 ingredients) in;
CSV / JSON / Markdown out for MVP. Excel and PDF are valuable but
they are not the default deliverable because they are harder to diff,
version, and audit.

## 9. Tests live next to behaviour that matters

Input validation, unit conversion (incl. dairy equivalents),
deterministic classification, Protein Tracker calculations, WWF
calculations (both Step 1 and Step 2), manual review state
transitions, and AI JSON parsing are all required to have automated
tests. Other code is tested as needed.

## 10. Reversibility over cleverness

When a feature can be added incrementally or behind a flag, prefer
that over a wholesale rewrite. The methodology modules must be
extensible — for example, when GPA / ProVeg publish a new PT edition,
or when WWF refines PHD reference values — without breaking older
calculations.

## 11. Altera owns methodology review and approval

Altera AI is delivered as a managed service. The client uploads a
catalogue and downloads an approved report; everything in between —
manual review of ambiguous items, methodology judgement calls, and
the final approval of the report — is performed by **Altera-internal
staff**, not by the client. The platform encodes this:

- The manual review queue is visible only to `altera_*` roles.
- Report exports are gated by an `approval_status` field; clients
  cannot download a draft or under-review report.
- The client UI shows a simplified status (Waiting for upload /
  Processing / Under Altera review / Report ready / Archived); it
  never exposes the internal lifecycle state machine.

This principle exists because the value Altera AI delivers is a
**defensible, methodology-faithful number** — and defensibility
requires that a trained human, not a client analyst on a deadline,
makes the close calls.

## 12. Client UI and Altera UI are separate

The platform serves two audiences with different goals:

- The **GMS client** wants to upload data, see a status, and download
  an approved report. Nothing else.
- **Altera staff** want to operate the pipeline: validate inputs,
  triage classification, drive manual review, run calculations,
  inspect drafts, and approve releases.

These are different UIs over the same backend, routed by
`organisation_type` and role at sign-in. We never collapse them into
a single "one-size-fits-all" surface, because the cognitive load on
the client must stay near zero.
