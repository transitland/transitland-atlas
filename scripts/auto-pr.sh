#!/usr/bin/env bash
#
# Open or update an automated PR on the current repo.
#
# If the working tree has no changes, exits 0 silently.
#
# Otherwise:
#   - If an open PR already exists with --branch-name as its head:
#     fetches that branch, commits the working-tree changes on top,
#     force-pushes (with lease).
#   - Else: creates a fresh branch, commits the changes, pushes it,
#     opens a new PR.
#
# Either way, optionally triggers a workflow on the new/updated branch
# (default: validate.yaml; pass --trigger-workflow '' to skip).
#
# Requires: a checked-out git repo and `gh` authenticated with rights to
# push to the repo and create/list PRs (GH_TOKEN in CI).
#
# Usage:
#   scripts/auto-pr.sh \
#     --branch-name auto/gbfs \
#     --pr-title "Automatic update of GBFS feeds" \
#     --pr-body "Automatically updated GBFS feeds from <url>" \
#     --commit-message "Updated GBFS feeds at $(date -u)"

set -euo pipefail

BRANCH_NAME=""
PR_TITLE=""
PR_BODY=""
COMMIT_MESSAGE=""
BASE_BRANCH="main"
GIT_USER_NAME="Automated Bot"
GIT_USER_EMAIL="info@interline.io"
TRIGGER_WORKFLOW="validate.yaml"

usage() {
  cat >&2 <<EOF
Usage: $(basename "$0") --branch-name NAME --pr-title TITLE --pr-body BODY --commit-message MSG [options]

Required:
  --branch-name NAME       Branch to push (e.g. auto/gbfs)
  --pr-title TITLE         PR title used when creating a new PR
  --pr-body BODY           PR body used when creating a new PR
  --commit-message MSG     Commit message

Options:
  --base-branch NAME       Base branch for new PRs (default: $BASE_BRANCH)
  --git-user-name NAME     Git committer name (default: "$GIT_USER_NAME")
  --git-user-email EMAIL   Git committer email (default: "$GIT_USER_EMAIL")
  --trigger-workflow FILE  Workflow file to run on the pushed branch
                           after commit (default: $TRIGGER_WORKFLOW;
                           pass empty string to skip)
EOF
  exit 1
}

while [ $# -gt 0 ]; do
  case "$1" in
    --branch-name) BRANCH_NAME="$2"; shift 2;;
    --pr-title) PR_TITLE="$2"; shift 2;;
    --pr-body) PR_BODY="$2"; shift 2;;
    --commit-message) COMMIT_MESSAGE="$2"; shift 2;;
    --base-branch) BASE_BRANCH="$2"; shift 2;;
    --git-user-name) GIT_USER_NAME="$2"; shift 2;;
    --git-user-email) GIT_USER_EMAIL="$2"; shift 2;;
    --trigger-workflow) TRIGGER_WORKFLOW="$2"; shift 2;;
    -h|--help) usage;;
    *) echo "Unknown argument: $1" >&2; usage;;
  esac
done

if [ -z "$BRANCH_NAME" ];     then echo "Error: --branch-name is required"     >&2; usage; fi
if [ -z "$PR_TITLE" ];        then echo "Error: --pr-title is required"        >&2; usage; fi
if [ -z "$PR_BODY" ];         then echo "Error: --pr-body is required"         >&2; usage; fi
if [ -z "$COMMIT_MESSAGE" ];  then echo "Error: --commit-message is required"  >&2; usage; fi

git config user.name "$GIT_USER_NAME"
git config user.email "$GIT_USER_EMAIL"

if [ -z "$(git status --porcelain)" ]; then
  echo "Working tree is clean — nothing to commit."
  exit 0
fi

EXISTING_PR=$(gh pr list --head "$BRANCH_NAME" --state open --json number --jq '.[0].number // empty')

if [ -n "$EXISTING_PR" ]; then
  echo "Found existing open PR #$EXISTING_PR — updating branch $BRANCH_NAME"
  git fetch origin "$BRANCH_NAME"
  git checkout -b "$BRANCH_NAME" "origin/$BRANCH_NAME" 2>/dev/null || git checkout -b "$BRANCH_NAME"
else
  echo "No existing PR — creating new branch $BRANCH_NAME"
  # Defensive: clear any stale remote branch with this name, in case a
  # prior PR was closed without the branch being deleted.
  git push origin --delete "$BRANCH_NAME" 2>/dev/null || true
  git checkout -b "$BRANCH_NAME"
fi

git add -A
git commit -m "$COMMIT_MESSAGE"

if [ -n "$EXISTING_PR" ]; then
  git push --force-with-lease origin "$BRANCH_NAME"
else
  git push --set-upstream origin "$BRANCH_NAME"
  gh pr create \
    --title "$PR_TITLE" \
    --body "$PR_BODY" \
    --base "$BASE_BRANCH"
fi

if [ -n "$TRIGGER_WORKFLOW" ]; then
  gh workflow run "$TRIGGER_WORKFLOW" --ref "$BRANCH_NAME"
fi
