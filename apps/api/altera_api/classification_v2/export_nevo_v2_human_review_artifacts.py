"""Phase Quality-V2-AH — persist NEVO V2 human review artifacts + download path.

The reviewer-friendly artifacts from Quality-V2-AG (workbook CSV/XLSX, README,
summary JSON) are produced in /tmp on Render, which forced reviewers to
cat/base64 files out of a shell. This CLI persists those artifacts to the
existing private Supabase ``exports`` bucket (reusing ``StorageService``) and
emits signed download URLs + an export manifest, so a reviewer gets a durable
link instead.

    python -m altera_api.classification_v2.export_nevo_v2_human_review_artifacts \
        --project-id <uuid> --output-dir /tmp/altera-quality \
        --artifact-prefix nevo_v2_human_review

It only ever exports the reviewer-friendly artifacts (never the raw retailer
upload, never commercial columns). It writes NO database rows: the existing
``report_exports`` table FK-references ``calculation_runs`` and a V2 review
run_id is a slug, not a calculation-run UUID — DB registration is not required
by the storage mechanism, so it is intentionally skipped. No nutrition records
are written; V1 stays default; embeddings stay off; no route imports this.

If Supabase storage is not configured (e.g. local dev), it still computes
checksums and writes the manifest with local paths, and says so clearly.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from altera_api.classification_v2.apply_nevo_v2_plan import _s

#: (role, filename-suffix glob, content-type) in console/print order.
_ARTIFACTS = (
    ("workbook_xlsx", "workbook", "xlsx",
     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ("workbook_csv", "workbook", "csv", "text/csv"),
    ("readme", "README", "txt", "text/plain"),
    ("summary", "summary", "json", "application/json"),
)
_RUN_SLUG_RE = re.compile(r"^[A-Za-z0-9._-]+$")
DEFAULT_EXPIRES_IN = 3600  # 1 hour — enough for a human, short enough to limit replay.
_MIN_EXPIRES_IN, _MAX_EXPIRES_IN = 60, 86_400


class ExportError(RuntimeError):
    """A human-facing export failure (missing artifacts, bad path, …)."""


def _newest(out_dir: Path, pattern: str) -> Path | None:
    matches = sorted(out_dir.glob(pattern), key=lambda p: p.stat().st_mtime)
    return matches[-1] if matches else None


def _run_id_from(name: str, project_id: str) -> str | None:
    m = re.search(rf"_{re.escape(project_id)}_(.+)\.(?:csv|xlsx|txt|json)$",
                  name)
    return m.group(1) if m else None


def discover_artifacts(out_dir: Path, *, project_id: str, prefix: str,
                       ) -> list[dict[str, Any]]:
    """Find the newest reviewer-friendly artifacts. Never raw retailer files."""
    found: list[dict[str, Any]] = []
    for role, suffix, ext, content_type in _ARTIFACTS:
        pattern = f"{prefix}_{suffix}_{project_id}_*.{ext}"
        path = _newest(out_dir, pattern)
        if path is None:
            continue
        # Defence in depth: only ever export files that start with the
        # reviewer-artifact prefix (never a raw retailer upload).
        if not path.name.startswith(f"{prefix}_"):
            continue
        found.append({"role": role, "path": path, "content_type": content_type})
    return found


def _safe_run_slug(run_id: str) -> str:
    run = _s(run_id)
    if not run or not _RUN_SLUG_RE.match(run):
        raise ExportError(
            f"unsafe run_id {run_id!r}: only letters, digits, '.', '_', '-' "
            "are allowed (no path separators).")
    return run


def _build_storage_path(*, organisation_id: UUID, project_id: UUID,
                        run_id: str, export_id: UUID, filename: str) -> str:
    """Server-generated, tenant-scoped path. No user-controlled separators."""
    safe_name = Path(filename).name  # basename only — strips any traversal.
    return (
        f"organisations/{organisation_id}/exports/nevo_v2_review/"
        f"{project_id}/{run_id}/{export_id}/{safe_name}"
    )


def _resolve_org_id(*, project_id: UUID, organisation_id: str | None,
                    store: Any) -> UUID:
    if organisation_id:
        return UUID(_s(organisation_id))
    if store is None:
        from altera_api.api.store_factory import get_store
        store = get_store()
    project = store.get_project(project_id)
    return project.organisation_id


def export_artifacts(*, project_id: str, output_dir: Path, prefix: str,
                     run_id: str | None = None,
                     organisation_id: str | None = None,
                     expires_in: int = DEFAULT_EXPIRES_IN,
                     use_storage: bool = True, storage: Any = None,
                     store: Any = None,
                     now: datetime | None = None) -> dict[str, Any]:
    pid = _s(project_id)
    project_uuid = UUID(pid)  # rejects non-UUID project ids (no path injection).
    expires_in = max(_MIN_EXPIRES_IN, min(_MAX_EXPIRES_IN, int(expires_in)))
    generated_at = (now or datetime.now(UTC))

    artifacts = discover_artifacts(output_dir, project_id=pid, prefix=prefix)
    if not artifacts:
        raise ExportError(
            f"no review artifacts found in {output_dir} for project {pid} "
            f"(prefix {prefix!r}). Build them first with "
            "build_nevo_v2_human_review_workbook.")

    run = _safe_run_slug(
        run_id or _run_id_from(artifacts[0]["path"].name, pid) or "run")

    # Resolve storage + tenant only when we actually intend to upload.
    if use_storage and storage is None:
        from altera_api.storage.factory import get_storage_service
        storage = get_storage_service()
    storage_configured = bool(use_storage and storage is not None)

    org_uuid: UUID | None = None
    export_id = uuid4()  # server-generated, unique per export run.
    if storage_configured:
        org_uuid = _resolve_org_id(project_id=project_uuid,
                                   organisation_id=organisation_id, store=store)

    exported_files: list[dict[str, Any]] = []
    for art in artifacts:
        path: Path = art["path"]
        content = path.read_bytes()
        record: dict[str, Any] = {
            "role": art["role"],
            "filename": path.name,
            "local_path": str(path),
            "content_type": art["content_type"],
            "file_size_bytes": len(content),
            "checksum_sha256": hashlib.sha256(content).hexdigest(),
            "storage_path": None,
            "signed_url": None,
            "expires_at": None,
            "uploaded": False,
        }
        if storage_configured and org_uuid is not None:
            storage_path = _build_storage_path(
                organisation_id=org_uuid, project_id=project_uuid, run_id=run,
                export_id=export_id, filename=path.name)
            storage.upload_export(storage_path, content, path.name)
            record["storage_path"] = storage_path
            record["uploaded"] = True
            try:
                record["signed_url"] = storage.generate_export_download_url(
                    storage_path, path.name, expires_in)
                record["expires_at"] = (
                    generated_at + timedelta(seconds=expires_in)).isoformat()
            except Exception as exc:  # noqa: BLE001 — surface, don't crash.
                record["signed_url"] = None
                record["signed_url_error"] = str(exc)
        exported_files.append(record)

    uploaded_any = any(f["uploaded"] for f in exported_files)
    manifest = {
        "phase": "quality-v2-ah",
        "project_id": pid,
        "run_id": run,
        "organisation_id": str(org_uuid) if org_uuid else None,
        "generated_at": generated_at.isoformat(),
        "storage_configured": storage_configured,
        "storage_bucket": ("exports" if uploaded_any else None),
        "export_id": str(export_id),
        "expires_in_seconds": expires_in if uploaded_any else None,
        "db_export_registered": False,
        "db_registration_skipped_reason": (
            "report_exports.run_id FK-references calculation_runs; a V2 review "
            "run_id is a slug, not a calculation-run UUID, so DB registration "
            "is not applicable and intentionally skipped."),
        "exported_files": exported_files,
        "recommendation": (
            "ready_for_download" if uploaded_any
            else "storage_not_configured_local_only"),
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = (output_dir
                     / f"nevo_v2_human_review_export_manifest_{pid}_{run}.json")
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    manifest["manifest_path"] = str(manifest_path)
    return manifest


def _url_or_local(rec: dict[str, Any]) -> str:
    return rec["signed_url"] or f"(local) {rec['local_path']}"


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m altera_api.classification_v2."
             "export_nevo_v2_human_review_artifacts",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--project-id", required=True)
    ap.add_argument("--output-dir", default="/tmp/altera-quality")
    ap.add_argument("--artifact-prefix", default="nevo_v2_human_review")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--organisation-id", default=None,
                    help="Override; otherwise resolved from the project.")
    ap.add_argument("--expires-in", type=int, default=DEFAULT_EXPIRES_IN,
                    help="Signed-URL TTL in seconds (clamped 60..86400).")
    ap.add_argument("--no-storage", action="store_true",
                    help="Skip upload; write a local-only manifest.")
    return ap


def main(argv: list[str] | None = None, *, storage: Any = None,
         store: Any = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        manifest = export_artifacts(
            project_id=args.project_id, output_dir=Path(args.output_dir),
            prefix=args.artifact_prefix, run_id=args.run_id,
            organisation_id=args.organisation_id, expires_in=args.expires_in,
            use_storage=not args.no_storage, storage=storage, store=store)
    except ExportError as exc:
        print(f"ERROR: {exc}")
        return 2

    by_role = {f["role"]: f for f in manifest["exported_files"]}
    print("# NEVO V2 review artifact export (read-only — no database writes)")
    print(f"  project={manifest['project_id']} run_id={manifest['run_id']}")
    if manifest["storage_configured"]:
        print(f"  storage=exports bucket  expires_in="
              f"{manifest['expires_in_seconds']}s")
    else:
        print("  storage NOT configured — local-only manifest written. Set "
              "SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY to persist + sign.")
    for role, label in (("workbook_xlsx", "XLSX workbook"),
                        ("workbook_csv", "CSV workbook"),
                        ("readme", "README"),
                        ("summary", "Summary JSON")):
        rec = by_role.get(role)
        if rec:
            print(f"  {label}: {_url_or_local(rec)}")
    print(f"  Manifest: {manifest['manifest_path']}")
    print(f"  RECOMMENDATION: {manifest['recommendation']}")
    print("")
    print("After the reviewer fills the file and returns it, normalize +"
          " validate:")
    print("  python -m altera_api.classification_v2."
          "normalize_nevo_v2_human_review_workbook \\")
    print(f"      --input <filled_file> --project-id {manifest['project_id']} "
          "--output-dir /tmp/altera-quality")
    print("  python -m altera_api.classification_v2."
          "validate_nevo_v2_batch_review_package \\")
    print("      --input <FILLED_NORMALIZED csv> --project-id "
          f"{manifest['project_id']} --output-dir /tmp/altera-quality")
    print("READ-ONLY — no database writes were made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
