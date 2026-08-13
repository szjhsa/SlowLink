import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIBRARY = ROOT / "scripts" / "distribution_lib.sh"


def function_body(source: str, name: str) -> str:
    match = re.search(
        rf"{re.escape(name)}\(\) \{{(?P<body>.*?)(?=\n\}})",
        source,
        flags=re.S,
    )
    if match is None:
        raise AssertionError(f"missing shell function: {name}")
    return match.group("body")


class DependencyBuildFreshnessV13897Tests(unittest.TestCase):
    def test_version_metadata_is_consistent(self):
        version = (ROOT / "VERSION").read_text(encoding="utf-8-sig").strip()
        config = (ROOT / "app" / "config.py").read_text(encoding="utf-8-sig")

        self.assertRegex(version, r"^\d+\.\d+(?:\.\d+)?$")
        self.assertIn(f'APP_VERSION = "{version}"', config)

    def test_release_copy_replaces_program_files_before_copying(self):
        source = LIBRARY.read_text(encoding="utf-8-sig")
        body = function_body(source, "copy_release_files")

        self.assertIn("for program_path in $PROGRAM_PATHS; do", body)
        self.assertIn('rm -rf -- "$INSTALL_DIR/$program_path"', body)
        self.assertLess(
            body.index('rm -rf -- "$INSTALL_DIR/$program_path"'),
            body.index('cp -a "$stage"/. "$INSTALL_DIR"/'),
        )

    def test_primary_app_build_does_not_reuse_dependency_cache(self):
        source = LIBRARY.read_text(encoding="utf-8-sig")
        body = function_body(source, "deploy_application")
        build_commands = re.findall(
            r'compose build(?: --no-cache)? "\$APP_SERVICE"',
            body,
        )

        self.assertGreaterEqual(len(build_commands), 1)
        self.assertEqual(
            build_commands[0],
            'compose build --no-cache "$APP_SERVICE"',
        )


if __name__ == "__main__":
    unittest.main()
