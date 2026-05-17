# Manual review workflow

Manual review is the final authority on every product's classification.
This document specifies the queue, the state machine, the reviewer
interface contract, and the audit trail.

## Why a product enters the queue

A product enters the review queue for one methodology under any of these
conditions:

- `reason='low_confidence'` — the AI classifier returned a confidence
  below the project threshold.
- `reason='ai_parse_failed'` — the AI classifier failed JSON validation
  twice.
- `reason='rule_collision'` — the deterministic engine matched more
  than one rule with conflicting categories.
- `reason='requested'` — an analyst manually requested review for the
  product, regardless of automatic verdict.

A product can be in the review queue for one methodology and fully
classified for the other.

## State machine

```
                +------------+
                |  in_queue  |
                +-----+------+
                      |
                      | reviewer opens
                      v
                +------------+
                | reviewing  |
                +-----+------+
                      |
        +-------------+--------------+
        |             |              |
        v             v              v
   +---------+   +----------+   +-----------+
   |accepted |   | changed  |   |  deferred |
   +---------+   +----------+   +-----------+
```

- `in_queue` — awaiting a reviewer.
- `reviewing` — a reviewer has opened the item (a soft lock that
  expires after 15 minutes).
- `accepted` — the reviewer agreed with the prior classification
  (whether deterministic, AI, or previous review).
- `changed` — the reviewer set a different category.
- `deferred` — the reviewer flagged the item as needing more
  information and re-queued it with a note.

Any of `accepted`, `changed`, or `deferred` writes a
`classification_event` and updates the `classifications` row's `source`
to `manual_review`.

## Soft lock

When a reviewer opens an item, a 15-minute soft lock is placed. Another
reviewer can still see the item but cannot submit a decision until the
lock expires. This avoids two reviewers stepping on each other on the
same product without requiring real-time collaboration infrastructure.

## What the reviewer sees

For each item:

- The product card (the same allowed fields the AI receives).
- The active methodology and its category definitions (PT: four
  groups; WWF: seven food groups with applicable subgroups and, for
  composites, the Step 1 bucket).
- The current classification (a `pt_group` for PT; the full
  food-group + subgroup + composite-bucket object for WWF), its
  source, and (if AI) the AI's rationale.
- The full classification history of this product under this
  methodology.
- A reason field (free text, optional but encouraged).

The reviewer does **not** see sales, units, revenue, or any other
commercial data, even though they are a trusted internal user. This is a
deliberate decision: the reviewer's job is the methodology
classification, not commercial judgement.

## Audit trail

Every reviewer action — open, accept, change, defer — writes to
`classification_events` with:

- `reviewer_user_id`
- `from_category`
- `to_category`
- `reason`
- `created_at`

These records are immutable. A subsequent action creates a new record;
it does not overwrite the prior one.

## Bulk operations

A reviewer can select multiple items in the queue and apply a single
category to all of them, provided every selected item is for the same
methodology. Each item still gets an individual
`classification_event` record.

## Closing a run with `unknown` items

A calculation run can be executed even with `unknown` items still in
the queue. The report will surface this prominently and flag a reduced
confidence score. Reviewers can re-open the run by classifying the
remaining items and trigger a re-calculation; the previous run is
retained.
