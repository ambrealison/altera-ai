# Runbook: git history secret cleanup

## Status

**Completed 2026-05-19 (Phase 31E).** The history rewrite has been run.
All commit hashes changed. The key `***REMOVED_OPENAI_KEY***` now appears
in place of the revoked key in every affected commit. Pre-rewrite state
is archived at `/tmp/altera_backup_pre_cleanup.bundle`.

---

## Situation

A real OpenAI API key was committed in commit `27205ca`
(`feat: add Altera review queue filtering and sorting`) inside `.env.example`.

The key was removed from the working tree in a later commit (Phase 30A),
and has been **revoked at the OpenAI dashboard**. Revoking the key means
it can no longer be used to make API calls. The literal value has now been
removed from git history (see Status above).

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

A git bundle is the preferred backup — it is self-contained and can be
used to restore without needing another copy of the repo directory.

```bash
git bundle create /tmp/altera_backup_$(date +%Y%m%d).bundle --all
git bundle verify /tmp/altera_backup_$(date +%Y%m%d).bundle
```

Alternatively, a directory copy also works:

```bash
cp -r /path/to/altera-ai /path/to/altera-ai.backup-$(date +%Y%m%d)
```

Do not skip this. `git filter-repo` rewrites all refs — the backup is
the only way to recover the pre-rewrite state.

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
# Use grep -v to exclude the placeholder line that replaced the real key.
git log --all -p -- .env.example \
  | grep '^+OPENAI_API_KEY=sk-proj-' \
  | grep -v 'YOUR_KEY_HERE' \
  | head -1 \
  | sed 's/^+OPENAI_API_KEY=//' \
  | tr -d '\n' \
  > /tmp/altera_key_raw.txt

# Build the replacements file: LITERAL==>PLACEHOLDER
printf '%s==>***REMOVED_OPENAI_KEY***\n' "$(cat /tmp/altera_key_raw.txt)" \
  > /tmp/altera-secrets.txt

# Sanity-check the file without printing the key:
echo "Replacements file: $(wc -l < /tmp/altera-secrets.txt) line(s), $(wc -c < /tmp/altera-secrets.txt) bytes"
# Expected: 1 line, > 180 bytes (sk-proj- prefix + 140+ chars + ==>***REMOVED_OPENAI_KEY***)
```

### 4. Run git-filter-repo

```bash
git filter-repo --replace-text /tmp/altera-secrets.txt --force
```

The `--force` flag is required when running on a non-fresh-clone repo
(i.e. any repo that has been worked in locally rather than just cloned).
All commit SHAs change — this is expected.

This replaces the exact key string with `***REMOVED_OPENAI_KEY***` in
every affected commit blob.

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
shred -u /tmp/altera_key_raw.txt 2>/dev/null || rm -f /tmp/altera_key_raw.txt
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

- [x] `./scripts/verify_no_tracked_secrets.sh` passes _(verified 2026-05-19)_
- [x] `git log --all -p | grep 'sk-proj-' | grep -v 'YOUR_KEY_HERE'` prints nothing _(verified 2026-05-19)_
- [ ] `gitleaks detect --source . --config .gitleaks.toml` passes _(gitleaks not yet installed; install with `brew install gitleaks`)_
- [x] Key has been confirmed revoked in the OpenAI dashboard _(revoked during Phase 30A)_
- [ ] `/tmp/altera-secrets.txt` and `/tmp/altera_key_raw.txt` deleted
- [ ] Bundle backup at `/tmp/altera_backup_pre_cleanup.bundle` archived or deleted after confirming history is clean
- [ ] Force-push completed (if remote exists)
- [ ] Collaborators notified and re-cloned (if remote has collaborators)
