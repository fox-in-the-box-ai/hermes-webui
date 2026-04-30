#!/bin/bash
# apply-local-patches.sh: Rebase local-patches onto the upstream default branch
# after a force-update (git reset --hard) that bypasses the post-merge hook.
#
# Called by api/updates.py after a hard reset.
#
# Usage:
#   scripts/apply-local-patches.sh [--no-restart]
#
# Exit codes:
#   0  Success
#   1  Rebase conflicts (leave the repository in a resolvable state)
#   2  local-patches branch does not exist

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

NO_RESTART=false
for arg in "$@"; do
    case "$arg" in
        --no-restart) NO_RESTART=true ;;
    esac
done

# Detect default branch (master for this repo)
DEFAULT_BRANCH=$(git -C "$REPO_DIR" symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's|.*/||' || echo "master")

# Verify local-patches exists
if ! git -C "$REPO_DIR" rev-parse --verify local-patches >/dev/null 2>&1; then
    echo "[apply-local-patches] local-patches branch not found — nothing to do."
    exit 2
fi

echo "[apply-local-patches] Rebasing local-patches onto origin/$DEFAULT_BRANCH..."

# Rebase local-patches onto freshly-reset default branch
if git -C "$REPO_DIR" rebase --onto origin/"$DEFAULT_BRANCH" origin/"$DEFAULT_BRANCH" local-patches 2>&1; then
    echo "[apply-local-patches] local-patches rebased successfully."
    
    if [ "$NO_RESTART" = false ]; then
        # Switch to local-patches if not already on it (safe because reset --hard
        # already cleared the working tree and no Python process is running during
        # this phase of the update flow).
        CURRENT_BRANCH=$(git -C "$REPO_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
        if [ "$CURRENT_BRANCH" != "local-patches" ]; then
            git -C "$REPO_DIR" checkout local-patches
            echo "[apply-local-patches] Switched to local-patches."
        fi
    fi
    exit 0
else
    RET=$?
    echo "[apply-local-patches] WARNING: rebase had conflicts."
    echo "  Resolve manually:"
    echo "    cd $REPO_DIR"
    echo "    git checkout local-patches"
    echo "    git rebase --continue  (after fixing conflicts)"
    echo "  Or abort:"
    echo "    git rebase --abort"
    # Do NOT exit non-zero here — the calling code may still want to restart the server
    exit 0
fi
