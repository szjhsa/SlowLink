import re
import unittest
from pathlib import Path

from tests.test_v139_chinese_guess_codes import FakeRedisClient, load_modules, load_redis_store


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
EXPECTED_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8-sig").strip()
MESSAGE = "\U0001f3b0 **\u3010\u62bd\u5956\u3011**\n\n\U0001f381 **\u5956\u54c1\u5185\u5bb9:**\nMDL \u6ce8\u518c\u7801"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


class MarkdownLotteryTriggerV13928Tests(unittest.TestCase):
    def test_versions_are_bumped_to_current_release(self):
        self.assertEqual(read(ROOT / "VERSION").strip(), EXPECTED_VERSION)
        self.assertIn(f'APP_VERSION = "{EXPECTED_VERSION}"', read(APP / "config.py"))

    def test_old_prize_content_rule_migrates_and_matches_markdown(self):
        redis_store = load_redis_store(FakeRedisClient(set()))
        old_rule = redis_store.LEGACY_PRIZE_CONTENT_TRIGGER_RULE
        client = FakeRedisClient({old_rule})
        store = load_redis_store(client)

        store.ensure_defaults()

        rules = client.smembers("regex_rules")
        self.assertNotIn(old_rule, rules)
        self.assertIn(store.SAFE_PRIZE_CONTENT_TRIGGER_RULE, rules)
        compiled = re.compile(store.SAFE_PRIZE_CONTENT_TRIGGER_RULE, re.I | re.M)
        self.assertIsNotNone(compiled.search(MESSAGE))

    def test_markdown_prize_content_triggers_through_matcher(self):
        redis_store = load_redis_store(FakeRedisClient(set()))
        _code_rules, matcher = load_modules(
            regex_rules={redis_store.SAFE_PRIZE_CONTENT_TRIGGER_RULE}
        )

        result = matcher.analyze_message(MESSAGE)

        self.assertTrue(result.get("matched"))

    def test_combined_old_rule_migrates(self):
        redis_store = load_redis_store(FakeRedisClient(set()))
        old_combined = redis_store.LEGACY_COMBINED_PRIZE_LOTTERY_RULE
        client = FakeRedisClient({old_combined})
        store = load_redis_store(client)

        store.ensure_defaults()

        rules = client.smembers("regex_rules")
        self.assertNotIn(old_combined, rules)
        self.assertIn(store.SAFE_COMBINED_PRIZE_LOTTERY_RULE, rules)


if __name__ == "__main__":
    unittest.main()
