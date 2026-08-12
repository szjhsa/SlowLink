import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
EXPECTED_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8-sig").strip()


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


class StabilityFixesV13861Tests(unittest.TestCase):
    def test_versions_are_bumped_to_v13861(self):
        self.assertEqual(read(ROOT / "VERSION").strip(), EXPECTED_VERSION)
        self.assertIn(f'APP_VERSION = "{EXPECTED_VERSION}"', read(APP / "config.py"))

    def test_send_failure_releases_pending_dedup_reservations(self):
        bot_runner = read(APP / "bot_runner.py")
        self.assertRegex(bot_runner, r"from dedup import .*release_dedup")
        self.assertIn("def _release_pending_dedup(", bot_runner)

        helper = re.search(
            r"def _release_pending_dedup\(.*?\n    async def _run",
            bot_runner,
            flags=re.S,
        )
        self.assertIsNotNone(helper)
        helper_body = helper.group(0)
        self.assertIn("r.delete(code_key)", helper_body)
        self.assertIn("release_dedup(dedup_id)", helper_body)

        start = bot_runner.index("if failed and not sent:")
        end = bot_runner.index('push_event("error", "命中但发送失败：" + " | ".join(failed[:2]))', start)
        failed_branch = bot_runner[start:end]
        self.assertIn("self._release_pending_dedup(reserved_code_keys, dedup_profile)", failed_branch)

    def test_code_level_dedup_uses_code_minutes_setting(self):
        bot_runner = read(APP / "bot_runner.py")
        self.assertIn('pipe.get("dedup_code_minutes")', bot_runner)
        self.assertIn("enabled, mode, other_minutes, code_minutes", bot_runner)
        self.assertIn("code_ttl = max(60, code_minutes * 60)", bot_runner)

    def test_admin_changes_clear_listener_runtime_cache_immediately(self):
        web = read(APP / "web.py")
        set_target = re.search(r"def set_target\(\):(?P<body>.*?@app\.post\(\"/test_send\"\))", web, flags=re.S)
        self.assertIsNotNone(set_target)
        self.assertIn("manager.clear_runtime_cache()", set_target.group("body"))

        save_dedup = re.search(r"def save_dedup\(\):(?P<body>.*?@app\.post\(\"/release_dedup\"\))", web, flags=re.S)
        self.assertIsNotNone(save_dedup)
        self.assertIn("clear_ttl_cache()", save_dedup.group("body"))
        self.assertIn("manager.clear_runtime_cache()", save_dedup.group("body"))

    def test_cleanup_does_not_delete_persistent_ttl_minus_one_keys(self):
        redis_store = read(APP / "redis_store.py")
        cleanup = re.search(
            r"def cleanup_expired_dedup_keys\(\) -> int:(?P<body>.*?)(?=\n\n\S|\Z)",
            redis_store,
            flags=re.S,
        )
        self.assertIsNotNone(cleanup)
        body = cleanup.group("body")
        self.assertNotIn("<= 0", body)
        self.assertIn("< -1", body)

    def test_clear_dedup_cache_refreshes_stats_between_before_and_after(self):
        web = read(APP / "web.py")
        branch = re.search(
            r"elif kind == \"dedup_cache\":(?P<body>.*?elif kind in \{\"dedup_all\", \"all_safe\"\}:)",
            web,
            flags=re.S,
        )
        self.assertIsNotNone(branch)
        body = branch.group("body")
        self.assertLess(body.index("before = cache_stats()"), body.index("for pattern in ACTIVE_DEDUP_PATTERNS"))
        self.assertIn("clear_stats_cache()", body[body.index("for pattern in ACTIVE_DEDUP_PATTERNS"):body.index("after = cache_stats()")])


if __name__ == "__main__":
    unittest.main()


