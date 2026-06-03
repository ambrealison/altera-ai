"""Phase Quality-V2-D HOTFIX — make the NEVO Voyage benchmark runnable
inside the Render image (package CLI + voyageai as a backend dep),
WITHOUT activating V2 or wiring embeddings into any route.

Safety contract verified here (all offline; no test calls Voyage):
  * App startup imports NO embedding SDK and no V2 embeddings module.
  * A present VOYAGE_API_KEY alone does NOT enable embeddings.
  * Embeddings are disabled by default.
  * The package CLI imports without importing ``voyageai`` (no network).
  * The fake benchmark runs via the package CLI with no key.
  * Voyage mode fails CLEARLY without a key / when embeddings disabled.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from altera_api.classification_v2 import benchmark_nevo_embeddings as cli
from altera_api.embeddings import (
    FakeEmbeddingProvider,
    build_embedding_provider,
    get_embedding_provider,
)
from altera_api.embeddings.provider import EmbeddingProviderError


# ---------------------------------------------------------------------------
# Import isolation — run in a FRESH interpreter so other tests that may
# already have imported voyageai cannot mask a regression.
# ---------------------------------------------------------------------------
def _assert_not_imported(import_line: str, forbidden: tuple[str, ...]) -> None:
    forbidden_literal = repr(forbidden)
    code = (
        f"{import_line}\n"
        "import sys\n"
        f"bad = [m for m in {forbidden_literal} if m in sys.modules]\n"
        "assert not bad, f'unexpectedly imported: {bad}'\n"
        "print('OK')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, (
        f"import {import_line!r} pulled in a forbidden module:\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )


class TestImportIsolation:
    def test_app_startup_imports_no_voyage_stack(self) -> None:
        # Importing the FastAPI app must import NEITHER the voyageai SDK
        # NOR the voyage provider module — embeddings stay fully offline
        # and out of the request path.
        _assert_not_imported(
            "import altera_api.main",
            ("voyageai", "altera_api.embeddings.voyage_provider"),
        )

    def test_package_cli_imports_without_voyageai_sdk(self) -> None:
        # The benchmark CLI loads the offline embeddings abstraction (which
        # pulls in the voyage_provider *module*), but must NEVER import the
        # network-capable ``voyageai`` SDK at import time.
        _assert_not_imported(
            "import altera_api.classification_v2.benchmark_nevo_embeddings",
            ("voyageai",),
        )


# ---------------------------------------------------------------------------
# Routes never reference the embeddings / V2 embeddings stack (static).
# ---------------------------------------------------------------------------
class TestRoutesAreClean:
    def test_api_package_does_not_reference_embeddings(self) -> None:
        from pathlib import Path

        api_dir = Path(cli.__file__).resolve().parents[1] / "api"
        offenders: list[str] = []
        for path in api_dir.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "altera_api.embeddings" in text or "voyageai" in text:
                offenders.append(path.name)
        assert not offenders, f"routes reference the embeddings stack: {offenders}"


# ---------------------------------------------------------------------------
# Default-safe behaviour — key alone never enables embeddings.
# ---------------------------------------------------------------------------
class TestDefaultsSafe:
    def test_embeddings_disabled_by_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ALTERA_ENABLE_EMBEDDINGS", raising=False)
        monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
        assert isinstance(get_embedding_provider(), FakeEmbeddingProvider)

    def test_key_present_alone_does_not_enable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VOYAGE_API_KEY", "sk-test-not-used")
        monkeypatch.delenv("ALTERA_ENABLE_EMBEDDINGS", raising=False)
        monkeypatch.setenv("ALTERA_EMBEDDING_PROVIDER", "voyage")
        # Key + provider=voyage but embeddings disabled → still fake.
        assert isinstance(get_embedding_provider(), FakeEmbeddingProvider)


# ---------------------------------------------------------------------------
# Clear Voyage errors (no network) — exact hotfix message strings.
# ---------------------------------------------------------------------------
class TestClearVoyageErrors:
    def test_no_key_raises_clear_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
        with pytest.raises(EmbeddingProviderError) as exc:
            build_embedding_provider("voyage", model="voyage-4")
        assert str(exc.value) == (
            "VOYAGE_API_KEY is required for embedding-provider=voyage."
        )


# ---------------------------------------------------------------------------
# Package CLI — fake runs without a key; voyage fails clearly.
# ---------------------------------------------------------------------------
class TestPackageCli:
    def test_fake_benchmark_runs_without_key(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
        monkeypatch.delenv("ALTERA_ENABLE_EMBEDDINGS", raising=False)
        rc = cli.main(["--models", "fake", "--output-dir", str(tmp_path)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "[done] fake" in out
        # CSVs land in the writable output dir.
        assert (tmp_path / "nevo_candidates_fake.csv").exists()
        assert (tmp_path / "nevo_mismatches_fake.csv").exists()

    def test_default_output_dir_is_tmp(self) -> None:
        # /app may be read-only in Render → default to a writable temp dir.
        ns = cli.build_arg_parser().parse_args(["--models", "fake"])
        assert ns.output_dir == "/tmp/altera-quality"

    def test_voyage_without_key_require_voyage_exits_nonzero(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
        monkeypatch.setenv("ALTERA_ENABLE_EMBEDDINGS", "true")
        rc = cli.main(
            ["--models", "voyage-4", "--require-voyage",
             "--output-dir", str(tmp_path)]
        )
        assert rc == 1
        assert "VOYAGE_API_KEY is required" in capsys.readouterr().out

    def test_voyage_with_embeddings_disabled_is_skipped(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        monkeypatch.delenv("ALTERA_ENABLE_EMBEDDINGS", raising=False)
        monkeypatch.setenv("VOYAGE_API_KEY", "sk-test-not-used")
        # Without --require-voyage the voyage model is skipped (not fatal)
        # and fake still runs → overall success.
        rc = cli.main(
            ["--models", "fake,voyage-4", "--output-dir", str(tmp_path)]
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "embeddings are disabled" in out
        assert "[done] fake" in out

    def test_voyage_embeddings_disabled_require_voyage_exits_nonzero(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        monkeypatch.delenv("ALTERA_ENABLE_EMBEDDINGS", raising=False)
        monkeypatch.setenv("VOYAGE_API_KEY", "sk-test-not-used")
        rc = cli.main(
            ["--models", "voyage-4", "--require-voyage",
             "--output-dir", str(tmp_path)]
        )
        assert rc == 1
        assert "embeddings are disabled" in capsys.readouterr().out
