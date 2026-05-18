# Runbook: AI classification failure

**Severity**: P2 (classification degraded) / P3 (single-product failure)
**Oncall trigger**: Upload job fails at classification stage, AI error rate spikes, or many products land in `unknown` state

---

## Background

AI classification calls OpenAI (or a configured provider) to classify products that did not match any deterministic rule. A failed AI call does not fail the whole upload — the product is marked `unknown` and flagged for manual review. A spike in `unknown` products or a job failure at the classification stage indicates a systemic AI issue.

See `docs/classification/ai-inputs-policy.md` for what data is and is not sent to the AI provider.

## Symptoms

- Upload job status is `failed` with `error_message` containing `classification.ai_error`.
- Many products have `classification_source = ai` but `classification_result = unknown`.
- Structured log shows repeated: `level=ERROR msg=ai.classification_failed`.
- High latency on upload jobs (> 90s for < 500 rows).

## Triage steps

### 1. Check AI provider status

- OpenAI: https://status.openai.com
- Check for active incidents or degraded API regions.

### 2. Check structured logs

```
level=ERROR msg=ai.classification_failed
```

Key fields to look for:

| Field | Meaning |
|---|---|
| `status_code` | HTTP status from the AI provider (429 = rate limit, 500/503 = provider error) |
| `attempt` | Retry attempt number (up to 3) |
| `model` | Model name used |
| `product_id` | Affected product |

### 3. Check API key validity

```sh
# Test the API key directly:
curl https://api.openai.com/v1/models \
  -H "Authorization: Bearer $OPENAI_API_KEY" | head -5
```

A 401 means the key is invalid or revoked. A 429 means the key has hit its rate limit.

### 4. Check classification results in Postgres

```sql
SELECT classification_source, classification_result, COUNT(*)
FROM classifications
WHERE organisation_id = '<org_id>'
  AND created_at > now() - interval '1 hour'
GROUP BY 1, 2
ORDER BY 3 DESC;
```

A sudden spike in `source=ai, result=unknown` or `result=failed` indicates systemic AI failure.

### 5. Check the job error message

```sql
SELECT error_message, updated_at
FROM jobs
WHERE job_id = '<job_id>';
```

## Resolution

| Scenario | Action |
|---|---|
| Provider outage | Wait for recovery; re-trigger the upload job |
| Rate limit (429) | Reduce batch size in config; re-trigger with backoff |
| Invalid API key | Rotate key in environment; restart API process |
| Persistent `unknown` for specific products | Send to manual review queue |
| AI returning unexpected format | File issue with AI module; use deterministic fallback |

### Re-triggering classification for failed products

Products that were classified as `unknown` due to a transient AI failure can be re-classified without re-uploading the CSV:

```sh
# Altera internal endpoint (Phase 15+):
POST /api/v1/projects/<project_id>/classify
```

This re-runs classification only on products with `classification_result = unknown`.

## Prevention

- AI calls use exponential backoff with 3 retries.
- A failed AI call sets `classification_result = unknown` rather than failing the job, so partial classification succeeds.
- `OPENAI_API_KEY` should be rotated every 90 days and stored in the secrets manager, not in `.env` on the server.
- Monitor the `products_with_missing_category` metric in the coverage section of each report.
