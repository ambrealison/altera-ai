"""Sanity checks on the Supabase migrations.

We don't run them against a real Postgres in CI (that requires a
running Supabase stack); but we do verify the migration files exist,
parse as SQL-ish, and collectively declare every table the Phase 13A
spec calls for. Catching a missing table or a typo'd RLS policy here
is cheap — the behavioural contract is ``pg_prove`` against the local
Supabase, documented in ``supabase/README.md``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
MIGRATIONS_DIR = REPO_ROOT / "supabase" / "migrations"
RLS_TESTS_DIR = REPO_ROOT / "supabase" / "tests" / "rls"
SEED_FILE = REPO_ROOT / "supabase" / "seed.sql"
SUPABASE_README = REPO_ROOT / "supabase" / "README.md"


def _all_migrations() -> list[Path]:
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    assert files, f"no migration files under {MIGRATIONS_DIR}"
    return files


def _all_migration_sql() -> str:
    return "\n".join(p.read_text() for p in _all_migrations())


def test_migrations_dir_exists() -> None:
    assert MIGRATIONS_DIR.is_dir()


def test_migration_filenames_are_numbered() -> None:
    pattern = re.compile(r"^\d{4}_[a-z0-9_]+\.sql$")
    for path in _all_migrations():
        assert pattern.match(path.name), f"unexpected migration filename: {path.name}"


def test_migration_filenames_strictly_increasing() -> None:
    files = _all_migrations()
    prefixes = [int(p.name.split("_", 1)[0]) for p in files]
    assert prefixes == sorted(prefixes)
    assert len(set(prefixes)) == len(prefixes), "duplicate migration prefix"


@pytest.mark.parametrize(
    "expected_table",
    [
        # Tenancy + identity
        "public.organisations",
        "public.memberships",
        "public.reserved_slugs",
        "public.user_profiles",
        # Project pipeline
        "public.projects",
        "public.uploads",
        "public.products",
        "public.product_composite_ingredients",
        "public.classifications",
        "public.classification_events",
        "public.manual_reviews",
        "public.calculation_runs",
        "public.calculation_rows",
        "public.audit_events",
        "public.report_exports",
        # Global version registries
        "public.methodology_versions",
        "public.taxonomy_versions",
        "public.rules_versions",
    ],
)
def test_every_canonical_table_is_created(expected_table: str) -> None:
    """One ``create table public.X`` must appear across the migrations."""
    sql = _all_migration_sql()
    table_name = expected_table.split(".", 1)[1]
    pattern = re.compile(
        rf"create\s+table\s+(?:public\.)?{re.escape(table_name)}\b",
        re.IGNORECASE,
    )
    assert pattern.search(sql), f"no `create table` for {expected_table}"


def test_rls_enabled_on_every_multi_tenant_table() -> None:
    sql = _all_migration_sql()
    must_enable = [
        "organisations",
        "memberships",
        "user_profiles",
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
        # Global registries are also RLS-enabled (deny writes by default).
        "methodology_versions",
        "taxonomy_versions",
        "rules_versions",
    ]
    for table in must_enable:
        pattern = re.compile(
            rf"alter\s+table\s+(?:public\.)?{table}\s+enable\s+row\s+level\s+security",
            re.IGNORECASE,
        )
        assert pattern.search(sql), f"RLS not enabled on {table}"


@pytest.mark.parametrize(
    "policy_name",
    [
        "projects_select",
        "uploads_select",
        "products_select",
        "classifications_select",
        "manual_reviews_select",
        "calculation_runs_select",
        "calculation_rows_select",
        "audit_events_select",
        "report_exports_select",
        "user_profiles_select_self",
        "user_profiles_select_shared_org",
        "methodology_versions_select",
        "taxonomy_versions_select",
        "rules_versions_select",
        "uploads_storage_select",
        "exports_storage_select",
        # Phase 20: broad Altera-internal UPDATE policy replaces approval-only one
        "report_exports_update",
    ],
)
def test_named_policy_exists(policy_name: str) -> None:
    sql = _all_migration_sql()
    pattern = re.compile(rf"create\s+policy\s+{policy_name}\b", re.IGNORECASE)
    assert pattern.search(sql), f"missing policy: {policy_name}"


def test_audit_immutability_triggers_present() -> None:
    sql = _all_migration_sql()
    for trigger in (
        "trg_audit_events_no_update",
        "trg_audit_events_no_delete",
        "trg_classification_events_no_update",
        "trg_classification_events_no_delete",
    ):
        assert trigger in sql, f"missing trigger: {trigger}"
    assert "reject_audit_mutation" in sql


def test_user_profile_signup_trigger_present() -> None:
    sql = _all_migration_sql()
    assert "trg_handle_new_user" in sql
    assert "handle_new_user" in sql
    assert "after insert on auth.users" in sql.lower()


def test_helper_functions_defined() -> None:
    sql = _all_migration_sql()
    for fn in ("current_user_organisations", "user_role_in"):
        # Each helper must be defined twice: a forward declaration and
        # the real body.
        occurrences = re.findall(rf"create\s+or\s+replace\s+function\s+public\.{fn}\b", sql)
        assert len(occurrences) >= 2, f"helper {fn} should be redefined in 0011"


def test_storage_buckets_created() -> None:
    sql = _all_migration_sql()
    assert "insert into storage.buckets" in sql.lower()
    assert "'uploads'" in sql
    assert "'exports'" in sql


def test_rls_tests_present() -> None:
    files = sorted(RLS_TESTS_DIR.glob("*.sql"))
    assert len(files) >= 3, "expected at least 3 pgTAP test files"
    for path in files:
        body = path.read_text()
        assert "select plan(" in body, f"{path.name} missing plan()"
        assert "select finish();" in body, f"{path.name} missing finish()"


def test_no_migration_drops_a_table() -> None:
    """Migrations are forward-only. A DROP TABLE in the canonical
    history is almost always a mistake (rename via ALTER instead)."""
    sql = _all_migration_sql()
    matches = re.findall(r"drop\s+table\s+", sql, re.IGNORECASE)
    assert matches == [], "DROP TABLE in canonical migrations — use ALTER + rename instead"


def test_classifications_pk_is_product_methodology() -> None:
    sql = (MIGRATIONS_DIR / "0006_classifications.sql").read_text()
    assert "primary key (product_id, methodology)" in sql.lower()


def test_manual_reviews_pk_is_product_methodology() -> None:
    sql = (MIGRATIONS_DIR / "0007_manual_reviews.sql").read_text()
    assert "primary key (product_id, methodology)" in sql.lower()


def test_calculation_rows_pk_is_run_product() -> None:
    sql = (MIGRATIONS_DIR / "0008_calculation_runs.sql").read_text()
    assert "primary key (run_id, product_id)" in sql.lower()


# ---------------------------------------------------------------------
# Forbidden-commercial-fields scan
#
# No table in the schema may declare a commercial column. We scan for
# both column declarations and any text that looks like a column name
# inside a `create table ... (...)` block.
# ---------------------------------------------------------------------
_FORBIDDEN_COLUMN_NAMES = (
    "revenue",
    "margin",
    "cost_price",
    "sales_value",
    "supplier_id",
    "supplier_name",
    "contract_terms",
    "promotion_id",
    "promotion_discount",
    "store_id",
    "store_region",
    "confidential_strategy",
    "internal_score",
)


@pytest.mark.parametrize("forbidden", _FORBIDDEN_COLUMN_NAMES)
def test_no_commercial_column_in_schema(forbidden: str) -> None:
    """The migrations must never declare a commercial column.

    We match against the entire migration corpus rather than parsing
    columns out — the bar is "this identifier never appears as a
    column-shaped token in DDL". A comment that says the word
    'revenue' is allowed; a `revenue numeric` line is not.
    """
    sql = _all_migration_sql()
    # Catches `revenue numeric`, `  revenue  text`, `revenue jsonb`, etc.
    pattern = re.compile(
        rf"(?<![a-z_]){re.escape(forbidden)}\s+(text|numeric|integer|bigint|boolean|jsonb|uuid|date|timestamptz|citext)\b",
        re.IGNORECASE,
    )
    matches = pattern.findall(sql)
    assert not matches, (
        f"forbidden commercial column declared: {forbidden} ({len(matches)} match(es))"
    )


def test_no_commercial_field_in_seed() -> None:
    seed = SEED_FILE.read_text().lower()
    for forbidden in _FORBIDDEN_COLUMN_NAMES:
        assert forbidden not in seed, f"seed.sql references forbidden column {forbidden}"


# ---------------------------------------------------------------------
# Seed file sanity
# ---------------------------------------------------------------------
def test_seed_inserts_version_registry() -> None:
    seed = SEED_FILE.read_text()
    assert "into public.methodology_versions" in seed
    assert "into public.taxonomy_versions" in seed
    assert "into public.rules_versions" in seed


def test_seed_idempotent() -> None:
    """Every INSERT in seed.sql must have an `on conflict` clause so
    repeated `db reset` runs are safe."""
    seed = SEED_FILE.read_text()
    inserts = re.findall(r"insert\s+into\s+public\.\w+", seed, re.IGNORECASE)
    conflicts = re.findall(r"on\s+conflict", seed, re.IGNORECASE)
    assert inserts, "seed.sql has no INSERT statements"
    assert len(conflicts) >= len(inserts), (
        f"seed.sql has {len(inserts)} INSERTs but only {len(conflicts)} ON CONFLICT clauses"
    )


# ---------------------------------------------------------------------
# README pins the contract
# ---------------------------------------------------------------------
def test_readme_lists_every_table() -> None:
    readme = SUPABASE_README.read_text()
    for table in (
        "organisations",
        "memberships",
        "user_profiles",
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
        "methodology_versions",
        "taxonomy_versions",
        "rules_versions",
    ):
        assert f"`{table}`" in readme, f"README missing entry for {table}"


def test_readme_notes_phase_13b_and_13c() -> None:
    readme = SUPABASE_README.read_text()
    assert "Phase 13B" in readme
    assert "Phase 13C" in readme


# ---------------------------------------------------------------------
# Phase 20B: report delivery lifecycle migration
# ---------------------------------------------------------------------
def test_phase20_report_exports_new_columns_present() -> None:
    """0022 must add all six Phase 20 columns to report_exports."""
    sql = _all_migration_sql()
    for col in (
        "under_review_by",
        "under_review_at",
        "delivered_by",
        "delivered_at",
        "client_downloaded_at",
        "client_download_count",
    ):
        assert col in sql, f"Phase 20 column missing from migrations: {col}"


def test_phase20_approval_status_constraint_includes_lifecycle_states() -> None:
    """The approval_status CHECK must include under_review and delivered."""
    sql = _all_migration_sql()
    assert "'under_review'" in sql, "approval_status CHECK missing 'under_review'"
    assert "'delivered'" in sql, "approval_status CHECK missing 'delivered'"


def test_phase20_old_approve_policy_replaced() -> None:
    """report_exports_approve must be dropped; report_exports_update must exist."""
    sql = _all_migration_sql()
    assert "drop policy if exists report_exports_approve" in sql.lower(), (
        "0022 must drop the old report_exports_approve policy"
    )
    pattern = re.compile(r"create\s+policy\s+report_exports_update\b", re.IGNORECASE)
    assert pattern.search(sql), "report_exports_update policy not created"


def test_phase20_select_policy_filters_client_visible_statuses() -> None:
    """The Phase 20 SELECT policy must gate clients to approved/delivered exports."""
    sql = _all_migration_sql()
    assert "approval_status in ('approved', 'delivered')" in sql.lower(), (
        "report_exports_select must restrict clients to approved/delivered statuses"
    )
