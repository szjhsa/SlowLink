import time
import unittest
from pathlib import Path

from tests.test_v13893_exclude_texts import load_matcher
from tests.test_v13871_cpu_dedup_stability import load_code_rules_with_fake_redis


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
EXPECTED_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8-sig").strip()


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


class SafetyFixesV13912Tests(unittest.TestCase):
    def test_versions_are_bumped_to_current_release(self):
        self.assertEqual(read(ROOT / "VERSION").strip(), EXPECTED_VERSION)
        self.assertIn(f'APP_VERSION = "{EXPECTED_VERSION}"', read(APP / "config.py"))

    def test_listener_holds_session_lock_and_login_is_guarded(self):
        bot_runner = read(APP / "bot_runner.py")
        login = read(APP / "telegram_login.py")
        web = read(APP / "web.py")

        self.assertIn("with SESSION_LOCK:", bot_runner)
        self.assertIn("self.loop.run_until_complete(self._run())", bot_runner)
        self.assertIn("_listener_running", login)
        self.assertEqual(login.count("请先停止监听后再登录或重新登录"), 2)
        self.assertLess(
            web.index('set_value("listener_desired_state", "stopped")'),
            web.index("manager.stop()"),
        )

    def test_stale_entity_refresh_and_worker_retire_cleanup(self):
        bot_runner = read(APP / "bot_runner.py")

        self.assertIn("_last_force_entity_refresh_ts", bot_runner)
        self.assertIn("await self._refresh_entity_cache(client, force=True)", bot_runner)
        self.assertIn("self.workers.pop(idx)", bot_runner)
        self.assertIn("self._worker_specs.pop(idx)", bot_runner)

    def test_collision_filter_is_atomic_and_dedup_stats_cover_template_keys(self):
        redis_store = read(APP / "redis_store.py")

        self.assertIn("_COLLISION_FILTER_SCRIPT", redis_store)
        self.assertIn("r.eval(_COLLISION_FILTER_SCRIPT", redis_store)
        self.assertIn('"dedup:lottery-template:*"', redis_store)
        self.assertIn('"dedup:collision_exempt:*"', redis_store)
        self.assertIn('list_len("dedup:collisions")', redis_store)

    def test_release_dedup_has_meta_loss_fallback(self):
        dedup = read(APP / "dedup.py")

        self.assertIn('r.scan_iter(match="dedup:*", count=200)', dedup)
        self.assertIn('r.get(k) == dedup_id', dedup)

    def test_catastrophic_user_regex_is_bounded(self):
        matcher = load_matcher(regex_rules={r"^(a|a)+$"})
        started = time.perf_counter()

        result = matcher.match_rule_details("a" * 200 + "!")
        elapsed_ms = (time.perf_counter() - started) * 1000

        self.assertFalse(result["matched"])
        self.assertLess(elapsed_ms, 1000)

    def test_normal_user_regex_still_matches_after_timeout_change(self):
        rule = "🍀 祝所有参与者好运！"
        matcher = load_matcher(regex_rules={rule})
        text = "🍀 祝所有参与者好运！"

        result = matcher.match_rule_details(text)

        self.assertTrue(result["matched"])
        self.assertEqual(result["rule"], rule)

    def test_catastrophic_code_rule_is_bounded(self):
        code_rules, _saved = load_code_rules_with_fake_redis([{
            "name": "坏规则",
            "pattern": r"^(a|a)+$",
            "group": "0",
            "enabled": True,
            "fast": True,
            "trigger": True,
            "strict_context": False,
            "note": "",
        }])
        started = time.perf_counter()

        detail = code_rules.extract_trigger_code_detail("a" * 200 + "!")
        elapsed_ms = (time.perf_counter() - started) * 1000

        self.assertEqual(detail, {})
        self.assertLess(elapsed_ms, 1000)

    def test_regex_dependency_is_pinned(self):
        requirements = read(ROOT / "deploy" / "requirements.txt")
        self.assertIn("regex==", requirements)


if __name__ == "__main__":
    unittest.main()
