#!/usr/bin/env python
"""Phase Quality-V2-D — NEVO embeddings benchmark (thin wrapper).

The implementation now lives in the package module
``altera_api.classification_v2.benchmark_nevo_embeddings`` so it ships
inside the Render runtime image (which copies ``altera_api/`` but NOT
this top-level ``scripts/`` dir). Prefer the package entry point::

    python -m altera_api.classification_v2.benchmark_nevo_embeddings --help

This wrapper keeps the local ``.venv/bin/python scripts/...`` invocation
working from a dev checkout.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from altera_api.classification_v2.benchmark_nevo_embeddings import (  # noqa: E402
    main,
)

if __name__ == "__main__":
    raise SystemExit(main())
