# Copilot Instructions for `cataggar/nodejs`

## NEVER interact with upstream `nodejs/node`

Do **NOT**:
- Open pull requests, issues, or discussions on `nodejs/node`
- Post comments or reviews on `nodejs/node`
- Push branches or tags to `nodejs/node`

All repository changes, workflows, and releases belong strictly in the `cataggar/nodejs` fork.

## Branch & Release Architecture

- **`release`** (default branch) — Hosts the `ghr`-compatible Node.js release mirror scripts (`scripts/`) and GitHub Actions workflows (`.github/workflows/`), with upstream Node.js's CI workflows removed so fork runs stay lightweight.
- **`main`** — Clean mirror of upstream `https://github.com/nodejs/node.git` (`refs/heads/main`), updated daily by `.github/workflows/sync-main.yml`.

## Mirroring Official Node.js Releases for `ghr`

- Manual copy of any tag (for example `v26.13.1`): dispatch `.github/workflows/publish.yml` with `tag=v26.13.1`.
- Automatic daily check for the newest stable release: `.github/workflows/check-release.yml` runs `scripts/check_node_release.py` and dispatches `publish.yml` only when a newer highest stable `vX.Y.Z` release appears on `https://nodejs.org/dist/index.json`.
- Verification:
  1. `scripts/download_assets.py` verifies `SHASUMS256.txt.asc` with `gpgv` against `nodejs/release-keys` (`gpg/pubring.kbx`), downloads only `ghr`-compatible platform archives (`linux-x64`, `linux-x64-musl`, `linux-arm64`, `darwin-arm64`, `darwin-x64`, `win-x64`, `win-arm64`), and checks every archive's SHA-256 digest before placing it in `dist/`.
  2. Upstream `SHASUMS256*` files are intentionally not uploaded to GitHub Releases; instead, `.github/workflows/publish.yml` generates SLSA v1 GitHub Artifact Attestations via `actions/attest-build-provenance` so `ghr install cataggar/nodejs@<tag>` verifies provenance natively.

## Testing Scripts Locally

Run unit tests via `uv`:

```sh
uv run --no-project --with requests python -m unittest discover -s scripts -p "test_*.py" -v
```
