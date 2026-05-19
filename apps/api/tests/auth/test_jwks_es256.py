"""Verifier tests for the ES256 / JWKS code path.

These tests cover the post-Phase-14 Supabase signing scheme where
the project issues asymmetric (ES256) access tokens whose public keys
live behind a JWKS endpoint. We don't reach out to Supabase — we
generate a keypair locally, swap in a stub JWKS client, and feed
tokens we minted ourselves.
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from altera_api.auth import verifier as verifier_module
from altera_api.auth.errors import InvalidTokenError
from altera_api.auth.verifier import verify_supabase_jwt
from tests.auth.conftest import TEST_JWT_SECRET

STAGING_SUPABASE_URL = "https://staging.supabase.co"
TEST_KID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def ec_keypair() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


@pytest.fixture
def stub_jwks(
    monkeypatch: pytest.MonkeyPatch,
    ec_keypair: ec.EllipticCurvePrivateKey,
) -> Iterator[ec.EllipticCurvePublicKey]:
    """Replace ``_get_jwks_client`` with a stub that returns our fixture key."""
    public_key = ec_keypair.public_key()

    class _FakeSigningKey:
        def __init__(self, key: ec.EllipticCurvePublicKey) -> None:
            self.key = key

    class _FakeJWKSClient:
        def get_signing_key_from_jwt(self, _token: str) -> _FakeSigningKey:
            return _FakeSigningKey(public_key)

    def _stub(_supabase_url: str, _anon_key: str | None) -> _FakeJWKSClient:
        return _FakeJWKSClient()

    verifier_module._jwks_clients.clear()
    monkeypatch.setattr(verifier_module, "_get_jwks_client", _stub)
    yield public_key
    verifier_module._jwks_clients.clear()


def _mint_es256(
    ec_keypair: ec.EllipticCurvePrivateKey,
    *,
    sub: str | None = None,
    audience: str = "authenticated",
    issuer: str | None = None,
    expires_in_seconds: int = 3600,
    kid: str = TEST_KID,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "sub": sub or str(uuid4()),
        "aud": audience,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_in_seconds)).timestamp()),
        "role": "authenticated",
    }
    if issuer is not None:
        claims["iss"] = issuer
    if extra_claims:
        claims.update(extra_claims)
    return jwt.encode(
        claims,
        ec_keypair,
        algorithm="ES256",
        headers={"kid": kid, "typ": "JWT"},
    )


class TestES256JWKSVerification:
    def test_valid_es256_token_accepted(
        self,
        monkeypatch: pytest.MonkeyPatch,
        ec_keypair: ec.EllipticCurvePrivateKey,
        stub_jwks: ec.EllipticCurvePublicKey,  # noqa: ARG002 — fixture installs stub
    ) -> None:
        monkeypatch.setenv("SUPABASE_URL", STAGING_SUPABASE_URL)
        token = _mint_es256(
            ec_keypair, issuer=f"{STAGING_SUPABASE_URL}/auth/v1"
        )
        claims = verify_supabase_jwt(token)
        assert claims["aud"] == "authenticated"
        assert claims["iss"] == f"{STAGING_SUPABASE_URL}/auth/v1"

    def test_wrong_issuer_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        ec_keypair: ec.EllipticCurvePrivateKey,
        stub_jwks: ec.EllipticCurvePublicKey,  # noqa: ARG002
    ) -> None:
        monkeypatch.setenv("SUPABASE_URL", STAGING_SUPABASE_URL)
        token = _mint_es256(
            ec_keypair, issuer="https://attacker.example.com/auth/v1"
        )
        with pytest.raises(InvalidTokenError, match="issuer"):
            verify_supabase_jwt(token)

    def test_supabase_url_trailing_slash_tolerated(
        self,
        monkeypatch: pytest.MonkeyPatch,
        ec_keypair: ec.EllipticCurvePrivateKey,
        stub_jwks: ec.EllipticCurvePublicKey,  # noqa: ARG002
    ) -> None:
        monkeypatch.setenv("SUPABASE_URL", f"{STAGING_SUPABASE_URL}/")
        token = _mint_es256(
            ec_keypair, issuer=f"{STAGING_SUPABASE_URL}/auth/v1"
        )
        claims = verify_supabase_jwt(token)
        assert claims["aud"] == "authenticated"

    def test_es256_without_supabase_url_rejected(
        self,
        ec_keypair: ec.EllipticCurvePrivateKey,
        stub_jwks: ec.EllipticCurvePublicKey,  # noqa: ARG002
    ) -> None:
        token = _mint_es256(ec_keypair)
        with pytest.raises(InvalidTokenError, match="SUPABASE_URL"):
            verify_supabase_jwt(token)

    def test_unsupported_alg_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SUPABASE_URL", STAGING_SUPABASE_URL)

        def b64u(s: str) -> str:
            return base64.urlsafe_b64encode(s.encode()).rstrip(b"=").decode()

        header = '{"alg":"none","typ":"JWT"}'
        body = '{"sub":"x","aud":"authenticated","exp":9999999999}'
        token = f"{b64u(header)}.{b64u(body)}."
        with pytest.raises(InvalidTokenError, match="unsupported alg"):
            verify_supabase_jwt(token)


class TestHS256StillWorks:
    """The legacy HS256 path must still verify when SUPABASE_JWT_SECRET is set."""

    def test_valid_hs256_token_accepted_via_me_endpoint(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        mint_token: Callable[..., str],
    ) -> None:
        monkeypatch.setenv("SUPABASE_JWT_SECRET", TEST_JWT_SECRET)
        user_id = uuid4()
        token = mint_token(sub=user_id, email="legacy@test.local")
        r = client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200, r.text
        assert r.json()["user_id"] == str(user_id)


def test_no_token_returns_authentication_required(client: TestClient) -> None:
    r = client.get("/api/v1/me")
    assert r.status_code == 401
    assert r.json()["detail"] == "authentication required"


class TestJWKSClientConstruction:
    """Verify ``_get_jwks_client`` configures PyJWKClient with the Supabase
    apikey header and caches per (url, anon-key) pair."""

    def test_anon_key_passed_as_apikey_header(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: dict[str, Any] = {}

        class _SpyClient:
            def __init__(self, uri: str, **kwargs: Any) -> None:
                captured["uri"] = uri
                captured["headers"] = kwargs.get("headers")
                captured["lifespan"] = kwargs.get("lifespan")

        verifier_module._jwks_clients.clear()
        monkeypatch.setattr(verifier_module, "PyJWKClient", _SpyClient)

        verifier_module._get_jwks_client(STAGING_SUPABASE_URL, "sb_anon_abc")

        assert captured["uri"] == (
            f"{STAGING_SUPABASE_URL}/auth/v1/.well-known/jwks.json"
        )
        assert captured["headers"] == {"apikey": "sb_anon_abc"}
        assert captured["lifespan"] == 600
        verifier_module._jwks_clients.clear()

    def test_no_apikey_header_when_anon_key_missing(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: dict[str, Any] = {}

        class _SpyClient:
            def __init__(self, _uri: str, **kwargs: Any) -> None:
                captured["headers"] = kwargs.get("headers")

        verifier_module._jwks_clients.clear()
        monkeypatch.setattr(verifier_module, "PyJWKClient", _SpyClient)

        verifier_module._get_jwks_client(STAGING_SUPABASE_URL, None)
        assert captured["headers"] is None
        verifier_module._jwks_clients.clear()

    def test_client_cached_across_calls(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        construct_count = 0

        class _CountingClient:
            def __init__(self, _uri: str, **_kwargs: Any) -> None:
                nonlocal construct_count
                construct_count += 1

        verifier_module._jwks_clients.clear()
        monkeypatch.setattr(verifier_module, "PyJWKClient", _CountingClient)

        verifier_module._get_jwks_client(STAGING_SUPABASE_URL, "sb_anon_abc")
        verifier_module._get_jwks_client(STAGING_SUPABASE_URL, "sb_anon_abc")
        assert construct_count == 1

        # Different anon key → different cache entry → reconstruct.
        verifier_module._get_jwks_client(STAGING_SUPABASE_URL, "sb_anon_xyz999")
        assert construct_count == 2
        verifier_module._jwks_clients.clear()

    def test_jwks_fetch_failure_surfaces_clear_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        ec_keypair: ec.EllipticCurvePrivateKey,
    ) -> None:
        from jwt.exceptions import PyJWKClientError

        class _FailingClient:
            def __init__(self, *_args: Any, **_kwargs: Any) -> None:
                pass

            def get_signing_key_from_jwt(self, _token: str) -> Any:
                raise PyJWKClientError(
                    'Fail to fetch data from the url, err: '
                    '"HTTP Error 401: Unauthorized"'
                )

        def _failing_factory(
            _url: str, _key: str | None
        ) -> _FailingClient:
            return _FailingClient()

        verifier_module._jwks_clients.clear()
        monkeypatch.setattr(verifier_module, "_get_jwks_client", _failing_factory)
        monkeypatch.setenv("SUPABASE_URL", STAGING_SUPABASE_URL)
        token = _mint_es256(
            ec_keypair, issuer=f"{STAGING_SUPABASE_URL}/auth/v1"
        )
        with pytest.raises(
            InvalidTokenError, match="could not resolve signing key"
        ):
            verify_supabase_jwt(token)
        verifier_module._jwks_clients.clear()
