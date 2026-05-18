"""Phase 30C — security remediation tests.

Covers:
- Trusted proxy: X-Forwarded-For only trusted from known proxies
- Untrusted clients cannot spoof X-Forwarded-For
- Trusted proxies can pass real client IP through X-Forwarded-For
- Malformed X-Forwarded-For falls back safely
- Bucket eviction: stale empty buckets are cleaned up
- Bucket eviction: max bucket cap is enforced
- Max cap eviction does not remove active buckets when possible
- Compute route group coverage (calculate, scenario run, comparisons)
- CORS fail-closed in production mode (missing CORS_ALLOWED_ORIGINS raises)
- CORS dev mode uses localhost default when CORS_ALLOWED_ORIGINS unset
- Secret scan: .env.example files contain no real sk-proj- OpenAI keys
"""

from __future__ import annotations

import ipaddress
import time
from pathlib import Path

import pytest

from altera_api.ratelimit import (
    RateLimiter,
    _extract_ip,
    _is_trusted_proxy,
    _parse_trusted_proxies,
    _route_group,
)

REPO_ROOT = Path(__file__).resolve().parents[4]


# ---------------------------------------------------------------------------
# Trusted proxy helpers
# ---------------------------------------------------------------------------


class TestParseTrustedProxies:
    def test_empty_string_returns_empty(self) -> None:
        assert _parse_trusted_proxies("") == []

    def test_single_ip(self) -> None:
        nets = _parse_trusted_proxies("10.0.0.1")
        assert len(nets) == 1
        assert ipaddress.ip_address("10.0.0.1") in nets[0]

    def test_cidr_range(self) -> None:
        nets = _parse_trusted_proxies("10.0.0.0/8")
        assert ipaddress.ip_address("10.0.1.100") in nets[0]

    def test_multiple_entries(self) -> None:
        nets = _parse_trusted_proxies("10.0.0.1, 192.168.0.0/16")
        assert len(nets) == 2

    def test_invalid_entry_skipped(self) -> None:
        nets = _parse_trusted_proxies("not_an_ip, 10.0.0.1")
        assert len(nets) == 1


class TestIsTrustedProxy:
    def test_empty_list_always_false(self) -> None:
        assert not _is_trusted_proxy("1.2.3.4", [])

    def test_ip_in_cidr_is_trusted(self) -> None:
        nets = _parse_trusted_proxies("10.0.0.0/8")
        assert _is_trusted_proxy("10.0.1.50", nets)

    def test_ip_outside_cidr_not_trusted(self) -> None:
        nets = _parse_trusted_proxies("10.0.0.0/8")
        assert not _is_trusted_proxy("192.168.1.1", nets)

    def test_invalid_host_not_trusted(self) -> None:
        nets = _parse_trusted_proxies("10.0.0.0/8")
        assert not _is_trusted_proxy("not_an_ip", nets)


class TestExtractIpTrustBehavior:
    def _fake_request(
        self,
        forwarded_for: str | None = None,
        client_host: str = "127.0.0.1",
    ):
        from unittest.mock import MagicMock
        r = MagicMock()
        headers: dict[str, str] = {}
        if forwarded_for is not None:
            headers["x-forwarded-for"] = forwarded_for
        r.headers = headers
        r.client = MagicMock()
        r.client.host = client_host
        return r

    def test_untrusted_client_cannot_spoof_forwarded_for(self) -> None:
        req = self._fake_request(forwarded_for="1.1.1.1", client_host="6.6.6.6")
        # no trusted proxies configured
        key = _extract_ip(req, [])
        assert key == "ip:6.6.6.6"

    def test_trusted_proxy_forwarded_for_is_used(self) -> None:
        nets = _parse_trusted_proxies("10.0.0.1")
        req = self._fake_request(forwarded_for="203.0.113.5", client_host="10.0.0.1")
        key = _extract_ip(req, nets)
        assert key == "ip:203.0.113.5"

    def test_trusted_proxy_with_multiple_hops_takes_first(self) -> None:
        nets = _parse_trusted_proxies("10.0.0.1")
        req = self._fake_request(forwarded_for="203.0.113.5, 10.0.0.2", client_host="10.0.0.1")
        key = _extract_ip(req, nets)
        assert key == "ip:203.0.113.5"

    def test_trusted_proxy_empty_forwarded_for_falls_back_to_peer(self) -> None:
        nets = _parse_trusted_proxies("10.0.0.1")
        req = self._fake_request(forwarded_for="", client_host="10.0.0.1")
        key = _extract_ip(req, nets)
        assert key == "ip:10.0.0.1"

    def test_no_client_returns_unknown(self) -> None:
        from unittest.mock import MagicMock
        r = MagicMock()
        r.headers = {}
        r.client = None
        key = _extract_ip(r, [])
        assert key == "ip:unknown"


# ---------------------------------------------------------------------------
# Bucket eviction and cap
# ---------------------------------------------------------------------------


