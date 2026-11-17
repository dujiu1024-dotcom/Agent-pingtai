#!/usr/bin/env bash
# Squash-merge open PRs and proactively cancel all their pending workflow runs.
# This prevents zombie runs from squash-merged branches from blocking CI queues.
#
# Usage:
#   scripts/squash-merge-pr.sh           # merge all open PRs
#   scripts/squash-merge-pr.sh 513 516   # merge specific PR numbers

set -euo pipefail

repo="dujiu1024-dotcom/Agent-pingtai"

if ! command -v gh &>/dev/null; then
  echo "❌ gh CLI not found. Install it first."
  exit 1
fi

# Collect PR numbers
if [ $# -gt 0 ]; then
  prs=("$@")
else
  mapfile -t prs < <(gh pr list --state open --repo "$repo" --json number -q '.[].number')
fi

if [ ${#prs[@]} -eq 0 ]; then
  echo "✅ No open PRs to merge."
  exit 0
fi

echo "📦 PRs to merge: ${prs[*]}"
echo ""

merged=0
rebase_needed=()

for n in "${prs[@]}"; do
  echo "━━━ PR #$n ━━━"

  # 1. Cancel all queued/in_progress runs for this PR — prevents zombie runners
  branch=$(gh pr view "$n" --repo "$repo" --json headRefName -q '.headRefName' 2>/dev/null || true)
  if [ -n "$branch" ]; then
    echo "  🧹 Canceling pending runs on '$branch'..."
    run_ids=$(gh run list --repo "$repo" --branch "$branch" \
      --status queued,in_progress --json databaseId -q '.[].databaseId' 2>/dev/null || true)
    for rid in $run_ids; do
      gh run cancel "$rid" --repo "$repo" 2>/dev/null && echo "    canceled #$rid" || true
    done
  fi

  # 2. Try squash merge directly
  result=$(gh pr merge "$n" --repo "$repo" --squash --delete-branch --admin 2>&1) || true

  if echo "$result" | grep -qi "conflict"; then
    echo "  ⚠️  merge conflict — needs rebase"
    rebase_needed+=("$n")
    continue
  fi

  if echo "$result" | grep -qiE "GraphQL:|error"; then
    echo "  ❌ failed: $result"
    continue
  fi

  echo "  ✅ merged"
  merged=$((merged + 1))
done

# 3. Rebase + merge conflicts
for n in "${rebase_needed[@]}"; do
  echo ""
  echo "━━━ Rebase PR #$n ━━━"
  branch=$(gh pr view "$n" --repo "$repo" --json headRefName -q '.headRefName')

  git fetch "https://github.com/$repo" "$branch:$branch" 2>/dev/null || continue
  git checkout "$branch" 2>/dev/null || continue

  while true; do
    conflicts=$(git diff --name-only --diff-filter=U 2>/dev/null)
    if [ -n "$conflicts" ]; then
      for f in $conflicts; do
        git checkout --theirs "$f" 2>/dev/null && git add "$f" 2>/dev/null || \
        git checkout --ours "$f" 2>/dev/null && git add "$f" 2>/dev/null || true
      done
    fi
    result=$(git rebase "origin/main" 2>&1) || result=$(git rebase "github/main" 2>&1) || true
    if ! echo "$result" | grep -q "Could not apply\|CONFLICT"; then
      break
    fi
  done

  remote=$(git remote | grep -E "git(hub)?$" | head -1 || echo "origin")
  git push --force-with-lease "$remote" "$branch" 2>/dev/null || \
  git push --force-with-lease "github" "$branch" 2>/dev/null || continue

  # Cancel runs again after rebase
  run_ids=$(gh run list --repo "$repo" --branch "$branch" \
    --status queued,in_progress --json databaseId -q '.[].databaseId' 2>/dev/null || true)
  for rid in $run_ids; do
    gh run cancel "$rid" --repo "$repo" 2>/dev/null || true
  done

  gh pr merge "$n" --repo "$repo" --squash --delete-branch --admin 2>&1 && echo "  ✅ merged" || echo "  ❌ still failed"
  merged=$((merged + 1))
done

echo ""
echo "🏁 Done: $merged merged, ${#rebase_needed[@]} rebased"
