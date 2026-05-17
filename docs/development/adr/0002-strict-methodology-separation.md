# ADR 0002 — Protein Tracker and WWF are strictly separate modules

- Status: Accepted
- Date: 2026-05-14

## Context

Protein Tracker and WWF address related but distinct questions about a
retailer's assortment. They use different category buckets, different
default rules, and produce different headline figures. A naive
implementation could share types, share rules, or even produce a
"blended" figure.

## Decision

The two methodologies are implemented as **strictly separate Python
packages**. They:

- Have their own category enums.
- Have their own deterministic rules files.
- Have their own `calculate(...)` functions and `Result` dataclasses.
- Have their own CHANGELOG and `VERSION`.
- Do not import from each other under any circumstance.

The codebase does not produce a hybrid methodology output. A blended
view, if ever introduced, must be behind an internal feature flag and
visibly labelled as experimental.

## Consequences

Positive:

- Methodology fidelity is structurally enforced. There is no place in
  the code where the two methodologies could quietly merge.
- A change to one methodology never silently changes the other.
- Auditors can read the code for one methodology in isolation.

Negative:

- Some code duplication is unavoidable (shared utilities, e.g. weight
  application, are pulled out where they are truly methodology-neutral).
- An analyst comparing results across methodologies must read both
  report blocks rather than a single number.

We accept the duplication as a feature, not a defect.
