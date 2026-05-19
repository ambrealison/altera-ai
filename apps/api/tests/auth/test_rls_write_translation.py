"""Translate PostgREST RLS denials into clean 403 responses.

The route layer already enforces ``auth.can_write_data``; an RLS denial
therefore means a configuration drift (role allow-list out of date) or
a write that should not have reached the database. Either way the
client should see a structured 403, not a bare 500.

Also pins migration 0027's RLS extension to namespaced roles — a guard
against accidentally narrowing the allow-list back to the legacy
single-namespace set in a later refactor.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import APIRouter, status
from fastapi.testclient import TestClient
from postgrest.exceptions import APIError

from altera_api.main import app

MIGRATION_FILE = (
    Path(__file__).resolve().parents[4]
    / "supabase"
    / "migrations"
    / "0027_phase14b_write_role_namespaces.sql"
)


class TestPostgrestAPIErrorHandler:
    def test_rls_denial_translated_to_403(self) -> None:
        # Register a one-off route that raises a 42501 APIError. We don't
        # hit Supabase — we synthesise the exception PostgREST would have
        # raised.
        bomb_router = APIRouter()

        @bomb_router.get("/__rls_bomb")
        def _bomb() -> None:
            raise APIError(
                {
                    "message": 'new row violates row-level security policy for table "projects"',
                    "code": "42501",
                    "hint": None,
                    "details": None,
                }
            )

        app.include_router(bomb_router)
        try:
            with TestClient(app, raise_server_exceptions=False) as client:
                r = client.get("/__rls_bomb")
            assert r.status_code == status.HTTP_403_FORBIDDEN
            body = r.json()
            assert body["code"] == "rls_denied"
            assert "row-level security" in body["detail"].lower()
        finally:
            # Best-effort cleanup so the bomb route doesn't leak into the
            # rest of the suite.
            app.router.routes = [
                r
                for r in app.router.routes
                if getattr(r, "path", None) != "/__rls_bomb"
            ]

    def test_non_rls_postgrest_error_returns_500(self) -> None:
        bomb_router = APIRouter()

        @bomb_router.get("/__pg_bomb")
        def _bomb() -> None:
            raise APIError({"message": "connection refused", "code": "08006"})

        app.include_router(bomb_router)
        try:
            with TestClient(app, raise_server_exceptions=False) as client:
                r = client.get("/__pg_bomb")
            assert r.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
            body = r.json()
            assert body["code"] == "08006"
            assert body["detail"] == "connection refused"
        finally:
            app.router.routes = [
                r
                for r in app.router.routes
                if getattr(r, "path", None) != "/__pg_bomb"
            ]


class TestWriteRoleMigrationShape:
    """Smoke checks on the SQL — we can't run the migration without a live
    Postgres, but we can pin the role allow-list so it doesn't silently
    regress to legacy-only."""

    @pytest.fixture(scope="class")
    def sql(self) -> str:
        return MIGRATION_FILE.read_text()

    def test_migration_file_present(self) -> None:
        assert MIGRATION_FILE.exists(), MIGRATION_FILE

    def test_helper_functions_defined(self, sql: str) -> None:
        assert "create or replace function public.user_role_can_write_org_data" in sql
        assert "create or replace function public.user_role_can_review_org_data" in sql
        assert "create or replace function public.user_role_can_admin_org" in sql

    def _helper_body(self, sql: str, signature: str) -> str:
        start = sql.index(signature)
        # Skip past the opening dollar-quote, then take everything up to
        # the closing dollar-quote.
        body_open = sql.index("$$", start) + 2
        body_close = sql.index("$$", body_open)
        return sql[body_open:body_close]

    def test_write_helper_includes_namespaced_roles(self, sql: str) -> None:
        body = self._helper_body(sql, "user_role_can_write_org_data(org uuid)")
        for required in (
            "'altera_admin'",
            "'altera_analyst'",
            "'altera_methodology_lead'",
            "'client_owner'",
            "'client_admin'",
        ):
            assert required in body, f"{required} missing from write-tier helper"

    def test_review_helper_includes_altera_reviewer(self, sql: str) -> None:
        body = self._helper_body(sql, "user_role_can_review_org_data(org uuid)")
        assert "'altera_reviewer'" in body
        assert "'reviewer'" in body  # legacy retained

    def test_projects_insert_policy_uses_helper(self, sql: str) -> None:
        # The whole point: the projects insert policy must now route
        # through the namespaced-aware helper, not the legacy allow-list.
        idx = sql.index("create policy projects_insert on public.projects")
        body = sql[idx : idx + 400]
        assert "user_role_can_write_org_data" in body
