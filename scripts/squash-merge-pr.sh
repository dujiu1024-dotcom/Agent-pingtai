#!/usr/bin/env bash
# Squash-merge open PRs and proactively cancel all their pending workflow runs.
# This prevents zombie runs from squash-merged branches from blocking CI queues.
#
# Usage:
#   scripts/squash-merge-pr.sh           # merge all open PRs
#   scripts/squash-merge-pr.sh 513 516   # merge specific PR numbers

set -u

repo="dujiu1024-dotcom/Agent-pingtai"

if ! command -v gh &>/dev/null; then
  echo "❌ gh CLI not found. Install it first."
  exit 1
fi

# --- Helpers ---
REMOTE=$(git remote get-url github 2>/dev/null && echo github || echo origin)

fetch_main() {
  git fetch "$REMOTE" main 2>/dev/null || git fetch origin main 2>/dev/null || true
}

cancel_pr_runs() {
  local branch="$1"
  local run_ids
  run_ids=$(gh run list --repo "$repo" --branch "$branch" \
    --status queued,in_progress --json databaseId -q '.[].databaseId' 2>/dev/null || true)
  for rid in $run_ids; do
    gh run cancel "$rid" --repo "$repo" 2>/dev/null || true
  done
}

resolve_rebase_conflicts() {
  # Loop: collect conflicts, pick theirs, continue rebase — until clean.
  while true; do
    local conflicts
    conflicts=$(git diff --name-only --diff-filter=U 2>/dev/null)
    if [ -z "$conflicts" ]; then
      break
    fi
    for f in $conflicts; do
      git checkout --theirs "$f" 2>/dev/null && git add "$f" 2>/dev/null || \
      git checkout --ours "$f"   "$f" 2>/dev/null && git add "$f" 2>/dev/null || true
    done
    git rebase --continue 2>/dev/null || true
  done
}

rebase_and_merge() {
  local pr_number="$1"
  local branch
  branch=$(gh pr view "$pr_number" --repo "$repo" --json headRefName -q '.headRefName' 2>/dev/null || true)
  [ -z "$branch" ] && return 1

  echo "  🧹 cancel runs on '$branch'"
  cancel_pr_runs "$branch"

  fetch_main
  git fetch "$REMOTE" "$branch:$branch" 2>/dev/null || git fetch origin "$branch:$branch" 2>/dev/null || return 1
  git checkout "$branch" 2>/dev/null || return 1

  # Rebase against latest main
  git rebase "$REMOTE/main" 2>/dev/null || git rebase origin/main 2>/dev/null || true
  resolve_rebase_conflicts
  echo "  rebase done"

  git push --force-with-lease "$REMOTE" "$branch" 2>/dev/null || \
  git push --force-with-lease origin "$branch" 2>/dev/null || return 1

  # Cancel again after force push (new runs may have queued)
  cancel_pr_runs "$branch"

  # Try merge — if base-branch-modified, rebase one more time
  local result
  for attempt in 1 2; do
    result=$(gh pr merge "$pr_number" --repo "$repo" --squash --delete-branch --admin 2>&1) || true
    if echo "$result" | grep -qiE "GraphQL: Base branch was modified|merge conflicts"; then
      echo "  ↻ base changed, rebase attempt $attempt/2"
      fetch_main
      git fetch "$REMOTE" "$branch:$branch" 2>/dev/null || true
      git checkout "$branch" 2>/dev/null || true
      git rebase "$REMOTE/main" 2>/dev/null || git rebase origin/main 2>/dev/null || true
      resolve_rebase_conflicts
      git push --force-with-lease "$REMOTE" "$branch" 2>/dev/null || \
      git push --force-with-lease origin "$branch" 2>/dev/null || continue
      cancel_pr_runs "$branch"
    else
      break
    fi
  done

  if echo "$result" | grep -qiE "error|GraphQL:"; then
    echo "  ❌ $result"
    return 1
  fi
  echo "  ✅ merged"
  return 0
}

# --- Protect working tree from script's own changes ---
STASHED=0
if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
  git stash -u -q 2>/dev/null && STASHED=1
fi

# Ensure we're on main, up to date
git checkout main 2>/dev/null || true
git pull "$REMOTE" main 2>/dev/null || git pull origin main 2>/dev/null || true

# --- Collect PR numbers ---
if [ $# -gt 0 ]; then
  prs=("$@")
else
  prs=()
  while IFS= read -r n; do
    [ -n "$n" ] && prs+=("$n")
  done < <(gh pr list --state open --repo "$repo" --json number -q '.[].number' 2>/dev/null || true)
fi

if [ ${#prs[@]} -eq 0 ]; then
  echo "✅ No open PRs to merge."
  git checkout main 2>/dev/null
  [ $STASHED -eq 1 ] && git stash pop -q 2>/dev/null
  exit 0
fi

echo "📦 PRs to merge: ${prs[*]}"
echo ""

merged=0
failed=()

for n in "${prs[@]}"; do
  echo "━━━ PR #$n ━━━"

  branch=$(gh pr view "$n" --repo "$repo" --json headRefName -q '.headRefName' 2>/dev/null || true)

  # 1. Cancel runs first
  [ -n "$branch" ] && cancel_pr_runs "$branch"

  # 2. Try direct squash merge
  result=$(gh pr merge "$n" --repo "$repo" --squash --delete-branch --admin 2>&1) || true

  if echo "$result" | grep -qiE "GraphQL:|error"; then
    if echo "$result" | grep -qi "merge conflict"; then
      echo "  ⚠️  conflict — rebase"
      if rebase_and_merge "$n"; then merged=$((merged + 1)); else failed+=("$n"); fi
    elif echo "$result" | grep -qi "Base branch was modified"; then
      echo "  ⚠️  base changed — rebase"
      if rebase_and_merge "$n"; then merged=$((merged + 1)); else failed+=("$n"); fi
    else
      echo "  ❌ $result"
      failed+=("$n")
    fi
  else
    echo "  ✅ merged"
    merged=$((merged + 1))
  fi
done

# --- Wrap up ---
git checkout main 2>/dev/null
git pull "$REMOTE" main 2>/dev/null || true

# Auto-prune zombie runs from squash-merged branches
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -x "$SCRIPT_DIR/prune-zombie-runs.sh" ]; then
  echo ""
  echo "🧹 Pruning zombie runs from merged branches..."
  bash "$SCRIPT_DIR/prune-zombie-runs.sh" 2>/dev/null || true
fi

if [ $STASHED -eq 1 ]; then
  git stash pop -q 2>/dev/null || true
fi

echo ""
echo "🏁 Done: $merged merged"
[ ${#failed[@]} -gt 0 ] && echo "⚠️  still open: ${failed[*]}"

open_left=$(gh pr list --state open --repo "$repo" --json number -q '.[].number' 2>/dev/null || true)
if [ -n "$open_left" ]; then
  echo "🆕 new PRs appeared: $open_left"
fi
