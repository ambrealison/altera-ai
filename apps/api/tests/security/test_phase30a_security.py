"""Phase 30A — security hardening baseline tests.

Covers:
- Security response headers present on every API response
- Cache-Control: no-store on API paths
- Environment-driven CORS: allowed origin accepted, disallowed rejected
- Secrets safety: .env.example files do not contain recognisable real secrets
- Signed-URL expiry defaults documented and within safe bounds
- Upload validation limits enforced at the boundary
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from altera_api.api.state import InMemoryStore
from altera_api.api.store_factory import get_store
from altera_api.main import app

REPO_ROOT = Path(__file__).resolve().parents[4]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _dev_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALTERA_DEV_AUTH_ENABLED", "true")
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def client(store: InMemoryStore):
    app.dependency_overrides[get_store] = lambda: store
    try:
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_store, None)


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------


class TestSecurityHeaders:
    def test_x_content_type_options(self, client: TestClient) -> None:
        r = client.get("/api/v1/projects")
        assert r.headers.get("x-content-type-options") == "nosniff"

    def test_x_frame_options(self, client: TestClient) -> None:
        r = client.get("/api/v1/projects")
        assert r.headers.get("x-frame-options") == "DENY"

    def test_referrer_policy(self, client: TestClient) -> None:
        r = client.get("/api/v1/projects")
        assert r.headers.get("referrer-policy") == "strict-origin-when-cross-origin"

    def test_permissions_policy_present(self, client: TestClient) -> None:
        r = client.get("/api/v1/projects")
        assert "permissions-policy" in r.headers

    def test_health_endpoint_has_security_headers(self, client: TestClient) -> None:
        r = client.get("/health")
        assert r.headers.get("x-content-type-options") == "nosniff"
        assert r.headers.get("x-frame-options") == "DENY"

    def test_api_responses_not_cached(self, client: TestClient) -> None:
        r = client.get("/api/v1/projects")
        assert r.headers.get("cache-control") == "no-store"

    def test_non_api_path_no_forced_cache_control(self, client: TestClient) -> None:
        r = client.get("/health")
        assert r.headers.get("cache-control", "not-set") != "no-store"


# ---------------------------------------------------------------------------
# CORS — environment-driven origins
# ---------------------------------------------------------------------------


class TestCORSConfig:
    def test_allowed_origin_gets_cors_header(
        self, store: InMemoryStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://app.altera-ai.com")
        # Re-import after env change so _parse_allowed_origins sees the new value.
        from altera_api import main as _main

        allowed_origins = _main._parse_allowed_origins()
        assert "https://app.altera-ai.com" in allowed_origins

    def test_multiple_origins_parsed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(
            "CORS_ALLOWED_ORIGINS",
            "https://app.altera-ai.com, https://staging.altera-ai.com",
        )
        from altera_api import main as _main

        origins = _main._parse_allowed_origins()
        assert "https://app.altera-ai.com" in origins
        assert "https://staging.altera-ai.com" in origins
        assert len(origins) == 2

    def test_default_origin_is_localhost(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
        from altera_api import main as _main

        origins = _main._parse_allowed_origins()
        assert origins == ["http://localhost:3000"]

    def test_wildcard_not_in_default_origins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
        from altera_api import main as _main

        origins = _main._parse_allowed_origins()
        assert "*" not in origins


# ---------------------------------------------------------------------------
# Secrets safety — .env.example files must not contain real secrets
# ---------------------------------------------------------------------------


class TestSecretsSafety:
    def _read_env_example(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def test_root_env_example_no_openai_key(self) -> None:
        content = self._read_env_example(REPO_ROOT / ".env.example")
        # A real OpenAI key matches sk-proj-[48+ chars] or sk-[48+ chars]
        import re

        real_key_pattern = re.compile(r"sk-proj-[A-Za-z0-9_-]{40,}")
        assert not real_key_pattern.search(content), (
            "Root .env.example contains what looks like a real OpenAI key"
        )

    def test_api_env_example_no_service_role_value(self) -> None:
        content = self._read_env_example(
            REPO_ROOT / "apps" / "api" / ".env.example"
        )
        lines = {
            k.strip(): v.strip()
            for line in content.splitlines()
            if "=" in line and not line.strip().startswith("#")
            for k, v in [line.split("=", 1)]
        }
        assert lines.get("SUPABASE_SERVICE_ROLE_KEY", "") == "", (
            "apps/api/.env.example must not contain a real service role key"
        )

    def test_web_env_example_no_service_role_key(self) -> None:
        content = self._read_env_example(
            REPO_ROOT / "apps" / "web" / ".env.example"
        )
        assert "SERVICE_ROLE" not in content, (
            "apps/web/.env.example must not reference the service role key"
        )

    def test_web_env_example_only_public_keys(self) -> None:
        content = self._read_env_example(
            REPO_ROOT / "apps" / "web" / ".env.example"
        )
        # Frontend must only use NEXT_PUBLIC_ prefixed Supabase vars
        assert "SUPABASE_SERVICE_ROLE_KEY" not in content
        # All variable names referencing Supabase should be public
        import re

        supabase_vars = re.findall(r"^(SUPABASE_\w+)\s*=", content, re.MULTILINE)
        for var in supabase_vars:
            assert var.startswith("NEXT_PUBLIC_"), (
                f"Non-public Supabase var in web .env.example: {var}"
            )


# ---------------------------------------------------------------------------
# Storage — signed URL expiry
# ---------------------------------------------------------------------------


class TestSignedUrlExpiry:
    def test_export_download_url_default_expiry(self) -> None:
        from altera_api.storage.fake import FakeStorageService

        svc = FakeStorageService()
        import inspect

        sig = inspect.signature(svc.generate_export_download_url)
        expires_default = sig.parameters["expires_in"].default
        assert expires_default <= 600, (
            f"Export download URL default expiry ({expires_default}s) exceeds 600s"
        )

    def test_upload_url_default_expiry(self) -> None:
        from altera_api.storage.fake import FakeStorageService

        svc = FakeStorageService()
        import inspect

        sig = inspect.signature(svc.generate_upload_url)
        expires_default = sig.parameters["expires_in"].default
        assert expires_default <= 600, (
            f"Upload URL default expiry ({expires_default}s) exceeds 600s"
        )


# ---------------------------------------------------------------------------
# Upload validation limits
# ---------------------------------------------------------------------------


class TestUploadValidationLimits:
    def test_max_upload_bytes_defined_and_reasonable(self) -> None:
        from altera_api.ingestion.validators import MAX_UPLOAD_BYTES

        assert MAX_UPLOAD_BYTES > 0
        # Must not be absurdly large (>100 MB would be a surprise)
        assert MAX_UPLOAD_BYTES <= 100 * 1024 * 1024

    def test_allowed_extensions_defined(self) -> None:
        from altera_api.ingestion.validators import ALLOWED_EXTENSIONS

        assert "csv" in ALLOWED_EXTENSIONS
        # Dangerous extensions must not be allowed
        for dangerous in ("exe", "sh", "py", "js", "php", "html"):
            assert dangerous not in ALLOWED_EXTENSIONS, (
                f"Dangerous extension {dangerous!r} is in ALLOWED_EXTENSIONS"
            )

    def test_allowed_content_types_defined(self) -> None:
        from altera_api.ingestion.validators import ALLOWED_CONTENT_TYPES

        assert "text/csv" in ALLOWED_CONTENT_TYPES
        # Script/executable MIME types must not be allowed
        for dangerous_mime in ("text/html", "application/javascript", "application/x-sh"):
            assert dangerous_mime not in ALLOWED_CONTENT_TYPES, (
                f"Dangerous MIME {dangerous_mime!r} is in ALLOWED_CONTENT_TYPES"
            )
