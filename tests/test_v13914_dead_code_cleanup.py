import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
EXPECTED_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8-sig").strip()


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


class DeadCodeCleanupV13914Tests(unittest.TestCase):
    def test_versions_are_bumped_to_current_release(self):
        self.assertEqual(read(ROOT / "VERSION").strip(), EXPECTED_VERSION)
        self.assertIn(f'APP_VERSION = "{EXPECTED_VERSION}"', read(APP / "config.py"))

    def test_truly_unused_functions_and_constants_are_removed(self):
        redis_store = read(APP / "redis_store.py")
        matcher = read(APP / "matcher.py")
        code_rules = read(APP / "code_rules.py")
        dedup = read(APP / "dedup.py")
        config = read(APP / "config.py")

        self.assertNotIn("def now_ts", redis_store)
        self.assertNotIn("def extract_code_identity", code_rules)
        self.assertNotIn("def is_usage_notice", matcher)
        self.assertNotIn("def is_closed_register_notice", matcher)
        self.assertNotIn("def is_registration_success_notice", matcher)
        self.assertNotIn("URL_RE", dedup)
        self.assertNotIn("APP_NAME", config)
        self.assertNotIn('"keywords"', matcher)

    def test_unused_imports_are_removed(self):
        redis_store = read(APP / "redis_store.py")
        web = read(APP / "web.py")
        bot_runner = read(APP / "bot_runner.py")
        link_builder = read(APP / "link_builder.py")
        telegram_login = read(APP / "telegram_login.py")
        code_rules = read(APP / "code_rules.py")

        self.assertNotIn("import os\n", redis_store)
        self.assertNotIn("import sys\n", redis_store)
        self.assertNotIn("from typing import Any, Iterable", redis_store)
        self.assertNotIn("from typing import Iterable", link_builder)
        self.assertNotIn("from code_rules import extract_code_detail", bot_runner)
        self.assertNotIn("ttl_minutes_for_activity", web)
        self.assertNotIn("WEB_HOST", web)
        self.assertNotIn("WEB_PORT", web)
        self.assertNotIn("scan_keys", web)
        self.assertNotIn(", r\n", telegram_login)
        self.assertNotIn("import json\n", code_rules)

    def test_legacy_test_support_fallbacks_are_kept_on_purpose(self):
        dedup = read(APP / "dedup.py")
        web = read(APP / "web.py")

        self.assertIn("except (ImportError, AttributeError):", dedup)
        self.assertIn("def _clear_collisions()", web)


if __name__ == "__main__":
    unittest.main()
