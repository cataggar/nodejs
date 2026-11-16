#!/usr/bin/env python3
"""Check for new stable Node.js releases and signal when a new release should be mirrored."""

# /// script
# requires-python = ">=3.12"
# dependencies = ["requests"]
# ///

import json
import logging
import os
import re

import requests  # type: ignore[import-untyped]

INDEX_URL = "https://nodejs.org/dist/index.json"
STABLE_TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
GHR_INDEX_PLATFORMS = frozenset({
    "linux-x64",
    "linux-x64-musl",
    "linux-arm64",
    "linux-arm64-musl",
    "osx-arm64-tar",
    "osx-x64-tar",
    "win-x64-zip",
    "win-arm64-zip",
})
MIN_GHR_PLATFORMS = 3

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)


def github_headers() -> dict[str, str]:
    """Build headers for GitHub API requests."""
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def release_exists(repo: str, tag: str) -> bool:
    """Check if a published (non-draft) GitHub Release already exists for tag."""
    url = f"https://api.github.com/repos/{repo}/releases/tags/{tag}"
    resp = requests.get(url, headers=github_headers(), timeout=30)
    if resp.status_code == 200:
        data = resp.json()
        return not bool(data.get("draft", False))
    if resp.status_code == 404:
        return False
    resp.raise_for_status()
    return False


def set_github_output(name: str, value: str) -> None:
    """Write a key=value pair to $GITHUB_OUTPUT."""
    output_file = os.environ.get("GITHUB_OUTPUT")
    if output_file:
        with open(output_file, "a", encoding="utf-8") as f:
            f.write(f"{name}={value}\n")
    else:
        log.info("GITHUB_OUTPUT not set (running locally?); would set %s=%s", name, value)


def fetch_index() -> list[dict]:
    """Fetch the Node.js download index."""
    resp = requests.get(INDEX_URL, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        raise ValueError(f"Unexpected index.json payload type: {type(data).__name__}")
    return data


def parse_stable_version(tag: str) -> tuple[int, int, int] | None:
    """Parse a stable vX.Y.Z tag into a numeric tuple, rejecting pre-releases."""
    match = STABLE_TAG_RE.fullmatch(tag)
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def count_ghr_platforms(entry: dict) -> int:
    """Count ghr-installable platform entries advertised in index.json files."""
    files = entry.get("files")
    if not isinstance(files, list):
        return 0
    return sum(1 for item in files if item in GHR_INDEX_PLATFORMS)


def main() -> None:
    our_repo = os.environ.get("GITHUB_REPOSITORY", "cataggar/nodejs")
    log.info("Checking for new stable Node.js releases...")

    index = fetch_index()

    # Only mirror the highest stable release; never backfill older releases automatically.
    stable_entries = [
        entry
        for entry in index
        if isinstance(entry, dict) and parse_stable_version(str(entry.get("version", ""))) is not None
    ]
    if not stable_entries:
        log.warning("No stable releases found in the download index")
        set_github_output("new_versions", "")
        return

    latest_entry = max(
        stable_entries,
        key=lambda entry: parse_stable_version(str(entry["version"])) or (0, 0, 0),
    )
    tag_name = str(latest_entry["version"])

    if release_exists(our_repo, tag_name):
        log.info("Latest version %s already mirrored — nothing to do", tag_name)
        set_github_output("new_versions", "")
        return

    platform_count = count_ghr_platforms(latest_entry)
    if platform_count < MIN_GHR_PLATFORMS:
        log.warning("Release %s has only %d ghr platforms — skipping", tag_name, platform_count)
        set_github_output("new_versions", "")
        return

    log.info("New latest release found: %s (%d ghr platforms)", tag_name, platform_count)

    versions_json = json.dumps([tag_name])
    log.info("New version to publish: %s", versions_json)
    set_github_output("new_versions", versions_json)


if __name__ == "__main__":
    main()
