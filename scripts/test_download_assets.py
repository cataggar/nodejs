import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import download_assets


V26_13_1_FILENAMES = [
    "node-v26.13.1-aix-ppc64.tar.gz",
    "node-v26.13.1-arm64.msi",
    "node-v26.13.1-darwin-arm64.tar.gz",
    "node-v26.13.1-darwin-arm64.tar.xz",
    "node-v26.13.1-darwin-x64.tar.gz",
    "node-v26.13.1-darwin-x64.tar.xz",
    "node-v26.13.1-headers.tar.gz",
    "node-v26.13.1-headers.tar.xz",
    "node-v26.13.1-linux-arm64.tar.gz",
    "node-v26.13.1-linux-arm64.tar.xz",
    "node-v26.13.1-linux-ppc64le.tar.gz",
    "node-v26.13.1-linux-ppc64le.tar.xz",
    "node-v26.13.1-linux-s390x.tar.gz",
    "node-v26.13.1-linux-s390x.tar.xz",
    "node-v26.13.1-linux-x64-musl.tar.gz",
    "node-v26.13.1-linux-x64-musl.tar.xz",
    "node-v26.13.1-linux-x64.tar.gz",
    "node-v26.13.1-linux-x64.tar.xz",
    "node-v26.13.1.pkg",
    "node-v26.13.1.tar.gz",
    "node-v26.13.1.tar.xz",
    "node-v26.13.1-win-arm64.7z",
    "node-v26.13.1-win-arm64.zip",
    "node-v26.13.1-win-x64.7z",
    "node-v26.13.1-win-x64.zip",
    "node-v26.13.1-x64.msi",
    "win-arm64/node.exe",
    "win-arm64/node.lib",
    "win-arm64/node_pdb.7z",
    "win-arm64/node_pdb.zip",
    "win-x64/node.exe",
    "win-x64/node.lib",
    "win-x64/node_pdb.7z",
    "win-x64/node_pdb.zip",
]


