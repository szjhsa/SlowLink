import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
EXPECTED_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8-sig").strip()


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


class StatsAndCsrfV13913Tests(unittest.TestCase):
    def test_versions_are_bumped_to_current_release(self):
        self.assertEqual(read(ROOT / "VERSION").strip(), EXPECTED_VERSION)
        self.assertIn(f'APP_VERSION = "{EXPECTED_VERSION}"', read(APP / "config.py"))

    def test_stats_hashes_are_bounded_and_daily_stats_pruned(self):
        redis_store = read(APP / "redis_store.py")

        self.assertIn("STATS_HASH_KEYS", redis_store)
        self.assertIn("STATS_HASH_MAX_FIELDS", redis_store)
        self.assertIn("DAILY_STATS_RETENTION_DAYS", redis_store)
        self.assertIn("_PRUNE_STATS_SCRIPT", redis_store)
        self.assertIn("def prune_stats_hashes", redis_store)
        self.assertIn("def prune_daily_stats", redis_store)
        self.assertIn("prune_stats_hashes()", redis_store)
        self.assertIn("prune_daily_stats()", redis_store)

    def test_csrf_is_enabled_and_forms_carry_token(self):
        web = read(APP / "web.py")
        login = read(APP / "templates" / "login.html")
        init = read(APP / "templates" / "init.html")
        index = read(APP / "templates" / "index.html")

        self.assertIn("from flask_wtf import CSRFProtect", web)
        self.assertIn("CSRFProtect(app)", web)
        self.assertIn('name="csrf_token"', login)
        self.assertIn('name="csrf_token"', init)
        self.assertIn("const CSRF_TOKEN", index)
        self.assertIn("input.name = 'csrf_token'", index)
        self.assertIn("input.value = CSRF_TOKEN", index)

    def test_flask_wtf_dependency_is_pinned(self):
        requirements = read(ROOT / "deploy" / "requirements.txt")
        self.assertIn("Flask-WTF==", requirements)


if __name__ == "__main__":
    unittest.main()
