#!/usr/bin/env bash
# Verify no real secrets are present in git-tracked files (working tree).
#
# Checks:
#   1. No real sk-proj- OpenAI keys in tracked files (placeholders/regexes OK)
#   2. No assigned OPENAI_API_KEY values in .env.example files
#   3. SUPABASE_SERVICE_ROLE_KEY is empty in .env.example files
#   4. gitleaks --no-git scan (if gitleaks is installed)
#
# Does NOT scan git history — see docs/development/runbooks/git-history-secret-cleanup.md
# for history rewrite instructions.
#
# Exit codes: 0 = clean, 1 = possible secret detected
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

FAIL=0
_ok()   { echo "  OK  $*"; }
_fail() { echo "  FAIL $*"; FAIL=1; }

# ── 1. Real OpenAI sk-proj- key in tracked files ─────────────────────────
echo ""
echo "=== 1. Checking tracked files for real sk-proj- keys ==="

# A real sk-proj- key: prefix followed by 40+ uninterrupted alphanum chars.
# Exclude lines that are:
#   - regex/pattern constructs (contain [, {, *, or a backslash)
#   - known placeholders (YOUR_KEY_HERE, sk-proj-...)
#   - code comments or test strings
REAL_KEY_RE='sk-proj-[A-Za-z0-9_-]{40,}'
EXCLUDE_RE='YOUR_KEY_HERE|sk-proj-\.\.\.|sk-proj-\[|sk-proj-\\|\{40|\{20|regex|compile|re\.'

found=0
while IFS= read -r file; do
  [ -f "$file" ] || continue
  matches=$(grep -nE "$REAL_KEY_RE" "$file" \
    | grep -vE "$EXCLUDE_RE" || true)
  if [ -n "$matches" ]; then
    # Redact key before printing — never echo the full secret
    redacted=$(echo "$matches" | sed -E "s/sk-proj-[A-Za-z0-9_-]{20,}/sk-proj-[REDACTED]/g")
    _fail "possible real key in $file:"
    echo "$redacted" | sed 's/^/       /'
    found=1
  fi
done < <(git ls-files 2>/dev/null)

if [ "$found" -eq 0 ]; then
  _ok "no real sk-proj- keys found in tracked files"
fi

# ── 2. .env.example OPENAI_API_KEY must be empty or placeholder ───────────
echo ""
echo "=== 2. Checking .env.example files for assigned OPENAI_API_KEY ==="

for f in .env.example apps/api/.env.example apps/web/.env.example; do
  [ -f "$f" ] || continue
  # Match lines like OPENAI_API_KEY=something-non-empty that isn't a placeholder
  assigned=$(grep -E '^OPENAI_API_KEY=[^#]' "$f" \
    | grep -vE 'YOUR_KEY_HERE|sk-proj-\.\.\.|^OPENAI_API_KEY=$' || true)
  if [ -n "$assigned" ]; then
    _fail "$f contains a non-placeholder OPENAI_API_KEY value"
  else
    _ok "$f"
  fi
done

# ── 3. .env.example SUPABASE_SERVICE_ROLE_KEY must be empty ──────────────
echo ""
echo "=== 3. Checking .env.example files for SUPABASE_SERVICE_ROLE_KEY ==="

for f in .env.example apps/api/.env.example apps/web/.env.example; do
  [ -f "$f" ] || continue
  # Must be empty: SUPABASE_SERVICE_ROLE_KEY=
  assigned=$(grep -E '^SUPABASE_SERVICE_ROLE_KEY=.+' "$f" || true)
  if [ -n "$assigned" ]; then
    _fail "$f contains a non-empty SUPABASE_SERVICE_ROLE_KEY"
  else
    _ok "$f"
  fi
done

# ── 4. gitleaks working-tree scan ─────────────────────────────────────────
echo ""
echo "=== 4. gitleaks working-tree scan ==="

if command -v gitleaks >/dev/null 2>&1; then
  if gitleaks detect --source . --config .gitleaks.toml --no-git --exit-code 1 \
      --report-path /dev/null 2>/dev/null; then
    _ok "gitleaks: no secrets in working tree"
  else
    _fail "gitleaks: secrets detected in working tree — run gitleaks manually for details"
  fi
else
  echo "  SKIP gitleaks not installed (brew install gitleaks / pip install gitleaks)"
fi

# ── Summary ───────────────────────────────────────────────────────────────
echo ""
echo "================================================"
if [ "$FAIL" -gt 0 ]; then
  echo "  FAIL: possible secrets detected"
  echo ""
  echo "  If a real secret is found:"
  echo "  1. Revoke the key immediately in the provider dashboard"
  echo "  2. Replace with a placeholder in the working tree and commit"
  echo "  3. Follow the history cleanup runbook:"
  echo "     docs/development/runbooks/git-history-secret-cleanup.md"
  echo "================================================"
  exit 1
else
  echo "  PASS: no secrets detected in tracked files"
  echo ""
  echo "  NOTE: this script checks the working tree only."
  echo "  Git history may still contain revoked secrets — see:"
  echo "  docs/development/runbooks/git-history-secret-cleanup.md"
  echo "================================================"
fi
