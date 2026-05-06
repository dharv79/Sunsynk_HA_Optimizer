#!/usr/bin/env bash
# Create a GitHub release for Sunsynk_HA_Optimizer.
#
# Usage:
#   GITHUB_TOKEN=ghp_xxx ./scripts/create_release.sh <tag> <target_branch> [notes_file]
#
# Examples:
#   ./scripts/create_release.sh v1.0.8b4 claude/post-beta-release-slack-gbTnT notes.md
#   ./scripts/create_release.sh v1.0.9 main RELEASE_NOTES.md
#
# Tags containing 'b' (beta), 'rc' (release candidate) or 'alpha' are marked as pre-release.
# Notes can also be piped on stdin if no notes_file is provided.
#
# A token can be supplied either via $GITHUB_TOKEN env var or `gh auth token`.

set -euo pipefail

OWNER="dharv79"
REPO="Sunsynk_HA_Optimizer"

TAG="${1:-}"
TARGET="${2:-main}"
NOTES_FILE="${3:-}"

if [[ -z "$TAG" ]]; then
  echo "Error: tag required" >&2
  echo "Usage: $0 <tag> <target_branch> [notes_file]" >&2
  exit 1
fi

# Resolve token: env var first, then `gh auth token`.
if [[ -z "${GITHUB_TOKEN:-}" ]]; then
  if command -v gh >/dev/null 2>&1; then
    GITHUB_TOKEN=$(gh auth token 2>/dev/null || true)
  fi
fi

if [[ -z "${GITHUB_TOKEN:-}" ]]; then
  echo "Error: no GitHub token. Set GITHUB_TOKEN or run 'gh auth login'." >&2
  exit 1
fi

# Detect prerelease from tag name.
PRERELEASE=false
if [[ "$TAG" =~ b[0-9]+ || "$TAG" =~ rc[0-9]+ || "$TAG" =~ alpha || "$TAG" =~ beta ]]; then
  PRERELEASE=true
fi

# Load release notes.
if [[ -n "$NOTES_FILE" ]]; then
  if [[ ! -f "$NOTES_FILE" ]]; then
    echo "Error: notes file not found: $NOTES_FILE" >&2
    exit 1
  fi
  BODY=$(cat "$NOTES_FILE")
elif [[ ! -t 0 ]]; then
  BODY=$(cat)  # piped on stdin
else
  BODY=""
fi

# Build payload using python so quotes/newlines are handled safely.
PAYLOAD=$(python3 -c "
import json, sys
print(json.dumps({
    'tag_name': sys.argv[1],
    'target_commitish': sys.argv[2],
    'name': sys.argv[1],
    'body': sys.argv[3],
    'draft': False,
    'prerelease': sys.argv[4] == 'true',
}))
" "$TAG" "$TARGET" "$BODY" "$PRERELEASE")

echo "Creating release $TAG (prerelease=$PRERELEASE) on $OWNER/$REPO target=$TARGET..."

RESPONSE=$(curl -sS -X POST \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer $GITHUB_TOKEN" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD" \
  "https://api.github.com/repos/$OWNER/$REPO/releases")

URL=$(echo "$RESPONSE" | python3 -c "import json,sys;print(json.load(sys.stdin).get('html_url',''))")

if [[ -n "$URL" ]]; then
  echo "Release created: $URL"
else
  echo "Failed to create release. API response:" >&2
  echo "$RESPONSE" >&2
  exit 1
fi
