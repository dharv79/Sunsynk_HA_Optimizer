# Scripts

Helper scripts for repository maintenance.

## `create_release.sh`

Create a GitHub release programmatically.

### Setup (once)

Set a GitHub personal access token with `repo` scope, either:

```bash
export GITHUB_TOKEN=ghp_yourtokenhere
```

…or use the GitHub CLI:

```bash
gh auth login
```

The script will fall back to `gh auth token` automatically if `$GITHUB_TOKEN` is not set.

### Usage

```bash
./scripts/create_release.sh <tag> <target_branch> [notes_file]
```

Example — publish the v1.0.8b4 beta from the current dev branch:

```bash
./scripts/create_release.sh v1.0.8b4 \
  claude/post-beta-release-slack-gbTnT \
  scripts/release_notes/v1.0.8b4.md
```

The pre-release flag is set automatically when the tag contains `b`, `rc`, `alpha`, or `beta`.

Notes can also be piped on stdin:

```bash
echo "Quick fix release" | ./scripts/create_release.sh v1.0.9 main
```
