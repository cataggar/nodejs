#!/usr/bin/env python3
"""Download and verify ghr-compatible Node.js release archives."""

# /// script
# requires-python = ">=3.12"
# dependencies = ["requests"]
# ///

import argparse
import hashlib
import logging
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import requests  # type: ignore[import-untyped]
from requests.adapters import HTTPAdapter  # type: ignore[import-untyped]
from urllib3.util.retry import Retry  # type: ignore[import-untyped]

DIST_BASE_URL = "https://nodejs.org/dist"
RELEASE_KEYRING_URL = "https://github.com/nodejs/release-keys/raw/refs/heads/main/gpg/pubring.kbx"
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")
SHASUM_LINE_RE = re.compile(r"^([0-9a-fA-F]{64})\s+\*?(\S+)$")
GHR_ARCHIVE_SUFFIXES = frozenset({
    "linux-x64.tar.xz",
    "linux-x64-musl.tar.xz",
    "linux-arm64.tar.xz",
    "linux-arm64-musl.tar.xz",
    "darwin-arm64.tar.xz",
    "darwin-x64.tar.xz",
    "win-x64.zip",
    "win-arm64.zip",
})
MIN_EXPECTED_ARCHIVES = 3

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)


def build_session() -> requests.Session:
    """Create an HTTP session with retry/backoff for transient network failures."""
    session = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "HEAD"}),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def normalize_tag(raw_tag: str) -> tuple[str, str]:
    """Normalize a tag or version (e.g. 'v26.13.1' or '26.13.1') into (version, tag)."""
    cleaned = raw_tag.strip()
    version = cleaned[1:] if cleaned.startswith("v") else cleaned
    if not VERSION_RE.fullmatch(version):
        raise ValueError(f"Invalid Node.js version or tag: {raw_tag!r}")
    return version, f"v{version}"


def is_ghr_compatible_asset(filename: str, tag: str) -> bool:
    """Return True if filename is a ghr-installable Node.js platform archive for tag."""
    prefix = f"node-{tag}-"
    if not filename.startswith(prefix) or "/" in filename or "\\" in filename:
        return False
    suffix = filename[len(prefix):]
    return suffix in GHR_ARCHIVE_SUFFIXES


