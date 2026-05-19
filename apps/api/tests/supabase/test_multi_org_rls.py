"""Phase 30 RLS fix — regression tests for multi-org scalar subquery bug.

visible_organisation_ids() previously used a CASE expression:

    select case
      when public.current_user_is_altera() then
        (select id from public.organisations)        -- scalar ← BREAKS with 2+ orgs
      else
        (select organisation_id from public.memberships ...)  -- scalar ← BREAKS with 2+ memberships
    end

After Phase 32A/32B, multiple organisations exist in staging (Altera internal
+ client orgs). The THEN branch returned N rows into a scalar context →
"more than one row returned by a subquery used as an expression" on every
project/dashboard load for Altera users.

These tests scan migration SQL so they catch regressions without a live DB.
"""

from __future__ import annotations

import pathlib
import re

_MIGRATIONS_DIR = pathlib.Path(__file__).parents[4] / "supabase" / "migrations"


def _all_migrations() -> str:
    sqls = sorted(_MIGRATIONS_DIR.glob("*.sql"))
    assert sqls, f"No migration files found in {_MIGRATIONS_DIR}"
    return "\n".join(f.read_text() for f in sqls)


def _last_function_body(sql: str, fn_name: str) -> str:
    """Return the body of the last definition of a zero-arg SQL function."""
    # Match: everything between the outer $$ delimiters of the last occurrence
    pattern = (
        rf"create or replace function public\.{re.escape(fn_name)}\(\)"
        r".*?\$\$(.*?)\$\$"
    )
    matches = re.findall(pattern, sql, re.DOTALL | re.IGNORECASE)
    assert matches, f"Function public.{fn_name}() not found in migrations"
    return matches[-1]


def _last_policy_block(sql: str, policy_name: str) -> str:
    """Return the last CREATE POLICY block for the given policy name."""
    pattern = (
        rf"create policy {re.escape(policy_name)}\s+on\s+\S+.*?;"
    )
    matches = re.findall(pattern, sql, re.DOTALL | re.IGNORECASE)
    assert matches, f"Policy {policy_name!r} not found in migrations"
    return matches[-1]


# ---------------------------------------------------------------------------
# visible_organisation_ids() — must NOT use CASE expression
# ---------------------------------------------------------------------------


class TestVisibleOrgIdsFunction:
    def test_no_case_expression(self) -> None:
        """CASE expression breaks when >1 organisation exists (scalar subquery error)."""
        body = _last_function_body(_all_migrations(), "visible_organisation_ids")
        # Strip SQL comments before checking
        body_no_comments = re.sub(r"--[^\n]*", "", body)
        assert "case" not in body_no_comments.lower(), (
            "visible_organisation_ids uses CASE — this breaks with multiple "
            "organisations: 'more than one row returned by a subquery used as an expression'"
        )

    def test_no_scalar_subquery_in_then_branch(self) -> None:
        """The old broken form had '(select id from organisations)' as a CASE THEN value."""
        body = _last_function_body(_all_migrations(), "visible_organisation_ids")
        # The dangerous pattern is a subquery wrapped in parens used as a CASE return
        # Heuristic: THEN keyword immediately followed (after whitespace) by a subquery
        assert not re.search(r"\bthen\s*\(select\b", body, re.IGNORECASE), (
            "visible_organisation_ids has a THEN branch returning a scalar subquery"
        )

    def test_uses_where_or_exists(self) -> None:
        """Fixed form uses set-based WHERE/EXISTS, not CASE."""
        body = _last_function_body(_all_migrations(), "visible_organisation_ids")
        body_no_comments = re.sub(r"--[^\n]*", "", body)
        has_where = "where" in body_no_comments.lower()
        has_exists = "exists" in body_no_comments.lower()
        assert has_where or has_exists, (
            "visible_organisation_ids should use WHERE or EXISTS for set-based logic"
        )

    def test_altera_branch_covers_all_orgs(self) -> None:
        """Altera users must see all organisations (not just their own memberships)."""
        body = _last_function_body(_all_migrations(), "visible_organisation_ids")
        # The fixed form selects from organisations WHERE current_user_is_altera()
        # so all org IDs are included for Altera users.
        assert "current_user_is_altera" in body.lower(), (
            "visible_organisation_ids should use current_user_is_altera() "
            "to determine visibility scope for Altera staff"
        )

    def test_membership_check_for_clients(self) -> None:
        """Client users must be restricted to orgs they are members of."""
        body = _last_function_body(_all_migrations(), "visible_organisation_ids")
        assert "memberships" in body.lower(), (
            "visible_organisation_ids should check memberships table for non-Altera users"
        )


# ---------------------------------------------------------------------------
# report_exports_update — must NOT use limit 1 scalar subquery
# ---------------------------------------------------------------------------


class TestReportExportsUpdatePolicy:
    def test_no_limit_1_scalar_for_role(self) -> None:
        """Old policy used (select organisation_id … limit 1) — arbitrary for multi-org users."""
        block = _last_policy_block(_all_migrations(), "report_exports_update")
        # limit 1 within the policy is the smell; the fixed form removes it
        assert "limit 1" not in block.lower(), (
            "report_exports_update uses 'limit 1' to pick an arbitrary membership — "
            "this silently breaks role checks for Altera users with multiple memberships"
        )

    def test_uses_current_user_is_altera(self) -> None:
        """Fixed policy gates on current_user_is_altera(), not a fragile role string lookup."""
        block = _last_policy_block(_all_migrations(), "report_exports_update")
        assert "current_user_is_altera" in block.lower(), (
            "report_exports_update should use current_user_is_altera() instead of "
            "user_role_in((select … limit 1))"
        )
