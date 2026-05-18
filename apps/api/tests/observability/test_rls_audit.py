"""Startup RLS audit check.

Scans the Supabase migration files (the source of truth for schema) to
assert that every multi-tenant table:

1. Has ``ENABLE ROW LEVEL SECURITY`` (or ``enable row level security``).
2. Has at least one ``CREATE POLICY`` statement.

This test runs entirely from SQL source and requires no live database.
It fails fast when someone adds a new table without adding RLS policies,
catching the omission before it reaches production.
"""

from __future__ import annotations

import pathlib
import re

_MIGRATIONS_DIR = pathlib.Path(__file__).parents[4] / "supabase" / "migrations"

# Tables that must have RLS enabled and at least one policy defined.
_REQUIRED_RLS_TABLES = {
    "organisations",
    "memberships",
    "projects",
    "uploads",
    "products",
    "product_composite_ingredients",
    "classifications",
    "classification_events",
    "manual_reviews",
    "calculation_runs",
    "calculation_rows",
    "audit_events",
    "report_exports",
    "jobs",
}


def _load_all_migrations() -> str:
    """Concatenate all .sql files in the migrations directory."""
    sqls = sorted(_MIGRATIONS_DIR.glob("*.sql"))
    assert sqls, f"No migration files found in {_MIGRATIONS_DIR}"
    return "\n".join(f.read_text() for f in sqls).lower()


def test_rls_enabled_on_all_required_tables() -> None:
    sql = _load_all_migrations()
    missing: list[str] = []
    for table in sorted(_REQUIRED_RLS_TABLES):
        pattern = rf"alter\s+table\s+(?:public\.)?{re.escape(table)}\s+enable\s+row\s+level\s+security"
        if not re.search(pattern, sql):
            missing.append(table)
    assert not missing, (
        f"RLS not enabled on table(s): {', '.join(missing)}. "
        "Add ALTER TABLE <table> ENABLE ROW LEVEL SECURITY; to the relevant migration."
    )


def test_policies_defined_on_all_required_tables() -> None:
    sql = _load_all_migrations()
    missing: list[str] = []
    for table in sorted(_REQUIRED_RLS_TABLES):
        # CREATE POLICY <name> ON <table> or ON public.<table>
        pattern = rf"create\s+policy\s+\w+\s+on\s+(?:public\.)?{re.escape(table)}"
        if not re.search(pattern, sql):
            missing.append(table)
    assert not missing, (
        f"No RLS policy found for table(s): {', '.join(missing)}. "
        "Add at least one CREATE POLICY statement for each multi-tenant table."
    )
