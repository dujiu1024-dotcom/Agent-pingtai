#!/usr/bin/env bash
# Cancel zombie workflow runs that block CI queue capacity.
#
# Zombies are detected by TWO signals, either of which qualifies:
#   1. Run's head branch no longer exists (squash-merged PR, branch deleted)
#   2. Run is in_progress for longer than --max-age minutes (stuck runner)
#
# Usage:
#   scripts/prune-zombie-runs.sh              # default: 30 min max age
#   scripts/prune-zombie-runs.sh --max-age 60 # more tolerant
#   scripts/prune-zombie-runs.sh --dry-run    # list only, don't cancel

set -u

repo="dujiu1024-dotcom/Agent-pingtai"
dry_run=0
max_age_min=30

for arg in "$@"; do
  case "$arg" in
    --dry-run)   dry_run=1 ;;
    --max-age=*) max_age_min="${arg#*=}" ;;
    --max-age)   max_age_min="${2:-30}" ;;
  esac
done

if ! command -v gh &>/dev/null; then
  echo "❌ gh CLI not found."
  exit 1
fi

# Epoch seconds threshold — runs older than this are zombies
threshold_epoch=$(( $(date +%s) - max_age_min * 60 ))

echo "🧟 Pruning zombie runs (deleted branch OR older than ${max_age_min}m)..."
echo ""

# Collect active open PR head branches — their runs are NOT zombies even if old
pr_branches=$(gh pr list --state open --repo "$repo" --json headRefName -q '.[].headRefName' 2>/dev/null || true)

# Convert ISO timestamp to epoch
iso_to_epoch() {
  # Input like: 2026-11-17T12:59:00Z
  local ts="${1%Z}"   # strip trailing Z
  date -j -f "%Y-%m-%dT%H:%M:%S" "$ts" +%s 2>/dev/null || echo 0
}

is_pr_branch() {
  echo "$pr_branches" | grep -Fxq "$1"
}

canceled=0
skipped=0
kept=0

while IFS='|' read -r run_id name branch created; do
  [ -z "$run_id" ] && continue

  reason=""
  branch_active=0
  is_pr_branch "$branch" && branch_active=1

  # Signal 1: branch gone (and not main/master)
  if [ "$branch" != "main" ] && [ "$branch" != "master" ] && [ $branch_active -eq 0 ]; then
    git show-ref --verify --quiet "refs/remotes/github/$branch" 2>/dev/null || \
    git show-ref --verify --quiet "refs/remotes/origin/$branch" 2>/dev/null || \
    reason="branch deleted"
  fi

  # Signal 2: age threshold
  run_epoch=$(iso_to_epoch "$created")
  if [ -z "$reason" ] && [ "$run_epoch" -gt 0 ] && [ "$run_epoch" -lt "$threshold_epoch" ]; then
    reason="age $(( ( $(date +%s) - run_epoch ) / 60 ))m > ${max_age_min}m"
  fi

  if [ -n "$reason" ]; then
    if [ $dry_run -eq 1 ]; then
      echo "  [DRY] would cancel #$run_id  $name  $branch  ($reason)"
      canceled=$((canceled + 1))
    else
      if gh run cancel "$run_id" --repo "$repo" 2>/dev/null; then
        echo "  ✅ canceled #$run_id  $name  $branch  ($reason)"
        canceled=$((canceled + 1))
      else
        echo "  ⚠️  skip  #$run_id  $name  $branch"
        skipped=$((skipped + 1))
      fi
    fi
  else
    kept=$((kept + 1))
  fi
done < <(gh run list --status in_progress --limit 100 --repo "$repo" \
  --json databaseId,name,headBranch,createdAt \
  -q '.[] | "\(.databaseId)|\(.name)|\(.headBranch)|\(.createdAt)"' 2>/dev/null || true)

echo ""
if [ $canceled -eq 0 ] && [ $skipped -eq 0 ]; then
  echo "✅ All clear — no zombies. ($kept active runs)"
else
  echo "🏁 Done: canceled=$canceled  skipped=$skipped  kept=$kept"
fi
