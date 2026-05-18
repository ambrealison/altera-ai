"""Version and build-phase reporting.

Phase is a deliberate, hand-rolled label rather than a derived value. It
moves forward each time a new layer of functionality lands and is asserted
by the /version endpoint and by tests.
"""

from __future__ import annotations

from pydantic import BaseModel

APP_NAME: str = "altera-ai-api"
APP_VERSION: str = "0.0.1"

# Build phase tracker. See docs/project/scope.md for the phased build plan.
BUILD_PHASE: str = "phase_13c_supabase_auth"
BUILD_PHASE_DESCRIPTION: str = (
    "Supabase Auth wiring: backend verifies HS256 JWTs against "
    "SUPABASE_JWT_SECRET via PyJWT, exposes /me, enforces cross-tenant "
    "404 on resource access, and falls back to a dev user only when "
    "ALTERA_DEV_AUTH_ENABLED=true AND no Authorization header is "
    "present. Frontend uses @supabase/supabase-js for sign-in and "
    "attaches Bearer tokens to every API call via the AuthGate + "
    "useAuth() context. The HTTP layer is still backed by the Phase 12 "
    "in-memory store; PostgresStore ships in 13B, Storage in 13D."
)


class VersionInfo(BaseModel):
    app_name: str
    app_version: str
    build_phase: str
    build_phase_description: str


def get_version_info() -> VersionInfo:
    return VersionInfo(
        app_name=APP_NAME,
        app_version=APP_VERSION,
        build_phase=BUILD_PHASE,
        build_phase_description=BUILD_PHASE_DESCRIPTION,
    )
