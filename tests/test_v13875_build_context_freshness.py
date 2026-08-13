import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
EXPECTED_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8-sig").strip()


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


class BuildContextFreshnessV13875Tests(unittest.TestCase):
    def test_versions_are_bumped_to_v13875(self):
        self.assertEqual(read(ROOT / "VERSION").strip(), EXPECTED_VERSION)
        self.assertIn(f'APP_VERSION = "{EXPECTED_VERSION}"', read(APP / "config.py"))

    def test_release_copy_refreshes_app_file_timestamps_before_docker_build(self):
        source = read(ROOT / "scripts" / "distribution_lib.sh")
        copy_function = re.search(
            r"copy_release_files\(\) \{(?P<body>.*?)(?=\n\})",
            source,
            flags=re.S,
        )
        self.assertIsNotNone(copy_function)
        body = copy_function.group("body")
        self.assertIn('find "$INSTALL_DIR/app" -type f -exec touch {} +', body)
        self.assertLess(body.index('cp -a "$stage"/. "$INSTALL_DIR"/'), body.index('find "$INSTALL_DIR/app"'))

    def test_deploy_verifies_container_version_and_rebuilds_once_on_mismatch(self):
        source = read(ROOT / "scripts" / "distribution_lib.sh")

        self.assertIn("verify_container_version()", source)
        self.assertIn('expected_version=$(cat "$INSTALL_DIR/VERSION")', source)
        self.assertIn("import config; print(config.APP_VERSION)", source)
        self.assertIn("容器版本与发布版本不一致", source)
        self.assertIn('compose build --no-cache "$APP_SERVICE"', source)
        self.assertIn('compose up -d --no-deps "$APP_SERVICE"', source)
        self.assertIn("容器版本校验失败", source)
        self.assertNotIn("docker compose down", source)


if __name__ == "__main__":
    unittest.main()
