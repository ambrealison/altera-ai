"""Phase 30B — rate limiting baseline tests.

Covers:
- Rate limiter disabled by default (does not block requests)
- Limiter enabled blocks after threshold with 429
- 429 uses structured error format (error_code: rate_limited)
- Retry-After header present on 429
- Upload route triggers the uploads group
- Export route triggers the exports group
- Separate users do not share buckets
- Separate IPs do not share buckets
- No Authorization/Cookie token leaked in error detail
"""

from __future__ import annotations

import base64
import json
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from altera_api.ratelimit import (
    RateLimiter,
    RateLimitMiddleware,
    _extract_key,
    _route_group,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_jwt(sub: str) -> str:
    """Build a fake (unsigned) JWT carrying the given sub claim."""
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(
        json.dumps({"sub": sub}).encode()
    ).rstrip(b"=").decode()
    return f"{header}.{payload}.fakesig"


def _app_with_limiter(limiter: RateLimiter | None) -> FastAPI:
    """Minimal FastAPI app wired with the given limiter (None = disabled)."""
    mini = FastAPI()

    mini.add_middleware(RateLimitMiddleware, limiter=limiter, read_env=False)

    @mini.get("/api/v1/projects")
    def projects() -> dict[str, Any]:
        return {"ok": True}

    @mini.post("/api/v1/projects/{pid}/uploads/prepare")
    def prepare_upload(pid: str) -> dict[str, Any]:
        return {"ok": True}

    @mini.get("/api/v1/projects/{pid}/runs/{rid}/export")
    def export(pid: str, rid: str) -> dict[str, Any]:
        return {"ok": True}

    @mini.post("/api/v1/projects/{pid}/uploads/{uid}/classify")
    def classify(pid: str, uid: str) -> dict[str, Any]:
        return {"ok": True}

    return mini


# ---------------------------------------------------------------------------
# _route_group unit tests
# ---------------------------------------------------------------------------


class TestRouteGroup:
    def test_options_always_skip(self) -> None:
        assert _route_group("/api/v1/projects", "OPTIONS") == "skip"

    def test_prepare_upload_is_uploads(self) -> None:
        assert _route_group("/api/v1/projects/x/uploads/prepare", "POST") == "uploads"

    def test_ingest_is_uploads(self) -> None:
        assert _route_group("/api/v1/projects/x/uploads/abc/ingest", "POST") == "uploads"

    def test_wwf_upload_is_uploads(self) -> None:
        assert _route_group("/api/v1/projects/x/wwf-ingredients/upload", "POST") == "uploads"

    def test_classify_is_classify(self) -> None:
        assert _route_group("/api/v1/projects/x/uploads/u/classify", "POST") == "classify"

    def test_jobs_classify_is_classify(self) -> None:
        assert _route_group("/api/v1/projects/x/uploads/u/jobs/classify", "POST") == "classify"

    def test_export_get_is_exports(self) -> None:
        assert _route_group("/api/v1/projects/x/runs/r/export", "GET") == "exports"

    def test_jobs_export_is_exports(self) -> None:
        assert _route_group("/api/v1/projects/x/runs/r/jobs/export", "POST") == "exports"

    def test_list_projects_is_default(self) -> None:
        assert _route_group("/api/v1/projects", "GET") == "default"

    def test_health_is_default(self) -> None:
        assert _route_group("/health", "GET") == "default"


# ---------------------------------------------------------------------------
# Key extraction unit tests
# ---------------------------------------------------------------------------


class TestExtractKey:
    def _fake_request(
        self,
        authorization: str | None = None,
        forwarded_for: str | None = None,
        client_host: str = "127.0.0.1",
    ) -> Any:
        """Build a minimal mock request object."""
        from unittest.mock import MagicMock

        r = MagicMock()
        headers: dict[str, str] = {}
        if authorization is not None:
            headers["authorization"] = authorization
        if forwarded_for is not None:
            headers["x-forwarded-for"] = forwarded_for
        r.headers = headers
        r.client = MagicMock()
        r.client.host = client_host
        return r

    def test_jwt_sub_used_as_key(self) -> None:
        token = _make_jwt("user-abc-123")
        req = self._fake_request(authorization=f"Bearer {token}")
        assert _extract_key(req) == "user:user-abc-123"

    def test_no_auth_falls_back_to_ip(self) -> None:
        req = self._fake_request(client_host="10.0.0.5")
        assert _extract_key(req) == "ip:10.0.0.5"

    def test_forwarded_for_first_hop_used(self) -> None:
        req = self._fake_request(forwarded_for="1.2.3.4, 5.6.7.8")
        assert _extract_key(req) == "ip:1.2.3.4"

    def test_malformed_jwt_falls_back_to_ip(self) -> None:
        req = self._fake_request(authorization="Bearer notajwt", client_host="9.9.9.9")
        assert _extract_key(req) == "ip:9.9.9.9"

    def test_raw_token_not_in_key(self) -> None:
        token = _make_jwt("secret-sub")
        req = self._fake_request(authorization=f"Bearer {token}")
        key = _extract_key(req)
        # Key must not contain the raw token
        assert token not in key


# ---------------------------------------------------------------------------
# Middleware integration tests
# ---------------------------------------------------------------------------


class TestRateLimitDisabledByDefault:
    """Without a limiter the middleware is transparent."""

    def test_no_limiter_never_blocks(self) -> None:
        client = TestClient(_app_with_limiter(None))
        for _ in range(50):
            r = client.get("/api/v1/projects")
            assert r.status_code == 200


class TestRateLimitBlocking:
    """With a tight limiter requests are blocked after the threshold."""

    def _tight_limiter(self, group: str, limit: int = 3) -> RateLimiter:
        limits = {"uploads": 200, "classify": 200, "exports": 200, "default": 200}
        limits[group] = limit
        return RateLimiter(limits=limits)

    def test_blocks_after_default_threshold(self) -> None:
        limiter = RateLimiter(limits={"uploads": 200, "classify": 200, "exports": 200, "default": 3})
        client = TestClient(_app_with_limiter(limiter))
        for _ in range(3):
            assert client.get("/api/v1/projects").status_code == 200
        r = client.get("/api/v1/projects")
        assert r.status_code == 429

    def test_blocks_upload_route(self) -> None:
        limiter = self._tight_limiter("uploads", limit=2)
        client = TestClient(_app_with_limiter(limiter))
        for _ in range(2):
            assert client.post("/api/v1/projects/p1/uploads/prepare").status_code == 200
        assert client.post("/api/v1/projects/p1/uploads/prepare").status_code == 429

    def test_blocks_export_route(self) -> None:
        limiter = self._tight_limiter("exports", limit=2)
        client = TestClient(_app_with_limiter(limiter))
        for _ in range(2):
            assert client.get("/api/v1/projects/p/runs/r/export").status_code == 200
        assert client.get("/api/v1/projects/p/runs/r/export").status_code == 429


class TestRateLimitResponse:
    """429 response shape matches the structured error envelope."""

    def test_429_has_correct_error_code(self) -> None:
        limiter = RateLimiter(limits={"uploads": 200, "classify": 200, "exports": 200, "default": 1})
        client = TestClient(_app_with_limiter(limiter))
        client.get("/api/v1/projects")  # exhaust
        r = client.get("/api/v1/projects")
        assert r.status_code == 429
        body = r.json()
        assert body["detail"]["error_code"] == "rate_limited"
        assert "message" in body["detail"]

    def test_429_has_retry_after_header(self) -> None:
        limiter = RateLimiter(limits={"uploads": 200, "classify": 200, "exports": 200, "default": 1})
        client = TestClient(_app_with_limiter(limiter))
        client.get("/api/v1/projects")  # exhaust
        r = client.get("/api/v1/projects")
        assert r.status_code == 429
        assert "retry-after" in r.headers
        assert int(r.headers["retry-after"]) > 0

    def test_429_detail_has_retry_after_seconds(self) -> None:
        limiter = RateLimiter(limits={"uploads": 200, "classify": 200, "exports": 200, "default": 1})
        client = TestClient(_app_with_limiter(limiter))
        client.get("/api/v1/projects")  # exhaust
        r = client.get("/api/v1/projects")
        body = r.json()
        assert "retry_after_seconds" in body["detail"]["details"]

    def test_429_contains_no_authorization_data(self) -> None:
        limiter = RateLimiter(limits={"uploads": 200, "classify": 200, "exports": 200, "default": 1})
        client = TestClient(_app_with_limiter(limiter))
        token = _make_jwt("sensitive-user-id")
        headers = {"Authorization": f"Bearer {token}"}
        client.get("/api/v1/projects", headers=headers)  # exhaust
        r = client.get("/api/v1/projects", headers=headers)
        assert r.status_code == 429
        body_text = r.text
        # Raw token must not appear in the response body.
        assert token not in body_text


class TestRateLimitBucketIsolation:
    """Different keys do not share buckets."""

    def test_separate_users_have_independent_buckets(self) -> None:
        limiter = RateLimiter(limits={"uploads": 200, "classify": 200, "exports": 200, "default": 2})
        client = TestClient(_app_with_limiter(limiter))

        token_a = _make_jwt("user-a")
        token_b = _make_jwt("user-b")

        # Exhaust user-a's bucket.
        for _ in range(2):
            r = client.get("/api/v1/projects", headers={"Authorization": f"Bearer {token_a}"})
            assert r.status_code == 200
        r = client.get("/api/v1/projects", headers={"Authorization": f"Bearer {token_a}"})
        assert r.status_code == 429

        # user-b's bucket is independent and should still be OK.
        r = client.get("/api/v1/projects", headers={"Authorization": f"Bearer {token_b}"})
        assert r.status_code == 200

    def test_separate_ips_have_independent_buckets(self) -> None:
        limiter = RateLimiter(limits={"uploads": 200, "classify": 200, "exports": 200, "default": 2})
        client = TestClient(_app_with_limiter(limiter))

        # Exhaust IP 1.
        for _ in range(2):
            r = client.get("/api/v1/projects", headers={"X-Forwarded-For": "1.1.1.1"})
            assert r.status_code == 200
        r = client.get("/api/v1/projects", headers={"X-Forwarded-For": "1.1.1.1"})
        assert r.status_code == 429

        # IP 2 is independent.
        r = client.get("/api/v1/projects", headers={"X-Forwarded-For": "2.2.2.2"})
        assert r.status_code == 200

    def test_different_groups_tracked_separately(self) -> None:
        """Upload and default groups have separate budgets for the same user."""
        limiter = RateLimiter(limits={"uploads": 1, "classify": 200, "exports": 200, "default": 200})
        client = TestClient(_app_with_limiter(limiter))

        # Exhaust uploads group.
        r = client.post("/api/v1/projects/p/uploads/prepare")
        assert r.status_code == 200
        r = client.post("/api/v1/projects/p/uploads/prepare")
        assert r.status_code == 429

        # Default group is unaffected.
        r = client.get("/api/v1/projects")
        assert r.status_code == 200
