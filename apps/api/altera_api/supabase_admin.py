"""Supabase Auth Admin API operations for the backend only.

Service-role operations that must never run in the browser or be
accessible via the frontend. The service role key bypasses RLS — every
call here is intentional and server-side only.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class InviteResult:
    user_id: UUID
    email: str


class SupabaseAdminClient:
    """Wraps supabase-py's admin auth API for server-side use."""

    def __init__(self, url: str, service_role_key: str) -> None:
        from supabase import create_client  # type: ignore[import-untyped]

        self._client = create_client(url, service_role_key)

    def resend_invite(
        self,
        email: str,
        *,
        redirect_to: str | None = None,
    ) -> None:
        """Send a password-recovery link to an existing Supabase Auth user.

        Used for "resend invite" — functionally identical to the original
        invite link: the user clicks it, lands on /auth/callback, and is
        redirected to /reset-password to set their password.

        Uses generate_link(type="recovery") rather than a second
        invite_user_by_email call because the latter fails for users who
        have already confirmed their email.
        """
        params: dict = {"type": "recovery", "email": email}
        if redirect_to:
            params["redirect_to"] = redirect_to
        self._client.auth.admin.generate_link(params)

    def invite_user_by_email(
        self,
        email: str,
        *,
        redirect_to: str | None = None,
    ) -> InviteResult:
        """Create a Supabase Auth user and send an invite email.

        The invited user sets their own password via the invite link.
        Raises RuntimeError if Supabase returns no user object.
        Raises the underlying supabase-py exception on HTTP errors
        (e.g. 422 if the email is already registered).
        """
        options: dict = {}
        if redirect_to:
            options["redirect_to"] = redirect_to
        response = self._client.auth.admin.invite_user_by_email(email, options=options)
        user = response.user
        if user is None:
            raise RuntimeError(
                f"Supabase invite returned no user object for {email!r}"
            )
        return InviteResult(user_id=UUID(user.id), email=user.email or email)


def get_supabase_admin_client(
    url: str | None,
    service_role_key: str | None,
) -> SupabaseAdminClient | None:
    """Build a client from config, or None if Supabase is not configured."""
    if not url or not service_role_key:
        return None
    return SupabaseAdminClient(url, service_role_key)
