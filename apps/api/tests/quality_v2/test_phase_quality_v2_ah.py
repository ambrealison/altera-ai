"""Phase Quality-V2-AH — persistent review artifact export + download path.

Persists the Quality-V2-AG reviewer-friendly artifacts (workbook/README/summary)
to the existing private Supabase ``exports`` bucket via ``StorageService``,
emitting signed URLs + checksums + a manifest — so a reviewer gets a durable
download link instead of cat/base64 from /tmp. Server-generated, tenant-scoped
storage paths; never the raw retailer upload; no commercial columns; no DB
writes; V1 default; embeddings off; routes clean.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

import altera_api.api  # noqa: F401  isort:skip
from altera_api.classification_v2 import (
    export_nevo_v2_human_review_artifacts as E,
)
from altera_api.storage.fake import FakeStorageService

PID = "326c6e1c-46b2-4103-98f1-331afadb721a"
ORG = uuid4()
NOW = datetime(2026, 6, 8, 12, 0, 0, tzinfo=UTC)


class _Project:
    organisation_id = ORG


class _ReadOnlyStore:
    """Reads the project; explodes on any write method."""

    def __init__(self) -> None:
        self.reads: list[str] = []

    def get_project(self, pid):
        self.reads.append("get_project")
        return _Project()

    def __getattr__(self, name):
        if name.split("_")[0] in ("add", "update", "delete", "upsert",
                                  "insert"):
            raise AssertionError(f"write attempted: {name}")
        raise AttributeError(name)


def _make_artifacts(d: Path, run: str, *, prefix="nevo_v2_human_review",
                    project=PID) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{prefix}_workbook_{project}_{run}.csv").write_text(
        "review_priority,product_name\nP1,Riz Basmati\n", encoding="utf-8")
    (d / f"{prefix}_README_{project}_{run}.txt").write_text(
        "README — how to review\n", encoding="utf-8")
    (d / f"{prefix}_summary_{project}_{run}.json").write_text(
        '{"total_rows": 53}', encoding="utf-8")


def _export(d: Path, **kw):
    base = dict(project_id=PID, output_dir=d, prefix="nevo_v2_human_review",
                storage=FakeStorageService(), store=_ReadOnlyStore(), now=NOW)
    base.update(kw)
    return E.export_artifacts(**base)


# ---------------------------------------------------------------------------
# Discovery + checksums + manifest.
# ---------------------------------------------------------------------------
class TestExport:
    def test_auto_discovers_newest(self, tmp_path) -> None:
        _make_artifacts(tmp_path, "R1")
        past = time.time() - 100
        for p in tmp_path.glob("*_R1.*"):
            os.utime(p, (past, past))
        _make_artifacts(tmp_path, "R2")
        manifest = _export(tmp_path)
        assert manifest["run_id"] == "R2"
        for f in manifest["exported_files"]:
            assert f["filename"].endswith("_R2.csv") or "_R2." in f["filename"]

    def test_computes_checksums_and_sizes(self, tmp_path) -> None:
        _make_artifacts(tmp_path, "RX")
        manifest = _export(tmp_path)
        for f in manifest["exported_files"]:
            content = Path(f["local_path"]).read_bytes()
            assert f["checksum_sha256"] == hashlib.sha256(content).hexdigest()
            assert f["file_size_bytes"] == len(content)

    def test_builds_safe_tenant_scoped_paths(self, tmp_path) -> None:
        _make_artifacts(tmp_path, "RX")
        manifest = _export(tmp_path)
        export_id = manifest["export_id"]
        for f in manifest["exported_files"]:
            sp = f["storage_path"]
            # Server-generated, tenant + project + run + export-id scoped.
            assert sp.startswith(
                f"organisations/{ORG}/exports/nevo_v2_review/{PID}/RX/")
            assert export_id in sp
            assert PID in sp and "/RX/" in sp
            assert ".." not in sp

    def test_manifest_written_with_required_fields(self, tmp_path) -> None:
        _make_artifacts(tmp_path, "RX")
        manifest = _export(tmp_path)
        path = Path(manifest["manifest_path"])
        assert path.exists()
        data = json.loads(path.read_text())
        for key in ("project_id", "run_id", "generated_at", "exported_files",
                    "storage_configured", "recommendation", "export_id",
                    "db_export_registered"):
            assert key in data
        assert data["recommendation"] == "ready_for_download"
        assert data["db_export_registered"] is False
        first = data["exported_files"][0]
        for key in ("local_path", "storage_path", "signed_url", "expires_at",
                    "file_size_bytes", "content_type", "checksum_sha256"):
            assert key in first

    def test_signed_url_metadata_present_with_storage(self, tmp_path) -> None:
        _make_artifacts(tmp_path, "RX")
        manifest = _export(tmp_path)
        assert manifest["storage_configured"] is True
        for f in manifest["exported_files"]:
            assert f["uploaded"] is True
            assert f["signed_url"] and f["signed_url"].startswith("https://")
            assert f["expires_at"] == "2026-06-08T13:00:00+00:00"  # NOW + 1h

    def test_expires_in_clamped(self, tmp_path) -> None:
        _make_artifacts(tmp_path, "RX")
        manifest = _export(tmp_path, expires_in=10**9)
        assert manifest["expires_in_seconds"] == E._MAX_EXPIRES_IN


# ---------------------------------------------------------------------------
# Refusals / safety.
# ---------------------------------------------------------------------------
class TestRefusals:
    def test_missing_artifacts_clear_error(self, tmp_path) -> None:
        with pytest.raises(E.ExportError, match="no review artifacts found"):
            _export(tmp_path)

    def test_does_not_export_raw_retailer_file(self, tmp_path) -> None:
        _make_artifacts(tmp_path, "RX")
        # A raw retailer upload with commercial columns must never be exported.
        (tmp_path / "raw_retailer_upload.csv").write_text(
            "product,volume,sales,price\nx,99,1000,4.5\n", encoding="utf-8")
        manifest = _export(tmp_path)
        blob = json.dumps(manifest)
        assert "raw_retailer_upload" not in blob
        assert "volume" not in blob and "sales" not in blob
        roles = {f["role"] for f in manifest["exported_files"]}
        assert roles == {"workbook_csv", "readme", "summary"}

    def test_rejects_path_traversal_run_id(self, tmp_path) -> None:
        _make_artifacts(tmp_path, "RX")
        with pytest.raises(E.ExportError, match="unsafe run_id"):
            _export(tmp_path, run_id="../../etc/passwd")

    def test_rejects_non_uuid_project_id(self, tmp_path) -> None:
        _make_artifacts(tmp_path, "RX", project="not-a-uuid")
        with pytest.raises(ValueError):
            E.export_artifacts(
                project_id="not-a-uuid", output_dir=tmp_path,
                prefix="nevo_v2_human_review", storage=FakeStorageService(),
                store=_ReadOnlyStore(), now=NOW)

    def test_no_db_writes(self, tmp_path) -> None:
        _make_artifacts(tmp_path, "RX")
        store = _ReadOnlyStore()
        manifest = E.export_artifacts(
            project_id=PID, output_dir=tmp_path,
            prefix="nevo_v2_human_review", storage=FakeStorageService(),
            store=store, now=NOW)
        # Only a project read happened — no write method was reached.
        assert store.reads == ["get_project"]
        assert manifest["db_export_registered"] is False

    def test_local_only_when_storage_unconfigured(self, tmp_path) -> None:
        _make_artifacts(tmp_path, "RX")
        manifest = E.export_artifacts(
            project_id=PID, output_dir=tmp_path,
            prefix="nevo_v2_human_review", use_storage=False, now=NOW)
        assert manifest["storage_configured"] is False
        assert manifest["recommendation"] == "storage_not_configured_local_only"
        for f in manifest["exported_files"]:
            assert f["uploaded"] is False
            assert f["signed_url"] is None
            assert f["checksum_sha256"]  # still computed locally.

    def test_module_has_no_enrichment_writes(self) -> None:
        src = Path(E.__file__).read_text(encoding="utf-8")
        for needle in ("add_enrichment", "update_enrichment",
                       "add_export_record", ".insert(", "delete_"):
            assert needle not in src, needle


# ---------------------------------------------------------------------------
# Route / matcher safety.
# ---------------------------------------------------------------------------
class TestSafety:
    def test_no_route_imports_classification_v2(self) -> None:
        api_dir = Path(E.__file__).resolve().parents[1] / "api"
        offenders = [
            p.name for p in api_dir.rglob("*.py")
            if "classification_v2" in p.read_text(encoding="utf-8")
        ]
        assert not offenders

    def test_v1_default_and_embeddings_off(self, monkeypatch) -> None:
        from altera_api.classification_v2.nevo_matcher import (
            resolve_nevo_matcher_version,
        )
        from altera_api.quality_config import embeddings_enabled

        monkeypatch.delenv("ALTERA_NEVO_MATCHER_VERSION", raising=False)
        monkeypatch.delenv("ALTERA_ENABLE_EMBEDDINGS", raising=False)
        assert str(resolve_nevo_matcher_version()) == "v1"
        assert embeddings_enabled() is False

    def test_cli_main_runs_clean(self, tmp_path) -> None:
        _make_artifacts(tmp_path, "RX")
        rc = E.main(
            ["--project-id", PID, "--output-dir", str(tmp_path)],
            storage=FakeStorageService(), store=_ReadOnlyStore())
        assert rc == 0
