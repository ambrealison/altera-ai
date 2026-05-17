# Recommendation engine — future scope

**Status:** Not in scope until after pilot. **Do not implement.**
**Earliest planned phase:** 30 (see
[../development/ROADMAP.md](../development/ROADMAP.md)).

This document is a placeholder so the team has a single canonical
description of the intent. It is not a design document.

## What it is

A future Altera tier that, given an **approved** Protein Tracker or
WWF run, suggests **range adjustments** — SKU substitutions,
delistings, listings, or category-level rebalancing — that would move
the client's measured plant/animal ratio (or food-group shares)
toward a target.

The recommendation engine is a *consumer* of an approved report. It
never feeds back into classification, calculation, or the
methodology itself.

## What it is not

- It is **not** part of the methodology. Recommendations are
  derivative; the methodology answers "what is the current ratio,"
  recommendations answer "how could you shift it."
- It is **not** a buying tool. It does not place orders, talk to
  suppliers, or read commercial data.
- It is **not** an AI assistant that sees the catalogue. The
  recommendation logic is rules-based first (substitution graphs in
  the taxonomy: "if you delist X, the nearest plant-based equivalent
  in your range is Y"). Any LLM use is restricted to explaining a
  recommendation in natural language, never to generating it.

## Inputs (when built)

- The approved `runs` row and its `calculation_rows`.
- The client's current product range (already in `products`).
- The taxonomy's substitution graph (a future addition to the
  taxonomy package).
- A client-set target (e.g. "60% plant share under PT" or "match the
  Planetary Health Diet reference for FG1").

## Outputs (when built)

- A ranked list of suggested actions, each with:
  - The action type (substitute / delist / list / rebalance).
  - The affected SKUs.
  - The projected impact on the headline figure.
  - A confidence band.
  - An optional natural-language explanation.

## Why it's deferred

Altera AI's first job is to be a credible, defensible measurement
platform. Recommendations are a value-add layer on top of measurement
that only makes sense once:

1. The measurement platform is in production with real client data.
2. The methodology approval workflow is mature.
3. Pilot clients have asked for "now what?" — i.e. there is demand.

Building recommendations before measurement is trusted would
undermine the trust the measurement product needs to earn.

## Constraints inherited from the platform

When the recommendation engine is eventually built, it must respect:

- The commercial-data firewall — recommendations never see revenue,
  margin, supplier terms, or store performance.
- The methodology-separation principle — PT recommendations and WWF
  recommendations are distinct; no blended target.
- The Altera-approval pattern — any client-visible recommendation
  set is approved by an `altera_methodology_lead` before release.
