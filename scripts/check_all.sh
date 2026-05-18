#!/usr/bin/env bash
# Run all CI checks locally before pushing.
# Usage: ./scripts/check_all.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PASS=0
FAIL=0

_run() {
  local label="$1"
  shift
  echo ""
  echo ">>> $label"
  if "$@"; then
    echo "    OK"
    PASS=$((PASS + 1))
  else
    echo "    FAILED"
    FAIL=$((FAIL + 1))
  fi
}

# ── Backend ──────────────────────────────────────────────────────────────
cd "$REPO_ROOT/apps/api"

_run "backend: pytest (no integration)" \
  uv run pytest --ignore=tests/integration -q

_run "backend: ruff" \
  uv run ruff check .

# ── Frontend ─────────────────────────────────────────────────────────────
cd "$REPO_ROOT"

_run "frontend: typecheck" \
  pnpm typecheck:web

_run "frontend: lint" \
  pnpm lint:web

_run "frontend: build" \
  env NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 \
  env NEXT_PUBLIC_SUPABASE_URL=https://placeholder.supabase.co \
  env NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=sb_publishable_placeholder \
  pnpm build:web

# ── Summary ──────────────────────────────────────────────────────────────
echo ""
echo "================================================"
echo "  Results: $PASS passed, $FAIL failed"
echo "================================================"

if [ "$FAIL" -gt 0 ]; then
  exit 1
fi
