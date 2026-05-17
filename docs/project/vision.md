# Vision

## What Altera AI is

Altera AI is an open-source, **managed-service SaaS** that quantifies
the **plant vs. animal balance** of a grocery retailer's product
range. It is delivered in the spirit of platforms like Greenly: the
client (a supermarket chain) supplies a product catalogue; Altera AI
operates the pipeline, owns the methodology review, and delivers an
approved report. The client never has to become a methodology expert.

It is built around two independent, published methodologies, each
implemented as a separate, non-interchangeable module:

1. **Protein Tracker** — Green Protein Alliance & ProVeg, Foodservice
   edition (August 2024) and the retail edition. A four-group
   classification (`plant_based_core`, `plant_based_non_core`,
   `composite_products`, `animal_core`) over **purchases by protein
   weight**, with a 50/50 default split applied at the group level to
   composite products.
2. **WWF Planet-Based Diets Retailer Methodology** — WWF Food
   Practice, 2024. A seven-food-group classification over **sales by
   product weight**, with plant/animal splits within the protein-rich
   groups (FG1, FG2), composite handling at two levels (whole-weight
   bucket and ingredient-level food-group attribution), and reporting
   against the **Planetary Health Diet** reference proportions.

The two methodologies measure different things in different units
(kg of protein vs. kg of product as sold). Altera AI never produces a
blended figure, except behind an internal experimental flag.

## Who it is for

The primary customer is the **GMS** (Grande Distribution / large
grocery chain): Carrefour, Lidl, Auchan, Casino, E.Leclerc, Tesco,
Ahold Delhaize, and equivalents. Their sustainability, ESG, and
buying teams need a defensible, auditable measurement of the
plant/animal balance of their product range to support reporting,
internal targets, and stakeholder dialogue.

Secondary users are the **Altera-internal** team operating the
platform on behalf of clients: analysts, reviewers, and methodology
leads. Tertiary audiences are NGOs and methodology stewards (GPA,
ProVeg, WWF) and academic partners.

## Why it exists

Most retailers do not have an internal classification of their range
by protein / food group. Building one by hand is slow and
inconsistent; using a single opaque vendor risks methodology lock-in
and unverifiable numbers; staffing a methodology team in-house is
disproportionate to the deliverable. Altera AI addresses this by
operating the platform as a managed service:

- Implementing two **published methodologies** transparently and
  side-by-side, so the client picks the one their stakeholders accept.
- Combining a **deterministic rules engine** with an **AI classifier**
  for ambiguous cases, and routing low-confidence items into the
  **Altera-internal manual review queue**, so a trained reviewer is
  always the final authority — not a client analyst on a deadline.
- Producing a report that is **reviewed and approved by Altera** (the
  methodology lead) before the client can download it. The approval
  is the product.
- Storing **methodology version, source edition, taxonomy version,
  and rules version** on every calculation, so a number produced
  today can be reproduced months later.
- Being **open source**, so the methodology implementation can be
  audited by GPA, ProVeg, WWF, and any third party.

## How the engagement looks

1. **Onboarding.** Altera creates a GMS-client organisation in the
   platform and provisions client users (owner / admin / viewer).
2. **Upload.** The client uploads its catalogue CSV (and, for WWF
   Step 2, a companion ingredient JSON).
3. **Automated pipeline.** Altera AI validates, normalises,
   categorises, and calculates — Protein Tracker, WWF, or both.
4. **Altera manual review.** Ambiguous and sensitive items are
   handled by Altera-internal reviewers. The client does not see the
   review queue.
5. **Altera approval.** An Altera methodology lead reviews the draft
   report and either approves it or returns it for adjustment.
6. **Client delivery.** Once approved, the report is released for
   client download. The client sees a simplified status only:
   *Waiting for upload / Processing / Under Altera review / Report
   ready / Archived.*

## What success looks like

A GMS client can move from "we sent our catalogue" to "we received an
approved report" without operating the platform's internal mechanics.
For Altera, success means the internal team can run dozens of client
projects in parallel without methodology drift, with every figure
traceable to its methodology, taxonomy, rules version, and named
reviewer.

## Non-goals

Altera AI is explicitly **not**:

- A self-service tool for clients to drive their own methodology
  decisions. The client uploads and downloads; Altera operates.
- A nutrition database. It uses protein values supplied by the user
  or drawn from referenced sources (NEVO Online or equivalents), not
  a proprietary nutrition dataset.
- A recipe-level meal-planning tool. It operates on company SKU /
  range data, with composite recipes optionally supplied for WWF
  Step 2.
- A carbon or wider sustainability accounting tool. Plant/animal
  balance under PT and food-group balance under WWF are the
  deliverables.
- A vendor of "the true" methodology. PT and WWF are external;
  Altera AI is a faithful implementation, not an arbiter.
- A recommendation engine — yet. A future Altera tier (see
  [docs/future/recommendation-engine.md](../future/recommendation-engine.md))
  will suggest range adjustments toward methodology targets, but this
  is out of scope until after pilot.
