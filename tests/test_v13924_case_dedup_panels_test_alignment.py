import re
import json
import hashlib
import unittest
from pathlib import Path

from tests.test_v139_chinese_guess_codes import FakeRedisClient, load_modules, load_redis_store


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
EXPECTED_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8-sig").strip()


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


class CaseDedupPanelsAndTestAlignmentV13924Tests(unittest.TestCase):
    def test_versions_are_bumped_to_current_release(self):
        self.assertEqual(read(ROOT / "VERSION").strip(), EXPECTED_VERSION)
        self.assertIn(f'APP_VERSION = "{EXPECTED_VERSION}"', read(APP / "config.py"))

    def test_code_identity_is_normalized_to_lowercase_for_dedup_key(self):
        code_rules, _saved = load_modules()

        normalized = code_rules.normalize_code_identity(
            "strong_register_renew:NONAY-30-Register_HePfJi5VD6"
        )

        self.assertEqual(normalized, "strong_register_renew:nonay-30-register_hepfji5vd6")

    def test_url_code_identity_is_normalized_without_losing_scheme(self):
        code_rules, _saved = load_modules()
        identity = (
            "strong_register_renew:http://t.me/saturday_lite_bot"
            "?start=SaturDay.Lite-30-Register_cGfV0h5c8q"
        )

        normalized = code_rules.normalize_code_identity(identity)

        self.assertEqual(normalized, identity.lower())
        self.assertNotEqual(normalized, identity)

    def test_identity_lock_and_content_lock_use_separate_namespaces(self):
        code_rules, _saved = load_modules()
        storage = read(APP / "plugins" / "builtin" / "storage_impl.py")
        identity = (
            "strong_register_renew:http://t.me/saturday_lite_bot"
            "?start=saturday.lite-30-register_abcdefghij"
        )
        normalized = code_rules.normalize_code_identity(identity)

        identity_key = "dedup:identity:" + hashlib.sha256(
            normalized.encode("utf-8")
        ).hexdigest()
        content_key = "dedup:code:" + hashlib.sha256(
            identity.encode("utf-8")
        ).hexdigest()

        self.assertEqual(normalized, identity)
        self.assertNotEqual(identity_key, content_key)
        self.assertIn('"identity_key_prefix": "dedup:identity:"', storage)
        self.assertIn('"dedup:identity:*"', storage)

    def test_bot_runner_uses_normalized_identity_and_full_log(self):
        source = read(APP / "bot_runner.py")

        self.assertIn('"normalize_dedup_identity"', source)
        self.assertIn("call_hook(", source)
        self.assertIn("identity_key = identity_key_prefix + sha(normalized_identity)", source)
        storage = read(APP / "plugins" / "builtin" / "storage_impl.py")
        self.assertIn('"identity_key_prefix": "dedup:identity:"', storage)
        self.assertIn("duplicate_identity[:160]", source)
        self.assertNotIn("duplicate_identity[:16]", source)

    def test_exhausted_register_matches_without_separator(self):
        _code_rules, matcher = load_modules()
        normalized = "剩余可注册 0 人"
        rules = json.loads(
            (APP / "plugins" / "builtin" / "rules.json").read_text(
                encoding="utf-8-sig"
            )
        )
        exhausted = re.compile(
            rules["matcher"]["exhausted_register_pattern"],
            re.I,
        )

        self.assertTrue(matcher._is_closed_register_notice(normalized, "剩余可注册0人"))
        self.assertIsNotNone(exhausted.search("剩余可注册 | 0"))

    def test_open_registration_state_rule_migrates_and_supports_variants(self):
        redis_store = load_redis_store(FakeRedisClient(set()))
        old_rule = redis_store.LEGACY_OPEN_REGISTRATION_STATE_RULE
        client = FakeRedisClient({old_rule})
        store = load_redis_store(client)

        store.ensure_defaults()

        rules = client.smembers("regex_rules")
        self.assertNotIn(old_rule, rules)
        self.assertIn(store.SAFE_OPEN_REGISTRATION_STATE_RULE, rules)
        compiled = re.compile(store.SAFE_OPEN_REGISTRATION_STATE_RULE, re.I | re.M)
        self.assertIsNotNone(compiled.search("当前开注状态 | ON"))
        self.assertIsNotNone(compiled.search("开注状态：开启"))
        self.assertIsNotNone(compiled.search("开注状态: 1"))

    def test_open_registration_state_triggers_through_matcher(self):
        redis_store = load_redis_store(FakeRedisClient(set()))
        _code_rules, matcher = load_modules(
            regex_rules={redis_store.SAFE_OPEN_REGISTRATION_STATE_RULE}
        )

        result = matcher.analyze_message("当前开注状态 | ON")

        self.assertTrue(result.get("matched"))
        self.assertIn("开注状态", result.get("rule", ""))

    def test_web_regex_test_uses_online_analyze_path(self):
        source = read(APP / "web.py")

        self.assertIn("analysis = analyze_message(text)", source)
        self.assertIn("from matcher import analyze_message", source)


if __name__ == "__main__":
    unittest.main()
