"""Phase Quality-V2-AG — print a retrieval manifest for review artifacts.

Operator ergonomics: prints, for a project, where the human review artifacts
live and the exact commands to validate them — so a reviewer does not have to
hunt around /tmp on a Render shell. A base64 dump command is offered ONLY as a
last resort (when there is no better download path).

    python -m altera_api.classification_v2.print_nevo_v2_review_artifact_manifest \
        --project-id <uuid> --output-dir /tmp/altera-quality

Read-only: it only inspects the output directory. No DB writes.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from altera_api.classification_v2.apply_nevo_v2_plan import _s


def _newest(out_dir: Path, pattern: str) -> Path | None:
    matches = sorted(out_dir.glob(pattern), key=lambda p: p.stat().st_mtime)
    return matches[-1] if matches else None


def build_manifest(*, project_id: str, output_dir: Path) -> dict[str, object]:
    p = project_id
    workbook = _newest(output_dir, f"nevo_v2_human_review_workbook_{p}_*.xlsx")
    csv_fallback = _newest(output_dir,
                           f"nevo_v2_human_review_workbook_{p}_*.csv")
    readme = _newest(output_dir, f"nevo_v2_human_review_README_{p}_*.txt")
    summary = _newest(output_dir, f"nevo_v2_human_review_summary_{p}_*.json")
    package = _newest(output_dir, f"nevo_v2_batch_review_package_{p}_*.csv")
    return {
        "project_id": p,
        "output_dir": str(output_dir),
        "workbook_xlsx": str(workbook) if workbook else None,
        "csv_fallback": str(csv_fallback) if csv_fallback else None,
        "readme": str(readme) if readme else None,
        "summary": str(summary) if summary else None,
        "review_package": str(package) if package else None,
    }


def _lines(manifest: dict[str, object], *, project_id: str) -> list[str]:
    primary = (manifest["workbook_xlsx"] or manifest["csv_fallback"]
               or "(none — run build_nevo_v2_human_review_workbook first)")
    out = [
        "# NEVO V2 review artifact manifest (read-only — no database writes)",
        f"  project_id: {project_id}",
        f"  output_dir: {manifest['output_dir']}",
        "",
        "Artifacts:",
        f"  Workbook (xlsx): {manifest['workbook_xlsx'] or '(not built)'}",
        f"  CSV fallback:    {manifest['csv_fallback'] or '(not built)'}",
        f"  README:          {manifest['readme'] or '(not built)'}",
        f"  Summary JSON:    {manifest['summary'] or '(not built)'}",
        f"  Review package:  {manifest['review_package'] or '(not built)'}",
        "",
        "After the reviewer fills the workbook, normalize then validate:",
        "  python -m altera_api.classification_v2."
        "normalize_nevo_v2_human_review_workbook \\",
        f"      --input '{primary}' --project-id {project_id} \\",
        "      --output-dir /tmp/altera-quality",
        "  python -m altera_api.classification_v2."
        "validate_nevo_v2_batch_review_package \\",
        "      --input '<the FILLED_NORMALIZED csv printed above>' \\",
        f"      --project-id {project_id} --output-dir /tmp/altera-quality",
        "",
        "Download to your laptop (pick what your shell supports):",
    ]
    download = manifest["workbook_xlsx"] or manifest["csv_fallback"]
    if download:
        out += [
            "  - Render / scp:   download the file above via the dashboard or "
            "scp.",
            "  - LAST RESORT (base64 copy/paste, then base64 -D locally):",
            f"      base64 '{download}'",
        ]
    else:
        out.append("  - (nothing to download yet)")
    return out


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m altera_api.classification_v2."
             "print_nevo_v2_review_artifact_manifest",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--project-id", required=True)
    ap.add_argument("--output-dir", default="/tmp/altera-quality")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    pid = _s(args.project_id)
    manifest = build_manifest(project_id=pid, output_dir=Path(args.output_dir))
    for line in _lines(manifest, project_id=pid):
        print(line)
    print("READ-ONLY — no database writes were made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