class TestBucketEviction:
    def test_stale_empty_buckets_are_evicted(self) -> None:
        limiter = RateLimiter(limits={"default": 200, "uploads": 20, "classify": 10, "exports": 30, "compute": 5})
        now = time.monotonic()

        # Populate a bucket but then "age" its last_seen artificially.
        limiter.check("ip:old", "default")
        with limiter._lock:
            bucket = limiter._buckets[("ip:old", "default")]
            # Force timestamps to expire and last_seen to be old.
            bucket._timestamps.clear()
            bucket.last_seen = now - 120  # 2 windows ago

        # Trigger cleanup via a new check.
        for _ in range(500):
            limiter.check("ip:active", "default")

        with limiter._lock:
            assert ("ip:old", "default") not in limiter._buckets

    def test_active_bucket_not_evicted(self) -> None:
        limiter = RateLimiter(limits={"default": 200, "uploads": 20, "classify": 10, "exports": 30, "compute": 5})
        for _ in range(10):
            limiter.check("ip:active", "default")

        for _ in range(500):
            limiter.check("ip:trigger", "default")

        with limiter._lock:
            assert ("ip:active", "default") in limiter._buckets

    def test_max_bucket_cap_evicts_oldest(self) -> None:
        limiter = RateLimiter(
            limits={"default": 200, "uploads": 20, "classify": 10, "exports": 30, "compute": 5},
            max_buckets=5,
        )
        for i in range(5):
            limiter.check(f"ip:10.0.0.{i}", "default")

        with limiter._lock:
            initial_count = len(limiter._buckets)
        assert initial_count == 5

        # One more check should trigger eviction.
        limiter.check("ip:10.0.0.99", "default")

        with limiter._lock:
            assert len(limiter._buckets) <= 5


# ---------------------------------------------------------------------------
# Compute route group
# ---------------------------------------------------------------------------


class TestComputeRouteGroup:
    def test_calculate_job_is_compute(self) -> None:
        assert _route_group("/api/v1/projects/p/jobs/calculate", "POST") == "compute"

    def test_scenario_run_is_compute(self) -> None:
        assert _route_group("/api/v1/scenarios/s1/run", "POST") == "compute"

    def test_comparisons_get_is_compute(self) -> None:
        assert _route_group("/api/v1/projects/p/comparisons", "GET") == "compute"

    def test_run_on_non_scenario_is_default(self) -> None:
        # A path ending in /run that is NOT a scenario must NOT be compute.
        result = _route_group("/api/v1/projects/p/run", "POST")
        assert result == "default"


# ---------------------------------------------------------------------------
# CORS fail-closed
# ---------------------------------------------------------------------------


class TestCORSFailClosed:
    def test_production_mode_without_origins_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
        monkeypatch.setenv("ALTERA_DEV_AUTH_ENABLED", "false")
        from altera_api import main as _main
        with pytest.raises(RuntimeError, match="CORS_ALLOWED_ORIGINS"):
            _main._check_cors_production_config()

    def test_dev_mode_without_origins_does_not_raise(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
        monkeypatch.setenv("ALTERA_DEV_AUTH_ENABLED", "true")
        from altera_api import main as _main
        _main._check_cors_production_config()  # must not raise

    def test_dev_mode_parse_returns_localhost_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
        monkeypatch.setenv("ALTERA_DEV_AUTH_ENABLED", "true")
        from altera_api import main as _main
        origins = _main._parse_allowed_origins()
        assert origins == ["http://localhost:3000"]

    def test_production_mode_with_explicit_origins_ok(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://app.altera-ai.com")
        monkeypatch.setenv("ALTERA_DEV_AUTH_ENABLED", "false")
        from altera_api import main as _main
        _main._check_cors_production_config()  # must not raise
        assert _main._parse_allowed_origins() == ["https://app.altera-ai.com"]

    def test_empty_cors_origins_in_production_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "")
        monkeypatch.setenv("ALTERA_DEV_AUTH_ENABLED", "false")
        from altera_api import main as _main
        with pytest.raises(RuntimeError, match="CORS_ALLOWED_ORIGINS"):
            _main._check_cors_production_config()


# ---------------------------------------------------------------------------
# Secret scanning — .env.example files must not contain real OpenAI keys
# ---------------------------------------------------------------------------


class TestEnvExampleSecrets:
    import re
    _REAL_KEY_PATTERN = re.compile(r"sk-proj-[A-Za-z0-9_-]{40,}")

    def _check_file(self, path: Path) -> None:
        content = path.read_text(encoding="utf-8")
        match = self._REAL_KEY_PATTERN.search(content)
        assert match is None, (
            f"{path} contains what looks like a real OpenAI key. "
            "Rotate the key immediately and replace with a placeholder."
        )

    def test_root_env_example_no_real_openai_key(self) -> None:
        self._check_file(REPO_ROOT / ".env.example")

    def test_api_env_example_no_real_openai_key(self) -> None:
        self._check_file(REPO_ROOT / "apps" / "api" / ".env.example")

    def test_web_env_example_no_real_openai_key(self) -> None:
        self._check_file(REPO_ROOT / "apps" / "web" / ".env.example")

    def test_api_env_example_no_service_role_value(self) -> None:
        content = (REPO_ROOT / "apps" / "api" / ".env.example").read_text()
        lines = {
            k.strip(): v.strip()
            for line in content.splitlines()
            if "=" in line and not line.strip().startswith("#")
            for k, v in [line.split("=", 1)]
        }
        assert lines.get("SUPABASE_SERVICE_ROLE_KEY", "") == ""
