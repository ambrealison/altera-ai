#!/usr/bin/env bash
# Smoke-test a deployed backend and optionally a deployed frontend.
#
# Usage:
#   API_BASE_URL=https://api.staging.altera-ai.com ./scripts/staging_smoke.sh
#   ./scripts/staging_smoke.sh <api-url> [web-url]
#
# Environment variables (override positional args):
#   API_BASE_URL   — backend root URL, no trailing slash
#   WEB_BASE_URL   — frontend root URL (optional)
set -euo pipefail

API_BASE_URL="${API_BASE_URL:-${1:-}}"
WEB_BASE_URL="${WEB_BASE_URL:-${2:-}}"

if [ -z "$API_BASE_URL" ]; then
  echo "ERROR: API_BASE_URL is required."
  echo "Usage: API_BASE_URL=<url> ./scripts/staging_smoke.sh [web-url]"
  exit 1
fi

PASS=0
FAIL=0

_check() {
  local label="$1"
  local url="$2"
  local expected_status="${3:-200}"
  local body_contains="${4:-}"

  local actual_status
  actual_status=$(curl -sS -o /tmp/_smoke_body -w "%{http_code}" \
    --max-time 15 --retry 2 --retry-delay 2 "$url" 2>/dev/null || echo "000")

  if [ "$actual_status" != "$expected_status" ]; then
    echo "  FAIL  $label — expected HTTP $expected_status, got $actual_status"
    FAIL=$((FAIL + 1))
    return
  fi

  if [ -n "$body_contains" ] && ! grep -q "$body_contains" /tmp/_smoke_body 2>/dev/null; then
    echo "  FAIL  $label — HTTP $actual_status but body missing: $body_contains"
    FAIL=$((FAIL + 1))
    return
  fi

  echo "  OK    $label (HTTP $actual_status)"
  PASS=$((PASS + 1))
}

# ── Backend ───────────────────────────────────────────────────────────────
echo ""
echo "=== Backend: $API_BASE_URL ==="
_check "GET /health"  "$API_BASE_URL/health"  200 '"status"'
_check "GET /version" "$API_BASE_URL/version" 200 '"app_name"'
# Unauthenticated API call should return 401, not 500.
_check "GET /api/v1/me (expect 401)" "$API_BASE_URL/api/v1/me" 401

# ── Frontend ──────────────────────────────────────────────────────────────
if [ -n "$WEB_BASE_URL" ]; then
  echo ""
  echo "=== Frontend: $WEB_BASE_URL ==="
  _check "GET /" "$WEB_BASE_URL/" 200
fi

# ── Summary ───────────────────────────────────────────────────────────────
rm -f /tmp/_smoke_body
echo ""
echo "================================================"
echo "  Results: $PASS passed, $FAIL failed"
echo "================================================"

if [ "$FAIL" -gt 0 ]; then
  exit 1
fi
