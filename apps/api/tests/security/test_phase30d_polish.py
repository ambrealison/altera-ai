"""Phase 30D — security polish tests.

Covers:
- Malformed X-Forwarded-For from a trusted proxy falls back to peer IP
- OrderedDict LRU: most-recently-used bucket is NOT evicted under cap pressure
- OrderedDict LRU: least-recently-used bucket IS evicted under cap pressure
- Gitleaks config has no file-level path allowlist (no global .env.example exemption)
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

from altera_api.ratelimit import (
    RateLimiter,
    _extract_ip,
    _parse_trusted_proxies,
)

REPO_ROOT = Path(__file__).resolve().parents[4]


# ---------------------------------------------------------------------------
# XFF validation — malformed first-hop falls back to peer
# ---------------------------------------------------------------------------


class TestExtractIpMalformedXFF:
    def _fake_request(
        self,
        forwarded_for: str | None,
        client_host: str,
    ):
        r = MagicMock()
        headers: dict[str, str] = {}
        if forwarded_for is not None:
            headers["x-forwarded-for"] = forwarded_for
        r.headers = headers
        r.client = MagicMock()
        r.client.host = client_host
        return r

    def test_malformed_xff_falls_back_to_peer(self) -> None:
        nets = _parse_trusted_proxies("10.0.0.1")
        req = self._fake_request(forwarded_for="not-an-ip", client_host="10.0.0.1")
        key = _extract_ip(req, nets)
        assert key == "ip:10.0.0.1"

    def test_injected_xff_with_spaces_falls_back(self) -> None:
        nets = _parse_trusted_proxies("10.0.0.1")
        req = self._fake_request(
            forwarded_for="evil.host/path, 1.2.3.4", client_host="10.0.0.1"
        )
        key = _extract_ip(req, nets)
        # "evil.host/path" is not a valid IP address; fall back to peer
        assert key == "ip:10.0.0.1"

    def test_valid_xff_still_used_when_trusted(self) -> None:
        nets = _parse_trusted_proxies("10.0.0.1")
        req = self._fake_request(forwarded_for="203.0.113.7", client_host="10.0.0.1")
        key = _extract_ip(req, nets)
        assert key == "ip:203.0.113.7"

    def test_empty_xff_after_split_strip_falls_back(self) -> None:
        nets = _parse_trusted_proxies("10.0.0.1")
        req = self._fake_request(forwarded_for="  ", client_host="10.0.0.1")
        key = _extract_ip(req, nets)
        assert key == "ip:10.0.0.1"


# ---------------------------------------------------------------------------
# OrderedDict LRU eviction — MRU bucket is protected; LRU bucket is evicted
# ---------------------------------------------------------------------------


class TestOrderedDictEviction:
    def _limiter(self, cap: int) -> RateLimiter:
        return RateLimiter(
            limits={"default": 200, "uploads": 20, "classify": 10, "exports": 30, "compute": 5},
            max_buckets=cap,
        )

    def test_most_recently_used_bucket_survives_cap_eviction(self) -> None:
        limiter = self._limiter(cap=3)

        # Fill to cap with three distinct keys.
        limiter.check("ip:1.1.1.1", "default")
        limiter.check("ip:2.2.2.2", "default")
        limiter.check("ip:3.3.3.3", "default")

        # Touch ip:1.1.1.1 again so it becomes the MRU bucket.
        limiter.check("ip:1.1.1.1", "default")

        # Adding a 4th key should evict ip:2.2.2.2 (now LRU), not ip:1.1.1.1.
        limiter.check("ip:4.4.4.4", "default")

        with limiter._lock:
            keys = set(limiter._buckets.keys())

        assert ("ip:1.1.1.1", "default") in keys, "MRU bucket must not be evicted"
        assert ("ip:2.2.2.2", "default") not in keys, "LRU bucket must be evicted"

    def test_least_recently_used_bucket_is_evicted(self) -> None:
        limiter = self._limiter(cap=2)

        limiter.check("ip:old", "default")   # first; becomes LRU after next check
        limiter.check("ip:new", "default")   # second; MRU

        # Adding a third key triggers eviction of ip:old.
        limiter.check("ip:newest", "default")

        with limiter._lock:
            keys = set(limiter._buckets.keys())

        assert ("ip:old", "default") not in keys
        assert ("ip:new", "default") in keys
        assert ("ip:newest", "default") in keys

    def test_bucket_count_does_not_exceed_cap(self) -> None:
        cap = 4
        limiter = self._limiter(cap=cap)
        for i in range(10):
            limiter.check(f"ip:10.0.0.{i}", "default")
        with limiter._lock:
            assert len(limiter._buckets) <= cap


# ---------------------------------------------------------------------------
# Gitleaks config: no global file-level path allowlist
# ---------------------------------------------------------------------------


class TestGitleaksConfig:
    def test_no_global_path_allowlist(self) -> None:
        """The top-level [allowlist] section must not exempt .env.example files.

        A global paths allowlist disables ALL rules for the listed files — the
        exact files where a leaked key was committed.  Only per-rule allowlists
        (under [[rules]]) are permitted.
        """
        config_path = REPO_ROOT / ".gitleaks.toml"
        assert config_path.exists(), ".gitleaks.toml must exist at repo root"
        content = config_path.read_text(encoding="utf-8")

        # Look for a bare [allowlist] section (not nested under [rules]).
        # A nested rules.allowlist looks like "[rules.allowlist]" or
        # "  [rules.allowlist]" — we only care about the top-level one.
        bare_allowlist = re.search(r"^\[allowlist\]", content, re.MULTILINE)
        assert bare_allowlist is None, (
            ".gitleaks.toml must not contain a top-level [allowlist] section — "
            "it would exempt entire files from all secret scanning rules.  "
            "Use per-rule [rules.allowlist] sections instead."
        )

    def test_env_example_paths_not_in_any_allowlist(self) -> None:
        """No allowlist block should list .env.example as a path to skip."""
        config_path = REPO_ROOT / ".gitleaks.toml"
        content = config_path.read_text(encoding="utf-8")
        assert ".env.example" not in content or _only_in_comments(content, ".env.example"), (
            ".env.example must not appear in any gitleaks allowlist paths list"
        )


def _only_in_comments(content: str, needle: str) -> bool:
    """Return True if every line containing `needle` is a TOML comment."""
    for line in content.splitlines():
        if needle in line and not line.strip().startswith("#"):
            return False
    return True
