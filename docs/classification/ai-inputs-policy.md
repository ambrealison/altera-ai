# AI inputs policy

The single most important rule in Altera AI:

> **Commercially sensitive data is never sent to an external LLM.**

This document specifies what is allowed, what is forbidden, how the rule
is enforced, and what happens when a violation is detected.

## Allowed inputs

The following fields may appear in a prompt sent to an external LLM:

- `product_name`
- `retailer_category`
- `retailer_subcategory`
- `brand`
- `ingredients_text`
- `labels`
- `language`
- `country` (only when needed for terminology disambiguation)

## Forbidden inputs

The following fields are forbidden from any prompt sent to an external
LLM, regardless of context:

- `items_purchased`, `items_sold` — these are physical methodology
  quantities required for calculation; they live in the database but
  are not classification inputs and the AI never needs them.
- `weight_per_item_kg` — required for calculation; not a classification
  signal.
- `sales_value`, `revenue`, `margin`, `cost_price`.
- Any store-level performance metric (`store_*` and similar).
- `supplier_id`, `supplier_name`, `contract_terms`.
- Any column starting with `confidential_` or `internal_`.
- Any retailer commercial strategy text.
- Any field marked `commercial=true` in the per-org column metadata.

A subset of these (sales value, revenue, margin, supplier terms,
confidential strategy) is also forbidden from the database entirely —
see [../data/input-formats.md](../data/input-formats.md) for the
ingestion-boundary drop list. The other fields above (item counts,
per-item weight) live in the database for calculation but never leave
the process into a prompt.

## Enforcement

Enforcement is **layered**:

1. **Ingestion boundary.** Forbidden columns are dropped at the ingest
   step before the data lands in the `products` table. The drop is
   recorded in the upload's audit metadata.
2. **Domain model.** The `Product` Pydantic model does not have fields
   for any commercial data. There is no place in the type system for
   them to exist on a product.
3. **Prompt construction.** `ClassifierPromptInput` (`altera_api/ai/prompt_input.py`)
   is a strict allow-list Pydantic model with **only** the allowed fields.
   `ClassifierPromptInput.from_product()` copies only those fields by name —
   commercial fields are physically absent from the type. Attempting to add
   a forbidden field is a type error at the boundary.
4. **Outbound HTTP guard.** `assert_payload_allowed()` (`altera_api/ai/policy.py`)
   inspects the outbound product card dict against `ALLOWED_PROMPT_FIELDS` before
   any HTTP call leaves the process. A forbidden key raises immediately and routes
   the product to manual review.
5. **CI test.** `tests/api/test_phase17_ai_classify.py::TestAIPrivacy` constructs
   a `NormalizedProduct` with all commercial fields populated, calls
   `ClassifierPromptInput.from_product()`, serialises the payload, and asserts
   that none of the forbidden field names are present. It also asserts that
   `ALLOWED_PROMPT_FIELDS` matches the `ClassifierPromptInput` model fields exactly.

If any of layers 1–4 fail, the upstream layer catches the violation. The
CI test ensures a regression is caught before merge.

## What happens on a detected violation

If the outbound HTTP guard (layer 4) ever fires in production:

- The request is aborted.
- A `commercial_data_block` audit event is recorded with the upload id,
  organisation id, and the offending field name (not value).
- The product is marked needing manual review.
- An alert is raised to the on-call operator.

The system does not retry the request. A violation is a code bug, not a
transient failure.

## Why this rule is absolute

A retailer trusts Altera AI with their assortment. Most retailers will
not agree to send sales, margin, or supplier data to a third-party LLM.
Even when a retailer is willing, the legal and reputational cost of a
leak is not worth the marginal classification benefit. The rule is
therefore absolute and enforced in code, not in policy.
