#!/usr/bin/env python3
"""
create_release.py — Create a GitHub release via the GitHub REST API.

Usage:
    python create_release.py --repo owner/repo --tag v1.2.3 [options]

Environment:
    GITHUB_TOKEN  Personal access token with 'repo' scope (or set via --token)
"""

import argparse
import json
import os
import sys
import urllib.request
import urllib.error


GITHUB_API = "https://api.github.com"


def github_request(method: str, path: str, token: str, payload: dict = None) -> dict:
    url = f"{GITHUB_API}{path}"
    data = json.dumps(payload).encode() if payload else None

    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "create-release-script/1.0",
        },
    )

    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            detail = json.loads(body).get("message", body)
        except Exception:
            detail = body
        print(f"[ERROR] GitHub API {e.code}: {detail}", file=sys.stderr)
        sys.exit(1)


def get_latest_release_tag(repo: str, token: str) -> str | None:
    """Return the tag of the latest release, or None if no releases exist."""
    try:
        data = github_request("GET", f"/repos/{repo}/releases/latest", token)
        return data.get("tag_name")
    except SystemExit:
        return None


def generate_release_notes(repo: str, token: str, tag: str, target: str, previous_tag: str = None) -> str:
    """Ask GitHub to auto-generate release notes between two tags."""
    payload = {"tag_name": tag, "target_commitish": target}
    if previous_tag:
        payload["previous_tag_name"] = previous_tag

    data = github_request("POST", f"/repos/{repo}/releases/generate-notes", token, payload)
    return data.get("body", "")


def create_release(
    repo: str,
    token: str,
    tag: str,
    name: str,
    body: str,
    target: str,
    draft: bool,
    prerelease: bool,
    generate_notes: bool,
) -> dict:
    payload = {
        "tag_name": tag,
        "name": name or tag,
        "body": body,
        "target_commitish": target,
        "draft": draft,
        "prerelease": prerelease,
        "generate_release_notes": generate_notes and not body,
    }
    return github_request("POST", f"/repos/{repo}/releases", token, payload)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Create a GitHub release.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--repo", required=True, help="Repository in owner/repo format")
    parser.add_argument("--tag", required=True, help="Tag name for the release (e.g. v1.2.3)")
    parser.add_argument("--name", help="Release title (defaults to tag name)")
    parser.add_argument("--body", help="Release notes body text")
    parser.add_argument("--notes-file", help="Path to a file containing release notes")
    parser.add_argument(
        "--target",
        default="main",
        help="Branch or commit SHA to tag (default: main)",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("GITHUB_TOKEN"),
        help="GitHub personal access token (default: $GITHUB_TOKEN env var)",
    )
    parser.add_argument(
        "--draft",
        action="store_true",
        help="Create as a draft (unpublished) release",
    )
    parser.add_argument(
        "--prerelease",
        action="store_true",
        help="Mark as a pre-release",
    )
    parser.add_argument(
        "--auto-notes",
        action="store_true",
        help="Auto-generate release notes from commits since the previous release",
    )
    parser.add_argument(
        "--previous-tag",
        help="Previous tag to compare against for auto-generated notes (optional)",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    if not args.token:
        print(
            "[ERROR] No GitHub token provided. Set GITHUB_TOKEN env var or use --token.",
            file=sys.stderr,
        )
        sys.exit(1)

    if "/" not in args.repo:
        print("[ERROR] --repo must be in owner/repo format.", file=sys.stderr)
        sys.exit(1)

    # Resolve release notes body
    body = args.body or ""

    if args.notes_file:
        try:
            with open(args.notes_file) as f:
                body = f.read()
        except OSError as e:
            print(f"[ERROR] Could not read notes file: {e}", file=sys.stderr)
            sys.exit(1)

    # If auto-notes requested and no manual body provided, generate via GitHub API
    if args.auto_notes and not body:
        previous_tag = args.previous_tag or get_latest_release_tag(args.repo, args.token)
        if previous_tag:
            print(f"Generating release notes from {previous_tag} → {args.tag}...")
        else:
            print("Generating release notes (no previous release found, using full history)...")
        body = generate_release_notes(
            args.repo, args.token, args.tag, args.target, previous_tag
        )

    print(f"Creating release {args.tag} on {args.repo} (target: {args.target})...")

    release = create_release(
        repo=args.repo,
        token=args.token,
        tag=args.tag,
        name=args.name,
        body=body,
        target=args.target,
        draft=args.draft,
        prerelease=args.prerelease,
        generate_notes=args.auto_notes,
    )

    status = "draft" if args.draft else ("pre-release" if args.prerelease else "release")
    print(f"\n✓ {status.capitalize()} created successfully!")
    print(f"  Tag:  {release['tag_name']}")
    print(f"  Name: {release['name']}")
    print(f"  URL:  {release['html_url']}")


if __name__ == "__main__":
    main()
