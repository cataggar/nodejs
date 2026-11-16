import unittest
from unittest.mock import Mock, patch

import check_node_release


class CheckNodeReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ghr_files = [
            "headers",
            "linux-arm64",
            "linux-ppc64le",
            "linux-s390x",
            "linux-x64",
            "linux-x64-musl",
            "osx-arm64-tar",
            "osx-x64-pkg",
            "osx-x64-tar",
            "src",
            "win-arm64-7z",
            "win-arm64-zip",
            "win-x64-7z",
            "win-x64-msi",
            "win-x64-zip",
        ]

    def run_check(self, index: list[dict], existing_release: bool = False) -> tuple[Mock, Mock]:
        with (
            patch.object(check_node_release, "fetch_index", return_value=index),
            patch.object(check_node_release, "release_exists", return_value=existing_release) as release_exists,
            patch.object(check_node_release, "set_github_output") as output,
            patch.dict(check_node_release.os.environ, {"GITHUB_REPOSITORY": "cataggar/nodejs"}),
        ):
            check_node_release.main()
        return release_exists, output

    def test_selects_latest_stable_version_numerically(self) -> None:
        release_exists, output = self.run_check([
            {"version": "v27.0.0-alpha.0.1.0", "files": self.ghr_files},
            {"version": "v9.11.2", "files": self.ghr_files},
            {"version": "v26.9.0", "files": self.ghr_files},
            {"version": "v26.13.1", "files": self.ghr_files},
            {"version": "v24.15.0", "files": self.ghr_files},
        ])

        release_exists.assert_called_once_with("cataggar/nodejs", "v26.13.1")
        output.assert_called_once_with("new_versions", '["v26.13.1"]')

    def test_does_not_backfill_when_latest_stable_release_exists(self) -> None:
        release_exists, output = self.run_check(
            [
                {"version": "v27.0.0-alpha.0.1.0", "files": self.ghr_files},
                {"version": "v26.13.1", "files": self.ghr_files},
                {"version": "v26.13.0", "files": self.ghr_files},
            ],
            existing_release=True,
        )

        release_exists.assert_called_once_with("cataggar/nodejs", "v26.13.1")
        output.assert_called_once_with("new_versions", "")

    def test_prerelease_only_index_does_not_publish(self) -> None:
        with self.assertLogs(check_node_release.log, level="WARNING") as logs:
            release_exists, output = self.run_check([
                {"version": "v27.0.0-alpha.0.1.0", "files": self.ghr_files},
                {"version": "v27.0.0-rc.1", "files": self.ghr_files},
            ])

        release_exists.assert_not_called()
        output.assert_called_once_with("new_versions", "")
        self.assertIn("No stable releases found", logs.output[0])

    def test_skips_incomplete_release_with_too_few_ghr_platforms(self) -> None:
        with self.assertLogs(check_node_release.log, level="WARNING") as logs:
            release_exists, output = self.run_check([
                {"version": "v26.13.1", "files": ["headers", "src", "linux-x64", "osx-x64-pkg"]},
            ])

        release_exists.assert_called_once_with("cataggar/nodejs", "v26.13.1")
        output.assert_called_once_with("new_versions", "")
        self.assertIn("only 1 ghr platforms", logs.output[0])

    def test_release_exists_handles_published_draft_and_missing(self) -> None:
        published_resp = Mock(status_code=200, json=Mock(return_value={"draft": False}))
        draft_resp = Mock(status_code=200, json=Mock(return_value={"draft": True}))
        missing_resp = Mock(status_code=404)

        with patch.object(check_node_release.requests, "get", side_effect=[published_resp, draft_resp, missing_resp]):
            self.assertTrue(check_node_release.release_exists("cataggar/nodejs", "v26.13.1"))
            self.assertFalse(check_node_release.release_exists("cataggar/nodejs", "v26.13.1"))
            self.assertFalse(check_node_release.release_exists("cataggar/nodejs", "v26.13.1"))


if __name__ == "__main__":
    unittest.main()
