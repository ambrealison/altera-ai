"""Phase Quality-V2-AI — conservative multilingual retrieval benchmark (CLI).

Thin wrapper around ``compare_nevo_multilingual_retrieval`` that forces the
conservative decision layer on: the conservative output candidate is the
baseline match by default and only switches to a multilingual candidate on a
clear, guarded improvement of a baseline failure/safety-downgrade (never
replacing a baseline auto_ready, never crossing a food family). The raw
comparison is still produced for before/after. Read-only; no DB writes; no
routes.

    python -m altera_api.classification_v2.\
compare_nevo_multilingual_retrieval_conservative \
        --project-id <uuid> --baseline-reference-source nevo \
        --multilingual-reference /tmp/altera-quality/nevo_reference_multilingual.csv \
        --output-dir /tmp/altera-quality --top-k 20 \
        --cache-dir /tmp/altera-quality/cache --require-voyage

Pass --allow-multilingual-overwrite-auto-ready to (unsafely) let it replace a
baseline auto_ready candidate.
"""

from __future__ import annotations

from typing import Any

from altera_api.classification_v2 import compare_nevo_multilingual_retrieval as base


def main(argv: list[str] | None = None, *, store: Any = None) -> int:
    argv = list(argv or [])
    if "--decision-mode" not in argv:
        argv += ["--decision-mode", "conservative"]
    return base.main(argv, store=store)


if __name__ == "__main__":
    raise SystemExit(main())
