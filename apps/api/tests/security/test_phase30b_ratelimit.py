"""Phase 30B — rate limiting baseline tests (updated for Phase 30C keying).

Covers:
- Rate limiter disabled by default (does not block requests)
- Limiter enabled blocks after threshold with 429
- 429 uses structured error format (error_code: rate_limited)
- Retry-After header present on 429
- Upload route triggers the uploads group
- Export route triggers the exports group
- Separate IPs do not share buckets
- No raw JWT token in bucket key, logs, or error response
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
    _extract_ip,
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

    def test_legacy_uploads_post_is_uploads(self) -> None:
        assert _route_group("/api/v1/projects/x/uploads", "POST") == "uploads"

    def test_prepare_upload_is_uploads(self) -> None:
        assert _route_group("/api/v1/projects/x/uploads/prepare", "POST") == "uploads"

    def test_ingest_is_uploads(self) -> None:
        assert _route_group("/api/v1/projects/x/uploads/abc/ingest", "POST") == "uploads"

    def test_jobs_validate_is_uploads(self) -> None:
        assert _route_group("/api/v1/projects/x/uploads/u/jobs/validate", "POST") == "uploads"

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

    def test_jobs_calculate_is_compute(self) -> None:
        assert _route_group("/api/v1/projects/x/jobs/calculate", "POST") == "compute"

    def test_scenario_run_is_compute(self) -> None:
        assert _route_group("/api/v1/scenarios/s1/run", "POST") == "compute"

    def test_comparisons_get_is_compute(self) -> None:
        assert _route_group("/api/v1/projects/x/comparisons", "GET") == "compute"

    def test_list_projects_is_default(self) -> None:
        assert _route_group("/api/v1/projects", "GET") == "default"

    def test_health_is_default(self) -> None:
        assert _route_group("/health", "GET") == "default"


# ---------------------------------------------------------------------------
# IP extraction tests
# ---------------------------------------------------------------------------


class TestExtractIp:
    def _fake_request(
        self,
        forwarded_for: str | None = None,
        client_host: str = "127.0.0.1",
    ) -> Any:
        from unittest.mock import MagicMock
        r = MagicMock()
        headers: dict[str, str] = {}
        if forwarded_for is not None:
            headers["x-forwarded-for"] = forwarded_for
        r.headers = headers
        r.client = MagicMock()
        r.client.host = client_host
        return r

    def test_no_proxy_uses_peer_ip(self) -> None:
        req = self._fake_request(client_host="10.0.0.5")
        assert _extract_ip(req, []) == "ip:10.0.0.5"

    def test_untrusted_proxy_ignores_forwarded_for(self) -> None:
        req = self._fake_request(forwarded_for="1.2.3.4", client_host="9.9.9.9")
        assert _extract_ip(req, []) == "ip:9.9.9.9"

    def test_jwt_bearer_in_request_still_keys_by_ip(self) -> None:
        """A JWT sub claim must NOT influence the rate-limit key."""
        from unittest.mock import MagicMock
        token = _make_jwt("secret-user-id")
        req = MagicMock()
        req.headers = {"authorization": f"Bearer {token}"}
        req.client = MagicMock()
        req.client.host = "5.5.5.5"
        key = _extract_ip(req, [])
        assert key == "ip:5.5.5.5"
        assert "secret-user-id" not in key
        assert token not in key


# ---------------------------------------------------------------------------
# Middleware integration tests
# ---------------------------------------------------------------------------


class TestRateLimitDisabledByDefault:
    def test_no_limiter_never_blocks(self) -> None:
        client = TestClient(_app_with_limiter(None))
        for _ in range(50):
            r = client.get("/api/v1/projects")
            assert r.status_code == 200


class TestRateLimitBlocking:
    def _tight_limiter(self, group: str, limit: int = 3) -> RateLimiter:
        limits = {
            "uploads": 200, "classify": 200, "exports": 200,
            "compute": 200, "default": 200,
        }
        limits[group] = limit
        return RateLimiter(limits=limits)

    def test_blocks_after_default_threshold(self) -> None:
        limiter = RateLimiter(limits={
            "uploads": 200, "classify": 200, "exports": 200, "compute": 200, "default": 3,
        })
        client = TestClient(_app_with_limiter(limiter))
        for _ in range(3):
            assert client.get("/api/v1/projects").status_code == 200
        assert client.get("/api/v1/projects").status_code == 429

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
    def _spent_limiter(self) -> RateLimiter:
        return RateLimiter(limits={
            "uploads": 200, "classify": 200, "exports": 200, "compute": 200, "default": 1,
        })

    def test_429_has_correct_error_code(self) -> None:
        client = TestClient(_app_with_limiter(self._spent_limiter()))
        client.get("/api/v1/projects")
        r = client.get("/api/v1/projects")
        assert r.status_code == 429
        assert r.json()["detail"]["error_code"] == "rate_limited"

    def test_429_has_retry_after_header(self) -> None:
        client = TestClient(_app_with_limiter(self._spent_limiter()))
        client.get("/api/v1/projects")
        r = client.get("/api/v1/projects")
        assert r.status_code == 429
        assert int(r.headers["retry-after"]) > 0

    def test_429_detail_has_retry_after_seconds(self) -> None:
        client = TestClient(_app_with_limiter(self._spent_limiter()))
        client.get("/api/v1/projects")
        r = client.get("/api/v1/projects")
        assert "retry_after_seconds" in r.json()["detail"]["details"]

    def test_429_does_not_echo_authorization_token(self) -> None:
        client = TestClient(_app_with_limiter(self._spent_limiter()))
        token = _make_jwt("sensitive-user-id")
        headers = {"Authorization": f"Bearer {token}"}
        client.get("/api/v1/projects", headers=headers)
        r = client.get("/api/v1/projects", headers=headers)
        assert r.status_code == 429
        assert token not in r.text


class TestRateLimitBucketIsolation:
    def test_separate_ips_have_independent_buckets(self) -> None:
        limiter = RateLimiter(limits={
            "uploads": 200, "classify": 200, "exports": 200, "compute": 200, "default": 2,
        })
        # Exhaust bucket for ip:1.1.1.1.
        for _ in range(2):
            allowed, _ = limiter.check("ip:1.1.1.1", "default")
            assert allowed
        blocked, _ = limiter.check("ip:1.1.1.1", "default")
        assert not blocked

        # ip:2.2.2.2 is independent.
        allowed, _ = limiter.check("ip:2.2.2.2", "default")
        assert allowed

    def test_forged_jwt_sub_does_not_create_per_user_buckets(self) -> None:
        """Phase 30C: forged JWTs must not create separate buckets — all keyed by IP."""
        limiter = RateLimiter(limits={
            "uploads": 200, "classify": 200, "exports": 200, "compute": 200, "default": 2,
        })
        client = TestClient(_app_with_limiter(limiter))
        token_a = _make_jwt("user-a")
        token_b = _make_jwt("user-b")
        # Exhaust with user-a JWT.
        for _ in range(2):
            client.get("/api/v1/projects", headers={"Authorization": f"Bearer {token_a}"})
        # user-b JWT from the same IP should also be rate-limited (same bucket).
        r = client.get("/api/v1/projects", headers={"Authorization": f"Bearer {token_b}"})
        assert r.status_code == 429

    def test_different_groups_tracked_separately(self) -> None:
        limiter = RateLimiter(limits={"uploads": 1, "classify": 200, "exports": 200, "compute": 200, "default": 200})
        client = TestClient(_app_with_limiter(limiter))
        assert client.post("/api/v1/projects/p/uploads/prepare").status_code == 200
        assert client.post("/api/v1/projects/p/uploads/prepare").status_code == 429
        assert client.get("/api/v1/projects").status_code == 200
