"""Phase Quality-V2-AI — print a NEVO language-specific V2 apply plan summary.

Convenience read-only inspector for a plan produced by
``plan_nevo_language_specific_v2_apply``: prints the headline metrics, the skip
breakdown, and the selected candidates. No DB writes; no routes.

    python -m altera_api.classification_v2.\
summarize_nevo_language_specific_v2_apply_plan \
        --plan-json /tmp/altera-quality/\
nevo_language_specific_v2_apply_plan_fr_<project>.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m altera_api.classification_v2."
             "summarize_nevo_language_specific_v2_apply_plan",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--plan-json", required=True)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    path = Path(args.plan_json)
    if not path.exists():
        print(f"ERROR: plan JSON not found: {path}")
        return 2
    s = json.loads(path.read_text(encoding="utf-8"))

    print("# NEVO language-specific V2 apply plan summary")
    print(f"  project={s.get('project_id')} "
          f"language={s.get('retailer_language')}")
    print(f"  recommendation={s.get('recommendation')} "
          f"coverage={s.get('language_reference_coverage')}")
    print(f"  total_rows={s.get('total_rows')} "
          f"candidates={s.get('candidate_count')} "
          f"skipped={s.get('skipped_count')}")
    print(f"  skip_counts={s.get('skip_counts')}")
    print(f"  candidate_nevo_codes={s.get('candidate_nevo_codes')}")
    tag = s.get("source_tagging", {})
    print(f"  source={tag.get('source')} match_method={tag.get('match_method')}"
          f" source_version={tag.get('source_version')}")
    print(f"  apply_supported={s.get('apply_supported')} "
          f"apply_status={s.get('apply_status')} dry_run={s.get('dry_run')}")
    print(f"  next_step: {s.get('recommendation_for_next_step')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
