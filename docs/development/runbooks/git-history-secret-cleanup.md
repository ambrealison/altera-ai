# Runbook: git history secret cleanup

## Situation

A real OpenAI API key was committed in commit `27205ca`
(`feat: add Altera review queue filtering and sorting`) inside `.env.example`.

The key was removed from the working tree in a later commit (Phase 30A),
and has been **revoked at the OpenAI dashboard**. Revoking the key means
it can no longer be used to make API calls — but the literal value still
exists in git history. Any clone of this repository exposes the revoked
key in plaintext.

## Why revoking is not enough for a public repo

Even a revoked secret in history is a liability:
- Automated scanners will flag the repo and may block CI/CD pipelines
- It normalises bad practice for contributors
- If the key is ever re-issued to the same value by the provider (uncommon
  but possible with some providers), it becomes active again
- GitHub's secret scanning will alert and may quarantine the repo

History must be rewritten before pushing to any **public** or
**shared** remote.

## When this runbook applies

- Before pushing to a public GitHub remote for the first time
- Before sharing the repo URL with any external party
- After any confirmed secret committed to history (not just OpenAI keys)

## Scope of contaminated commits

The leaked key was introduced in `27205ca` and removed in `7257ba4`.
All commits between those two (inclusive of `27205ca`) contain the real
value in the `.env.example` blob. `git filter-repo` rewrites all of
them in a single pass.

---

## Recommended tool: git-filter-repo

`git filter-repo` is the officially recommended replacement for
`git filter-branch`. It is faster, safer, and produces a clean result.

```bash
# Install (pick one):
pip install git-filter-repo          # cross-platform
brew install git-filter-repo         # macOS
apt install git-filter-repo          # Debian/Ubuntu
```

---

## Step-by-step: rewrite history

### 1. Back up the repo

```bash
cp -r /path/to/altera-ai /path/to/altera-ai.backup-$(date +%Y%m%d)
```

Do not skip this. The rewrite cannot be undone without the backup.

### 2. Confirm the key is gone from the working tree

```bash
./scripts/verify_no_tracked_secrets.sh
```

This must pass before proceeding. If it fails, re-check the current
working tree and commit any placeholder replacement first.

### 3. Extract the leaked value to a replacements file

The replacements file must contain the **exact** secret string to replace.
The following extracts it directly from git history so you never need to
copy-paste it into a terminal or document.

```bash
# Extract the leaked value from .env.example history.
# The key is extracted, trimmed, and written directly to a temp file.
git log --all -p -- .env.example \
  | grep '^+OPENAI_API_KEY=sk-' \
  | head -1 \
  | sed 's/^+OPENAI_API_KEY=//' \
  | tr -d '[:space:]' \
  | awk '{ print $0 "==>REDACTED_OPENAI_KEY" }' \
  > /tmp/altera-secrets.txt

# Sanity-check the file without printing the key:
echo "Replacements file size: $(wc -c < /tmp/altera-secrets.txt) bytes"
# Expected: > 50 bytes (sk-proj- prefix + 40+ chars + ==>REDACTED_OPENAI_KEY)
```

### 4. Run git-filter-repo

```bash
git filter-repo --replace-text /tmp/altera-secrets.txt --force
```

This rewrites every commit in history, replacing the exact key string with
`REDACTED_OPENAI_KEY`. All commit SHAs change — this is expected.

### 5. Verify the key is gone from history

```bash
# Should print nothing:
git log --all -p -- .env.example | grep 'sk-proj-' | grep -v 'YOUR_KEY_HERE'

# Full working-tree + gitleaks check:
./scripts/verify_no_tracked_secrets.sh

# Full history scan with gitleaks (requires gitleaks installed):
gitleaks detect --source . --config .gitleaks.toml
```

All three should produce no output / pass.

### 6. Shred the replacements file

```bash
shred -u /tmp/altera-secrets.txt 2>/dev/null || rm -f /tmp/altera-secrets.txt
```

### 7. If a remote already exists: coordinate before force-pushing

**Read this section before pushing to any remote.**

History rewrite changes every commit SHA from the oldest contaminated
commit onwards. If any collaborators have cloned the repo:

1. Notify all collaborators **before** force-pushing.
2. After force-push, each collaborator must re-clone (not pull):
   ```bash
   cd ..
   rm -rf altera-ai
   git clone <remote-url> altera-ai
   ```
3. Any open pull requests will need to be re-opened against the new history.

Since this repo currently has **no remote**, no coordination is needed.

### 8. Force-push (when ready)

```bash
# Verify you are on the right branch and remote:
git remote -v
git log --oneline -5

# Force-push (requires write access):
git push --force-with-lease origin main

# Push all refs if needed:
git push --force-with-lease --all origin
```

Never use `--force` without `--force-with-lease` on a shared remote —
it silently overwrites concurrent pushes.

---

## Alternative tool: BFG Repo-Cleaner

If `git filter-repo` is unavailable, BFG is an alternative:

```bash
# Install: download bfg.jar from https://rtyley.github.io/bfg-repo-cleaner/
# Create a passwords file with the exact key value:
echo 'sk-proj-...' > /tmp/passwords.txt   # fill in the full key

java -jar bfg.jar --replace-text /tmp/passwords.txt .
git reflog expire --expire=now --all
git gc --prune=now --aggressive

rm /tmp/passwords.txt
```

BFG is generally slower and less thorough than `git filter-repo`.
Prefer `git filter-repo`.

---

## Final verification checklist

- [ ] `./scripts/verify_no_tracked_secrets.sh` passes
- [ ] `git log --all -p | grep 'sk-proj-' | grep -v 'YOUR_KEY_HERE'` prints nothing
- [ ] `gitleaks detect --source . --config .gitleaks.toml` passes
- [ ] Key has been confirmed revoked in the OpenAI dashboard
- [ ] `/tmp/altera-secrets.txt` has been deleted
- [ ] Backup copy deleted after confirming history is clean
- [ ] Force-push completed (if remote exists)
- [ ] Collaborators notified and re-cloned (if remote has collaborators)