def parse_shasums(content: str) -> dict[str, str]:
    """Parse verified SHASUMS256.txt lines into a filename -> sha256 map."""
    checksums: dict[str, str] = {}
    for line_no, raw_line in enumerate(content.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        match = SHASUM_LINE_RE.fullmatch(line)
        if not match:
            raise ValueError(f"Malformed SHASUMS256 entry on line {line_no}: {raw_line!r}")
        digest = match.group(1).lower()
        filename = match.group(2)
        existing = checksums.get(filename)
        if existing is not None and existing != digest:
            raise ValueError(f"Conflicting SHA256 entries for {filename}")
        checksums[filename] = digest
    if not checksums:
        raise ValueError("Verified SHASUMS256 content is empty")
    return checksums


def verify_gpg_clearsign(
    asc_path: Path,
    keyring_path: Path,
    verified_out_path: Path,
    gnupg_home: Path,
) -> str:
    """Verify clearsigned SHASUMS256.txt.asc with gpgv against the official Node.js keyring."""
    gnupg_home.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["GNUPGHOME"] = str(gnupg_home.resolve())
    result = subprocess.run(
        [
            "gpgv",
            "--keyring",
            str(keyring_path.resolve()),
            "--output",
            str(verified_out_path.resolve()),
            str(asc_path.resolve()),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or "(no stderr output)"
        raise RuntimeError(f"gpgv failed (exit {result.returncode}): {stderr}")
    if not verified_out_path.is_file() or verified_out_path.stat().st_size == 0:
        raise RuntimeError("gpgv succeeded without producing verified SHASUMS256 output")
    for line in result.stderr.splitlines():
        if line.strip():
            log.info("  %s", line.strip())
    return verified_out_path.read_text(encoding="utf-8")


def download_file(session: requests.Session, url: str, dest: Path) -> None:
    """Stream a URL to dest atomically via a temporary .part file."""
    log.info("  Downloading %s ...", dest.name)
    part_path = dest.with_name(f"{dest.name}.part")
    try:
        with session.get(url, stream=True, timeout=(30, 600)) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            downloaded = 0
            with open(part_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8 * 1024 * 1024):
                    if not chunk:
                        continue
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0:
                        pct = downloaded * 100 // total
                        mb = downloaded // (1024 * 1024)
                        log.info("    %d MB (%d%%)", mb, pct)
        part_path.replace(dest)
    except BaseException:
        part_path.unlink(missing_ok=True)
        raise
    log.info("  Saved %s (%.1f MB)", dest.name, dest.stat().st_size / (1024 * 1024))


def verify_shasum(path: Path, expected: str) -> bool:
    """Verify SHA-256 digest of path against expected lowercase hex digest."""
    sha256 = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            sha256.update(chunk)
    actual = sha256.hexdigest().lower()
    expected_lower = expected.lower()
    if actual != expected_lower:
        log.error("  SHA256 MISMATCH for %s: expected %s, got %s", path.name, expected_lower, actual)
        return False
    log.info("  SHA256 OK: %s", path.name)
    return True


def fetch_verified_shasums(session: requests.Session, tag: str) -> dict[str, str]:
    """Download the Node.js release keyring and SHASUMS256.txt.asc into a temp dir and verify."""
    asc_url = f"{DIST_BASE_URL}/{tag}/SHASUMS256.txt.asc"
    with tempfile.TemporaryDirectory(prefix=f"nodejs-verify-{tag}-") as tmp:
        tmp_dir = Path(tmp)
        keyring_path = tmp_dir / "nodejs-pubring.kbx"
        asc_path = tmp_dir / "SHASUMS256.txt.asc"
        verified_path = tmp_dir / "SHASUMS256.verified.txt"
        gnupg_home = tmp_dir / "gnupg"

        log.info("Downloading Node.js release keyring...")
        download_file(session, RELEASE_KEYRING_URL, keyring_path)

        log.info("Downloading %s ...", asc_url)
        download_file(session, asc_url, asc_path)

        log.info("Verifying OpenPGP signature on SHASUMS256.txt.asc...")
        verified_text = verify_gpg_clearsign(asc_path, keyring_path, verified_path, gnupg_home)
        return parse_shasums(verified_text)


def download_release_assets(raw_tag: str, output_dir: Path) -> list[Path]:
    """Download and verify all ghr-compatible Node.js archives for raw_tag into output_dir."""
    _, tag = normalize_tag(raw_tag)
    output_dir.mkdir(parents=True, exist_ok=True)

    with build_session() as session:
        all_checksums = fetch_verified_shasums(session, tag)
        selected = {
            filename: digest
            for filename, digest in sorted(all_checksums.items())
            if is_ghr_compatible_asset(filename, tag)
        }

        if len(selected) < MIN_EXPECTED_ARCHIVES:
            raise RuntimeError(
                f"Expected at least {MIN_EXPECTED_ARCHIVES} ghr-compatible archives for {tag}, "
                f"found {len(selected)}: {sorted(selected)}"
            )

        # Remove any stale files in output_dir that do not belong to this release's asset set.
        for existing in list(output_dir.iterdir()):
            if existing.is_file() and existing.name not in selected:
                log.info("Removing stale file from %s: %s", output_dir, existing.name)
                existing.unlink()

        log.info("Downloading ghr-compatible assets for Node.js %s (%d archives)", tag, len(selected))

        failed: list[str] = []
        downloaded_paths: list[Path] = []

        for filename, expected_sha in selected.items():
            dest = output_dir / filename
            asset_url = f"{DIST_BASE_URL}/{tag}/{filename}"
            log.info("[%s]", filename)

            try:
                if dest.is_file() and verify_shasum(dest, expected_sha):
                    log.info("  Already downloaded and verified, skipping")
                    downloaded_paths.append(dest)
                    continue

                dest.unlink(missing_ok=True)
                download_file(session, asset_url, dest)
                if not verify_shasum(dest, expected_sha):
                    dest.unlink(missing_ok=True)
                    failed.append(filename)
                    continue

                downloaded_paths.append(dest)
            except Exception as exc:
                dest.unlink(missing_ok=True)
                log.error("  FAILED %s: %s", filename, exc)
                failed.append(filename)

        if failed:
            raise RuntimeError(f"{len(failed)} asset download(s) failed: {failed}")

    files = sorted(p for p in output_dir.iterdir() if p.is_file())
    total_size = sum(f.stat().st_size for f in files)
    log.info("Done! %d verified archives in %s/ (%.1f MB total)", len(files), output_dir, total_size / (1024**2))
    for f in files:
        log.info("  %s (%.1f MB)", f.name, f.stat().st_size / (1024**2))
    return files


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download and verify ghr-compatible Node.js release archives.",
    )
    parser.add_argument(
        "tag",
        help="Node.js release tag or version (e.g. v26.13.1 or 26.13.1)",
    )
    parser.add_argument(
        "--output-dir",
        default="dist",
        type=Path,
        help="Directory to write verified release archives into (default: dist)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    try:
        download_release_assets(args.tag, args.output_dir)
    except Exception as exc:
        log.error("%s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
