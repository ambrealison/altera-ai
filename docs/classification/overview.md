# Classification — overview

A product moves through up to three stages of classification, in a fixed
order:

```
                     +-----------------------+
                     |  Deterministic rules  |
                     +----------+------------+
                                | classified or pass-through
                                v
                     +-----------------------+
                     |    AI classifier      |  (only for pass-through)
                     +----------+------------+
                                | high-confidence JSON or fall-through
                                v
                     +-----------------------+
                     |    Manual review      |  (final authority)
                     +-----------------------+
```

Each stage is independently versioned and independently testable. A
product can complete classification at any of the three stages.

## Stage 1 — deterministic rules

The deterministic engine reads versioned rules and the versioned
taxonomy. It produces, per `(product, methodology)`:

- A methodology-specific classification (a PT group for PT, or the
  WWF food-group plus the methodology's required sub-fields for WWF), or
- A pass-through verdict ("no rule matched").

Confidence is reported as `1.0` for any rule match: deterministic rules
do not produce uncertain results. If two rules collide on a product, the
product is routed to manual review with `reason='rule_collision'`.

See [docs/classification/deterministic-rules.md](deterministic-rules.md).

## Stage 2 — AI classifier

The AI classifier is invoked only for products the deterministic engine
did not classify. The classifier:

- Sends only the **allowed inputs** (see
  [ai-inputs-policy.md](ai-inputs-policy.md)).
- Requests a **strict JSON object** that matches the published schema
  (see [json-validation.md](json-validation.md)).
- On parse failure, retries exactly once with the same prompt.
- On a second parse failure, routes the product to manual review with
  `reason='ai_parse_failed'`.
- On a low-confidence result (confidence below the configured project
  threshold), routes the product to manual review with
  `reason='low_confidence'`.

See [docs/classification/ai-classifier.md](ai-classifier.md).

## Stage 3 — manual review

Manual review is operated by users with the `reviewer` or above role.
Any decision made in manual review is the final word for the product
under that methodology. The decision is logged as an immutable event in
`classification_events`.

A reviewer may also choose to **promote** a low-confidence AI result
("accept as-is") rather than re-classifying. This still creates a
classification event so the audit trail is complete.

See [docs/classification/review.md](review.md).

## Concurrency

A product can be in classification for both methodologies in parallel.
The two methodologies share the deterministic engine's pass-through
mechanism, but each methodology runs its own rules and, where needed,
its own AI call.

## Confidence thresholds

- Deterministic rule match: confidence `1.0`.
- AI accepted: confidence `>=` project threshold (default `0.8`).
- AI low-confidence: confidence `<` project threshold → manual review.
- AI parse-failed twice: routed to manual review (confidence not set).

The project threshold is configurable per project, with the default of
`0.8` chosen because the AI confidence calibration is documented in
[ai-classifier.md](ai-classifier.md).