class DownloadAssetsTests(unittest.TestCase):
    def test_normalize_tag_accepts_valid_and_rejects_invalid(self) -> None:
        self.assertEqual(download_assets.normalize_tag("v26.13.1"), ("26.13.1", "v26.13.1"))
        self.assertEqual(download_assets.normalize_tag("26.13.1"), ("26.13.1", "v26.13.1"))
        self.assertEqual(
            download_assets.normalize_tag(" v27.0.0-alpha.0.1.0 "),
            ("27.0.0-alpha.0.1.0", "v27.0.0-alpha.0.1.0"),
        )
        for invalid in ("", "v26.13", "../v26.13.1", "https://nodejs.org/dist/v26.13.1", "v26.13.1;rm"):
            with self.assertRaises(ValueError, msg=invalid):
                download_assets.normalize_tag(invalid)

    def test_filters_only_ghr_compatible_archives_for_v26_13_1(self) -> None:
        matched = [
            name for name in V26_13_1_FILENAMES if download_assets.is_ghr_compatible_asset(name, "v26.13.1")
        ]
        self.assertEqual(
            matched,
            [
                "node-v26.13.1-darwin-arm64.tar.xz",
                "node-v26.13.1-darwin-x64.tar.xz",
                "node-v26.13.1-linux-arm64.tar.xz",
                "node-v26.13.1-linux-x64-musl.tar.xz",
                "node-v26.13.1-linux-x64.tar.xz",
                "node-v26.13.1-win-arm64.zip",
                "node-v26.13.1-win-x64.zip",
            ],
        )

    def test_parse_shasums_rejects_malformed_or_conflicting_lines(self) -> None:
        digest_a = "a" * 64
        digest_b = "b" * 64
        parsed = download_assets.parse_shasums(
            f"{digest_a}  node-v26.13.1-linux-x64.tar.xz\n"
            f"{digest_b.upper()} *node-v26.13.1-win-x64.zip\n"
        )
        self.assertEqual(
            parsed,
            {
                "node-v26.13.1-linux-x64.tar.xz": digest_a,
                "node-v26.13.1-win-x64.zip": digest_b,
            },
        )

        with self.assertRaises(ValueError):
            download_assets.parse_shasums("not-a-sha256  node-v26.13.1-linux-x64.tar.xz\n")

        with self.assertRaises(ValueError):
            download_assets.parse_shasums(
                f"{digest_a}  node-v26.13.1-linux-x64.tar.xz\n"
                f"{digest_b}  node-v26.13.1-linux-x64.tar.xz\n"
            )

    def test_download_release_assets_verifies_hashes_and_excludes_non_ghr_files(self) -> None:
        payloads = {
            "node-v26.13.1-darwin-arm64.tar.xz": b"darwin-arm64",
            "node-v26.13.1-linux-arm64.tar.xz": b"linux-arm64",
            "node-v26.13.1-linux-x64-musl.tar.xz": b"linux-x64-musl",
            "node-v26.13.1-linux-x64.tar.xz": b"linux-x64",
            "node-v26.13.1-win-x64.zip": b"win-x64",
        }
        shasums = {
            name: hashlib.sha256(data).hexdigest()
            for name, data in payloads.items()
        }
        # Non-ghr entries in SHASUMS256.txt must never be downloaded or left in dist/.
        shasums["node-v26.13.1-linux-x64.tar.gz"] = "c" * 64
        shasums["node-v26.13.1.pkg"] = "d" * 64

        downloaded_urls: list[str] = []

        def fake_download(_session: object, url: str, dest: Path) -> None:
            downloaded_urls.append(url)
            filename = url.rsplit("/", 1)[-1]
            dest.write_bytes(payloads[filename])

        with tempfile.TemporaryDirectory() as tmp:
            dist_dir = Path(tmp) / "dist"
            dist_dir.mkdir()
            (dist_dir / "SHASUMS256.txt").write_text("stale", encoding="utf-8")
            # Pre-populate one valid archive to verify idempotent skip behavior.
            (dist_dir / "node-v26.13.1-linux-x64.tar.xz").write_bytes(payloads["node-v26.13.1-linux-x64.tar.xz"])

            with (
                patch.object(download_assets, "fetch_verified_shasums", return_value=shasums),
                patch.object(download_assets, "download_file", side_effect=fake_download),
            ):
                files = download_assets.download_release_assets("26.13.1", dist_dir)

            self.assertEqual([f.name for f in files], sorted(payloads))
            self.assertFalse((dist_dir / "SHASUMS256.txt").exists())
            self.assertEqual(len(downloaded_urls), len(payloads) - 1)

    def test_download_release_assets_fails_closed_on_sha256_mismatch(self) -> None:
        shasums = {
            "node-v26.13.1-darwin-arm64.tar.xz": hashlib.sha256(b"ok-1").hexdigest(),
            "node-v26.13.1-linux-arm64.tar.xz": hashlib.sha256(b"ok-2").hexdigest(),
            "node-v26.13.1-linux-x64.tar.xz": hashlib.sha256(b"expected").hexdigest(),
        }
        served = {
            "node-v26.13.1-darwin-arm64.tar.xz": b"ok-1",
            "node-v26.13.1-linux-arm64.tar.xz": b"ok-2",
            "node-v26.13.1-linux-x64.tar.xz": b"tampered",
        }

        def fake_download(_session: object, url: str, dest: Path) -> None:
            dest.write_bytes(served[url.rsplit("/", 1)[-1]])

        with tempfile.TemporaryDirectory() as tmp:
            dist_dir = Path(tmp) / "dist"
            with (
                patch.object(download_assets, "fetch_verified_shasums", return_value=shasums),
                patch.object(download_assets, "download_file", side_effect=fake_download),
            ):
                with self.assertRaises(RuntimeError):
                    download_assets.download_release_assets("v26.13.1", dist_dir)

            self.assertFalse((dist_dir / "node-v26.13.1-linux-x64.tar.xz").exists())

    @unittest.skipUnless(shutil.which("gpgv"), "gpgv is required for signature failure test")
    def test_verify_gpg_clearsign_fails_closed_on_invalid_signature(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            keyring = tmp_dir / "empty.kbx"
            keyring.write_bytes(b"")
            asc = tmp_dir / "SHASUMS256.txt.asc"
            asc.write_text(
                "-----BEGIN PGP SIGNED MESSAGE-----\n"
                "Hash: SHA256\n\n"
                f"{'0' * 64}  node-v26.13.1-linux-x64.tar.xz\n"
                "-----BEGIN PGP SIGNATURE-----\n"
                "iQIzBAEBCAAdFiEEinvalid==\n"
                "-----END PGP SIGNATURE-----\n",
                encoding="utf-8",
            )
            verified_out = tmp_dir / "SHASUMS256.verified.txt"
            with self.assertRaises(RuntimeError):
                download_assets.verify_gpg_clearsign(asc, keyring, verified_out, tmp_dir / "gnupg")
            self.assertFalse(verified_out.exists())


if __name__ == "__main__":
    unittest.main()
