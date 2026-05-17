# ADR 0003 — Enforce AI input restrictions in code, not policy

- Status: Accepted
- Date: 2026-05-14

## Context

Retailers' assortment files often contain commercially sensitive
columns (units sold, sales value, revenue, margin, supplier terms).
The AI classifier provides no marginal benefit from these fields and
the cost of leaking them to a third-party LLM is unacceptable.

A naive defence is documented policy: "do not put commercial data in
prompts". This relies on every future code change respecting the rule.

## Decision

Enforce the restriction in code, at multiple layers:

1. Drop forbidden columns at ingestion.
2. Omit forbidden fields from the domain model.
3. Use a strict typed `ClassifierPromptInput` with only allowed
   fields, not a generic dict.
4. Inspect outbound HTTP payloads against an allow-list of field
   names and abort on violation.
5. A CI test asserts no forbidden field name appears in any prompt
   payload across all fixtures.

## Consequences

Positive:

- A future engineer cannot accidentally include commercial data in a
  prompt without simultaneously bypassing all four layers and the CI
  test.
- A violation in production triggers a high-severity audit event and
  an alert.

Negative:

- The strict typed prompt input requires explicit code changes when
  adding a new allowed field. This is the desired friction.

The decision is final and is not subject to per-project configuration.
