import re
import unittest
from pathlib import Path

from tests.test_v139_chinese_guess_codes import (
    CURRENT_WHITELIST_RULE,
    FakeRedisClient,
    WHITELIST_CODES,
    load_modules,
    load_redis_store,
)


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
EXPECTED_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8-sig").strip()
PURE_SAMPLE = "WindMoon-Whitelist_1OTb0O0FMO"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


class WhitelistPureTriggerV13923Tests(unittest.TestCase):
    def test_versions_are_bumped_to_current_release(self):
        self.assertEqual(read(ROOT / "VERSION").strip(), EXPECTED_VERSION)
        self.assertIn(f'APP_VERSION = "{EXPECTED_VERSION}"', read(APP / "config.py"))

    def test_combined_whitelist_rule_matches_pure_and_guess_codes(self):
        _redis_store = load_redis_store(FakeRedisClient(set()))
        compiled = re.compile(_redis_store.SAFE_WHITELIST_TRIGGER_RULE, re.I | re.M)

        self.assertIsNotNone(compiled.search(PURE_SAMPLE))
        for code in WHITELIST_CODES:
            with self.subTest(code=code):
                self.assertIsNotNone(compiled.search(code))

    def test_pure_whitelist_triggers_through_matcher(self):
        redis_store = load_redis_store(FakeRedisClient(set()))
        _code_rules, matcher = load_modules(
            regex_rules={redis_store.SAFE_WHITELIST_TRIGGER_RULE}
        )

        result = matcher.analyze_message(PURE_SAMPLE)

        self.assertTrue(result.get("matched"))
        self.assertIn("Whitelist", result.get("rule", ""))

    def test_guess_only_whitelist_rule_migrates_to_combined_rule(self):
        redis_store = load_redis_store(FakeRedisClient(set()))
        old_guess = redis_store.LEGACY_GUESS_WHITELIST_TRIGGER_RULE
        client = FakeRedisClient({old_guess})
        store = load_redis_store(client)

        store.ensure_defaults()

        rules = client.smembers("regex_rules")
        self.assertNotIn(old_guess, rules)
        self.assertIn(store.SAFE_WHITELIST_TRIGGER_RULE, rules)
        compiled = re.compile(store.SAFE_WHITELIST_TRIGGER_RULE, re.I | re.M)
        self.assertIsNotNone(compiled.search(PURE_SAMPLE))
        for code in WHITELIST_CODES:
            with self.subTest(code=code):
                self.assertIsNotNone(compiled.search(code))

    def test_legacy_pure_whitelist_rule_still_migrates(self):
        client = FakeRedisClient({CURRENT_WHITELIST_RULE})
        store = load_redis_store(client)

        store.ensure_defaults()

        rules = client.smembers("regex_rules")
        self.assertNotIn(CURRENT_WHITELIST_RULE, rules)
        self.assertIn(store.SAFE_WHITELIST_TRIGGER_RULE, rules)


if __name__ == "__main__":
    unittest.main()
