"""Phase 35E — AI classification batch-size + parallelism benchmark.

Standalone script that drives the in-process batch_classifier
against a scripted fake provider with **simulated** OpenAI latency.
Goal: measure wall-time-per-1000-products for several
``batch_size`` / parallelism combinations WITHOUT spending real
OpenAI quota.

Usage
-----

    cd apps/api
    .venv/bin/python scripts/bench_ai_classification.py
    .venv/bin/python scripts/bench_ai_classification.py --batches 25,40,50,75
    .venv/bin/python scripts/bench_ai_classification.py --rows 1000 --latency-ms 5000

The simulated latency models a realistic OpenAI round-trip: a fixed
floor (network + model warm-up) + a per-product term. Numbers below
are illustrative; calibrate by running once with a small batch
against real OpenAI and pinning the resulting per-batch ms.

Output is a printed table:

    batch_size   total_wall_time   batches   ai_calls   wall_time_per_product
    25           ~210 s            40        40         210 ms
    40           ~135 s            25        25         135 ms
    50           ~110 s            20        20         110 ms
    75           ~ 80 s            14        14          80 ms

What we DON'T do
----------------
- We don't run live parallel advance calls against Render. That's
  a separate experiment gated by ``ALTERA_AI_CLASSIFICATION_PARALLELISM``.
- We don't model OpenAI rate limits. The fake provider always
  succeeds.
- We don't write to the products table. The benchmark uses a
  no-op store wrapper so it measures the orchestrator + provider
  call path only.

Recommendation flow
-------------------
1. Run with default args.
2. Read the wall-time-per-product column.
3. If batch_size=50 is 1.5× faster than 25 *with parse_failures <= 5%*,
   recommend bumping the production default from 25 → 50.
4. If a parallelism prototype is wired in (out of scope here),
   compare concurrency=1 vs 2 vs 3 — typically diminishing returns
   past 2 on OpenAI's rate limits.
"""

from __future__ import annotations

import argparse
import json

# Allow running the script from anywhere — fix sys.path to the
# apps/api root.
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

_HERE = os.path.abspath(os.path.dirname(__file__))
_API_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
if _API_ROOT not in sys.path:
    sys.path.insert(0, _API_ROOT)


@dataclass
class _LatencyFakeProvider:
    """Fake ClassifierProvider that adds simulated wall-time per
    batch_classify call.

    The shape mimics a real OpenAI call: a fixed floor (network +
    server warm-up) plus a per-row term (token throughput).
    """

    floor_ms: float = 1500.0
    per_row_ms: float = 25.0
    model_name: str = "bench-fake-model"
    calls: list[Any] = field(default_factory=list)

    @property
    def model(self) -> str:
        return self.model_name

    def classify(self, prompt: Any):  # pragma: no cover
        raise NotImplementedError

    def supports_batch(self) -> bool:
        return True

    def batch_classify(self, prompt: Any):
        # Count rows in the user_message — one per JSONL line that
        # starts with '{'.
        rows = sum(
            1 for line in prompt.user_message.split("\n") if line.startswith("{")
        )
        delay = (self.floor_ms + self.per_row_ms * rows) / 1000.0
        time.sleep(delay)
        self.calls.append(prompt)
        # Build a well-formed envelope so the orchestrator's tolerant
        # parser is happy and parse_failures stays 0.
        results = []
        for line in prompt.user_message.split("\n"):
            if not line.startswith("{"):
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "id" not in row:
                continue
            results.append(
                {
                    "id": row["id"],
                    "pt_group": "plant_based_core",
                    "confidence": 0.95,
                    "rationale": "ok",
                }
            )
        from altera_api.ai.provider import ProviderResponse

        return ProviderResponse(
            raw_text=json.dumps({"results": results}),
            model=self.model_name,
        )


def _make_products(n: int) -> list:
    """Synthesize ``n`` NormalizedProducts with a single PT-eligible
    field set — enough for the batch classifier to chew on."""
    from altera_api.domain.common import Methodology
    from altera_api.domain.product import NormalizedProduct, PTProductFields

    out: list = []
    org = uuid4()
    project = uuid4()
    upload = uuid4()
    now = datetime.now(UTC)
    for i in range(n):
        out.append(
            NormalizedProduct(
                id=uuid4(),
                organisation_id=org,
                project_id=project,
                upload_id=upload,
                row_number=i + 1,
                external_product_id=f"ext-{i}",
                product_name=f"Tofu Lot {i}",
                weight_per_item_kg=Decimal("0.5"),
                language="fr",
                country="FR",
                methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
                pt_fields=PTProductFields(items_purchased=Decimal("1")),
                wwf_fields=None,
                created_at=now,
            )
        )
    return out


def _run_sweep(
    rows: int, batches: list[int], floor_ms: float, per_row_ms: float
) -> list[dict]:
    from altera_api.ai.batch_classifier import batch_classify
    from altera_api.domain.common import Methodology

    products = _make_products(rows)
    results: list[dict] = []
    for batch_size in batches:
        provider = _LatencyFakeProvider(
            floor_ms=floor_ms, per_row_ms=per_row_ms
        )
        t0 = time.perf_counter()
        bundle = batch_classify(
            products,
            provider,
            Methodology.PROTEIN_TRACKER,
            now=datetime.now(UTC),
            batch_size=batch_size,
            enable_retry=False,  # benchmark the happy path
        )
        elapsed = time.perf_counter() - t0
        accepted = sum(
            1
            for v in bundle.verdicts
            if v.__class__.__name__ == "AIAccepted"
        )
        results.append(
            {
                "batch_size": batch_size,
                "rows": rows,
                "ai_calls": len(provider.calls),
                "batches": bundle.batch_count,
                "wall_time_s": round(elapsed, 2),
                "ms_per_product": round(elapsed * 1000 / max(rows, 1), 1),
                "accepted": accepted,
                "parse_failures": bundle.parse_failures,
                "provider_errors": bundle.provider_errors,
            }
        )
    return results


def main() -> int:
    p = argparse.ArgumentParser(description="AI classification benchmark")
    p.add_argument("--rows", type=int, default=1000)
    p.add_argument(
        "--batches",
        type=lambda s: [int(x) for x in s.split(",")],
        default=[25, 40, 50, 75],
        help="Comma-separated batch sizes to sweep (default 25,40,50,75)",
    )
    p.add_argument(
        "--floor-ms",
        type=float,
        default=1500.0,
        help="Simulated per-call fixed latency in ms (default 1500)",
    )
    p.add_argument(
        "--per-row-ms",
        type=float,
        default=25.0,
        help="Simulated per-row latency in ms (default 25)",
    )
    args = p.parse_args()

    print(
        f"Benchmark: rows={args.rows} batches={args.batches} "
        f"floor_ms={args.floor_ms} per_row_ms={args.per_row_ms}\n"
    )

    results = _run_sweep(
        args.rows, args.batches, args.floor_ms, args.per_row_ms
    )

    # Compact table.
    cols = [
        "batch_size",
        "ai_calls",
        "batches",
        "wall_time_s",
        "ms_per_product",
        "accepted",
        "parse_failures",
    ]
    print(" | ".join(f"{c:>15}" for c in cols))
    print("-" * (17 * len(cols)))
    for r in results:
        print(" | ".join(f"{r[c]:>15}" for c in cols))
    print()
    fastest = min(results, key=lambda r: r["wall_time_s"])
    print(
        f"Fastest: batch_size={fastest['batch_size']} "
        f"({fastest['wall_time_s']}s for {args.rows} rows = "
        f"{fastest['ms_per_product']} ms/product)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
