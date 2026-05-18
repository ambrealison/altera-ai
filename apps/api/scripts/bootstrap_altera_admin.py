#!/usr/bin/env python3
"""Bootstrap the first Altera internal organisation and admin user.

One-time setup script for staging and production environments.
The Supabase Auth user must already exist before running this script.

Create the Auth user first:
  - Supabase dashboard → Authentication → Users → Invite user
  - Or: supabase auth user create --email admin@altera-ai.com  (requires Supabase CLI)

Then run this script with the Auth user's UUID:

    cd apps/api
    SUPABASE_URL=https://<ref>.supabase.co \\
    SUPABASE_SERVICE_ROLE_KEY=<key> \\
    uv run python scripts/bootstrap_altera_admin.py \\
        --user-id <supabase-auth-user-uuid> \\
        --email admin@altera-ai.com \\
        --confirm

The script is idempotent: running it twice with the same slug and
user-id produces the same result — no duplicates are created.

Required environment variables:
    SUPABASE_URL              Supabase project URL
    SUPABASE_SERVICE_ROLE_KEY Service role key (bypasses RLS; never commit)

See docs/development/runbooks/bootstrap-first-admin.md for full instructions.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import uuid
from datetime import UTC, datetime
from typing import Any

# ── Validation helpers ─────────────────────────────────────────────────────

_SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def _validate_slug(slug: str) -> str:
    if not _SLUG_RE.match(slug):
        raise ValueError(
            f"Invalid slug {slug!r} — must be lowercase alphanumeric with hyphens, "
            "e.g. 'altera-ai'"
        )
    if len(slug) > 80:
        raise ValueError(f"Slug too long (max 80 chars): {slug!r}")
    return slug


def _validate_uuid(value: str, field: str) -> str:
    try:
        uuid.UUID(value)
        return value
    except ValueError:
        raise ValueError(f"Invalid UUID for {field}: {value!r}") from None


# ── Core bootstrap operations (pure functions, injectable client) ──────────

def upsert_organisation(
    client: Any,
    org_name: str,
    org_slug: str,
    org_id: str | None = None,
) -> tuple[str, bool]:
    """Create the Altera internal organisation if it does not exist.

    Identifies an existing organisation by slug (unique constraint).
    If one already exists, returns its ID without any modification.

    Returns:
        (org_id, created): org_id is the UUID string; created is True if
        a new row was inserted, False if an existing row was found.
    """
    existing = (
        client.table("organisations")
        .select("id")
        .eq("slug", org_slug)
        .execute()
    )
    if existing.data:
        return existing.data[0]["id"], False

    new_id = org_id or str(uuid.uuid4())
    now = _now_iso()
    client.table("organisations").insert(
        {
            "id": new_id,
            "name": org_name,
            "slug": org_slug,
            "organisation_type": "altera_internal",
            "created_at": now,
        }
    ).execute()
    return new_id, True


def upsert_user_profile(
    client: Any,
    user_id: str,
    email: str,
    display_name: str,
) -> bool:
    """Create or update the user profile record.

    Uses on_conflict=user_id so re-runs update display_name/email if
    they have changed. The Auth user must already exist in auth.users.

    Returns True if the call succeeded (raises on DB error).
    """
    now = _now_iso()
    client.table("user_profiles").upsert(
        {
            "user_id": user_id,
            "email": email,
            "display_name": display_name,
            "created_at": now,
            "updated_at": now,
        },
        on_conflict="user_id",
    ).execute()
    return True


def upsert_membership(
    client: Any,
    user_id: str,
    org_id: str,
    role: str,
) -> bool:
    """Create or update the org membership.

    Uses on_conflict=(user_id,organisation_id) so re-runs are safe and
    will update the role if it has changed.

    Returns True if the call succeeded (raises on DB error).
    """
    now = _now_iso()
    client.table("memberships").upsert(
        {
            "user_id": user_id,
            "organisation_id": org_id,
            "role": role,
            "created_at": now,
        },
        on_conflict="user_id,organisation_id",
    ).execute()
    return True


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ── CLI ────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--org-name",
        default="Altera AI",
        help="Organisation display name (default: 'Altera AI')",
    )
    parser.add_argument(
        "--org-slug",
        default="altera-ai",
        help="Organisation URL slug — lowercase alphanumeric-dash (default: 'altera-ai')",
    )
    parser.add_argument(
        "--org-id",
        default=None,
        metavar="UUID",
        help="Pin the organisation to a specific UUID (optional; auto-generated otherwise)",
    )
    parser.add_argument(
        "--user-id",
        required=True,
        metavar="UUID",
        help="UUID of the existing Supabase Auth user to grant altera_admin role",
    )
    parser.add_argument(
        "--email",
        required=True,
        help="Email address of the admin user (must match the Supabase Auth account)",
    )
    parser.add_argument(
        "--display-name",
        default=None,
        help="Display name for the user profile (defaults to local part of email)",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Required safety flag — confirms you intend to modify the database",
    )
    return parser


def run(args: argparse.Namespace, client: Any | None = None) -> None:
    """Run the bootstrap.

    ``client`` is injected in tests; when None it is built from env vars.
    Raises SystemExit on validation failure to keep the main path clean.
    """
    # ── Safety gate ────────────────────────────────────────────────────────
    env_confirm = os.getenv("BOOTSTRAP_CONFIRM", "").lower() in ("1", "true", "yes")
    if not args.confirm and not env_confirm:
        print(
            "ERROR: --confirm flag (or BOOTSTRAP_CONFIRM=true env var) is required.\n"
            "This script writes to the database. Re-run with --confirm when ready.",
            file=sys.stderr,
        )
        sys.exit(1)

    # ── Input validation ───────────────────────────────────────────────────
    try:
        _validate_slug(args.org_slug)
        _validate_uuid(args.user_id, "--user-id")
        if args.org_id:
            _validate_uuid(args.org_id, "--org-id")
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    display_name = (args.display_name or "").strip() or args.email.split("@")[0]

    # ── Build client from env vars (skipped when injected) ────────────────
    if client is None:
        supabase_url = os.environ.get("SUPABASE_URL", "").strip()
        service_role_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()

        if not supabase_url:
            print("ERROR: SUPABASE_URL environment variable is not set.", file=sys.stderr)
            sys.exit(1)
        if not service_role_key:
            print(
                "ERROR: SUPABASE_SERVICE_ROLE_KEY environment variable is not set.",
                file=sys.stderr,
            )
            sys.exit(1)

        try:
            from supabase import create_client  # type: ignore[import-untyped]
        except ImportError:
            print(
                "ERROR: supabase package is not installed.\n"
                "Run: cd apps/api && uv sync",
                file=sys.stderr,
            )
            sys.exit(1)

        client = create_client(supabase_url, service_role_key)

    # ── Execute ────────────────────────────────────────────────────────────
    print("Bootstrap starting…")
    print(f"  Organisation : {args.org_name!r}  (slug={args.org_slug!r})")
    print(f"  User         : {args.email!r}  (user_id={args.user_id})")
    print("  Role         : altera_admin")
    print()

    try:
        org_id, org_created = upsert_organisation(
            client, args.org_name, args.org_slug, args.org_id
        )
        print(f"  [org]     {'CREATED' if org_created else 'EXISTS '}  id={org_id}")

        upsert_user_profile(client, args.user_id, args.email, display_name)
        print(f"  [profile] UPSERTED  user_id={args.user_id}")

        upsert_membership(client, args.user_id, org_id, "altera_admin")
        print(f"  [member]  UPSERTED  org_id={org_id}  role=altera_admin")

    except Exception as exc:  # noqa: BLE001
        print(f"\nERROR: Database operation failed: {exc}", file=sys.stderr)
        print(
            "\nIf the error mentions 'foreign key constraint', the Supabase Auth user\n"
            "does not exist. Create it in the Supabase dashboard first:\n"
            "  Authentication → Users → Invite user",
            file=sys.stderr,
        )
        sys.exit(1)

    print()
    print("Bootstrap complete.")
    print(f"  Verify: log in as {args.email!r} and call GET /api/v1/me")
    print("  Expected: role='altera_admin', organisation_type='altera_internal'")


def main() -> None:
    parser = _build_parser()
    run(parser.parse_args())


if __name__ == "__main__":
    main()
