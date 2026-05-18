"""Unit tests for scripts/bootstrap_altera_admin.py.

No live Supabase connection required — all database calls are mocked.
"""
from __future__ import annotations

import argparse

# Add scripts/ to sys.path so the module can be imported directly.
import pathlib
import sys
from typing import Any
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "scripts"))

from bootstrap_altera_admin import (  # noqa: E402
    _validate_slug,
    _validate_uuid,
    run,
    upsert_membership,
    upsert_organisation,
    upsert_user_profile,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ORG_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_USER_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
_EMAIL = "admin@altera-ai.local"


def _mock_client(existing_org_id: str | None = None) -> MagicMock:
    """Build a mock Supabase client with per-table tracking.

    When existing_org_id is set, the organisations.select().eq().execute()
    chain returns that ID (simulates an existing org).

    Each table name gets its own MagicMock so call assertions on
    client.table("user_profiles").upsert are independent of
    client.table("memberships").upsert.
    """
    client = MagicMock()
    _tables: dict[str, MagicMock] = {}

    def _get_table(name: str) -> MagicMock:
        if name not in _tables:
            t = MagicMock()
            result = MagicMock()
            result.data = [{"id": existing_org_id}] if (
                name == "organisations" and existing_org_id
            ) else []
            t.select.return_value.eq.return_value.execute.return_value = result
            t.insert.return_value.execute.return_value = MagicMock(data=[])
            t.upsert.return_value.execute.return_value = MagicMock(data=[])
            _tables[name] = t
        return _tables[name]

    client.table.side_effect = _get_table
    return client


def _args(**overrides: Any) -> argparse.Namespace:
    """Return a populated Namespace for run() tests."""
    defaults = dict(
        org_name="Altera AI",
        org_slug="altera-ai",
        org_id=None,
        user_id=_USER_ID,
        email=_EMAIL,
        display_name=None,
        confirm=True,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


# ---------------------------------------------------------------------------
# _validate_slug
# ---------------------------------------------------------------------------


class TestValidateSlug:
    def test_valid_simple(self) -> None:
        assert _validate_slug("altera-ai") == "altera-ai"

    def test_valid_single_word(self) -> None:
        assert _validate_slug("altera") == "altera"

    def test_valid_numbers(self) -> None:
        assert _validate_slug("altera-ai-2") == "altera-ai-2"

    def test_rejects_uppercase(self) -> None:
        with pytest.raises(ValueError, match="Invalid slug"):
            _validate_slug("Altera-AI")

    def test_rejects_spaces(self) -> None:
        with pytest.raises(ValueError, match="Invalid slug"):
            _validate_slug("altera ai")

    def test_rejects_double_hyphen(self) -> None:
        with pytest.raises(ValueError, match="Invalid slug"):
            _validate_slug("altera--ai")

    def test_rejects_leading_hyphen(self) -> None:
        with pytest.raises(ValueError, match="Invalid slug"):
            _validate_slug("-altera")

    def test_rejects_too_long(self) -> None:
        with pytest.raises(ValueError, match="too long"):
            _validate_slug("a" * 81)


# ---------------------------------------------------------------------------
# _validate_uuid
# ---------------------------------------------------------------------------


class TestValidateUuid:
    def test_valid_uuid(self) -> None:
        assert _validate_uuid(_USER_ID, "--user-id") == _USER_ID

    def test_rejects_non_uuid(self) -> None:
        with pytest.raises(ValueError, match="Invalid UUID"):
            _validate_uuid("not-a-uuid", "--user-id")

    def test_rejects_empty(self) -> None:
        with pytest.raises(ValueError, match="Invalid UUID"):
            _validate_uuid("", "--user-id")


# ---------------------------------------------------------------------------
# upsert_organisation
# ---------------------------------------------------------------------------


class TestUpsertOrganisation:
    def test_creates_new_org_when_not_found(self) -> None:
        client = _mock_client(existing_org_id=None)
        org_id, created = upsert_organisation(client, "Altera AI", "altera-ai")
        assert created is True
        assert len(org_id) == 36  # UUID format
        client.table("organisations").insert.assert_called_once()

    def test_returns_existing_org_without_insert(self) -> None:
        client = _mock_client(existing_org_id=_ORG_ID)
        org_id, created = upsert_organisation(client, "Altera AI", "altera-ai")
        assert org_id == _ORG_ID
        assert created is False
        client.table("organisations").insert.assert_not_called()

    def test_uses_provided_org_id(self) -> None:
        client = _mock_client(existing_org_id=None)
        pinned_id = "cccccccc-cccc-cccc-cccc-cccccccccccc"
        org_id, _ = upsert_organisation(client, "Altera AI", "altera-ai", org_id=pinned_id)
        assert org_id == pinned_id

    def test_inserts_altera_internal_type(self) -> None:
        client = _mock_client(existing_org_id=None)
        upsert_organisation(client, "Altera AI", "altera-ai")
        insert_args = client.table("organisations").insert.call_args[0][0]
        assert insert_args["organisation_type"] == "altera_internal"

    def test_idempotent_on_second_call(self) -> None:
        """Calling twice with the same slug returns the same ID, inserts only once."""
        first_client = _mock_client(existing_org_id=None)
        org_id, _ = upsert_organisation(first_client, "Altera AI", "altera-ai")

        # Second call: simulate org now exists.
        second_client = _mock_client(existing_org_id=org_id)
        returned_id, created = upsert_organisation(second_client, "Altera AI", "altera-ai")
        assert returned_id == org_id
        assert created is False
        second_client.table("organisations").insert.assert_not_called()


# ---------------------------------------------------------------------------
# upsert_user_profile
# ---------------------------------------------------------------------------


class TestUpsertUserProfile:
    def test_calls_upsert_with_correct_fields(self) -> None:
        client = _mock_client()
        upsert_user_profile(client, _USER_ID, _EMAIL, "Admin User")
        upsert_call = client.table("user_profiles").upsert
        upsert_call.assert_called_once()
        data = upsert_call.call_args[0][0]
        assert data["user_id"] == _USER_ID
        assert data["email"] == _EMAIL
        assert data["display_name"] == "Admin User"
        assert "created_at" in data
        assert "updated_at" in data

    def test_on_conflict_is_user_id(self) -> None:
        client = _mock_client()
        upsert_user_profile(client, _USER_ID, _EMAIL, "Admin")
        kwargs = client.table("user_profiles").upsert.call_args[1]
        assert kwargs.get("on_conflict") == "user_id"

    def test_returns_true_on_success(self) -> None:
        client = _mock_client()
        assert upsert_user_profile(client, _USER_ID, _EMAIL, "Admin") is True


# ---------------------------------------------------------------------------
# upsert_membership
# ---------------------------------------------------------------------------


class TestUpsertMembership:
    def test_calls_upsert_with_correct_fields(self) -> None:
        client = _mock_client()
        upsert_membership(client, _USER_ID, _ORG_ID, "altera_admin")
        upsert_call = client.table("memberships").upsert
        upsert_call.assert_called_once()
        data = upsert_call.call_args[0][0]
        assert data["user_id"] == _USER_ID
        assert data["organisation_id"] == _ORG_ID
        assert data["role"] == "altera_admin"

    def test_on_conflict_is_composite_pk(self) -> None:
        client = _mock_client()
        upsert_membership(client, _USER_ID, _ORG_ID, "altera_admin")
        kwargs = client.table("memberships").upsert.call_args[1]
        assert kwargs.get("on_conflict") == "user_id,organisation_id"

    def test_returns_true_on_success(self) -> None:
        client = _mock_client()
        assert upsert_membership(client, _USER_ID, _ORG_ID, "altera_admin") is True


# ---------------------------------------------------------------------------
# run() — integration of the full flow
# ---------------------------------------------------------------------------


class TestRunSafetyGate:
    def test_missing_confirm_exits_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("BOOTSTRAP_CONFIRM", raising=False)
        with pytest.raises(SystemExit) as exc_info:
            run(_args(confirm=False))
        assert exc_info.value.code == 1

    def test_env_confirm_allows_run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BOOTSTRAP_CONFIRM", "true")
        client = _mock_client()
        run(_args(confirm=False), client=client)  # must not raise

    def test_invalid_slug_exits_1(self) -> None:
        with pytest.raises(SystemExit) as exc_info:
            run(_args(org_slug="Bad Slug!!"), client=_mock_client())
        assert exc_info.value.code == 1

    def test_invalid_user_id_exits_1(self) -> None:
        with pytest.raises(SystemExit) as exc_info:
            run(_args(user_id="not-a-uuid"), client=_mock_client())
        assert exc_info.value.code == 1


class TestRunFullFlow:
    def test_creates_org_profile_and_membership(self) -> None:
        client = _mock_client(existing_org_id=None)
        run(_args(), client=client)
        # organisations.insert called once
        client.table("organisations").insert.assert_called_once()
        # user_profiles.upsert called once
        client.table("user_profiles").upsert.assert_called_once()
        # memberships.upsert called once
        client.table("memberships").upsert.assert_called_once()

    def test_idempotent_existing_org(self) -> None:
        # Org already exists → insert not called; profile and membership still upserted.
        client = _mock_client(existing_org_id=_ORG_ID)
        run(_args(), client=client)
        client.table("organisations").insert.assert_not_called()
        client.table("user_profiles").upsert.assert_called_once()
        client.table("memberships").upsert.assert_called_once()

    def test_display_name_defaults_to_email_local_part(self) -> None:
        client = _mock_client()
        run(_args(display_name=None, email="jane@example.com"), client=client)
        profile_data = client.table("user_profiles").upsert.call_args[0][0]
        assert profile_data["display_name"] == "jane"

    def test_explicit_display_name_used(self) -> None:
        client = _mock_client()
        run(_args(display_name="Jane Smith", email="jane@example.com"), client=client)
        profile_data = client.table("user_profiles").upsert.call_args[0][0]
        assert profile_data["display_name"] == "Jane Smith"

    def test_db_error_exits_1(self) -> None:
        client = _mock_client()
        # Trigger the table mock into cache, then inject the failure.
        client.table("organisations").insert.return_value.execute.side_effect = RuntimeError(
            "db error"
        )
        with pytest.raises(SystemExit) as exc_info:
            run(_args(), client=client)
        assert exc_info.value.code == 1

    def test_output_does_not_contain_service_role_key(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "super-secret-key")
        client = _mock_client()
        run(_args(), client=client)
        out, err = capsys.readouterr()
        assert "super-secret-key" not in out
        assert "super-secret-key" not in err
