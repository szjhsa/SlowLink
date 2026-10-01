import importlib
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"


def load_matcher():
    fake_redis = types.ModuleType("redis_store")
    fake_redis.smembers = lambda key: {r"(?m)^抽奖活动已开始！?$"} if key == "regex_rules" else set()
    fake_redis.get_json = lambda key, default=None: default
    fake_redis.set_json = lambda *args, **kwargs: None

    fake_code_rules = types.ModuleType("code_rules")
    fake_code_rules.extract_code_detail = lambda text: {}
    fake_code_rules.extract_trigger_code_detail = lambda text: {}

    old_redis = sys.modules.get("redis_store")
    old_code = sys.modules.get("code_rules")
    sys.modules["redis_store"] = fake_redis
    sys.modules["code_rules"] = fake_code_rules
    sys.path.insert(0, str(APP))
    try:
        sys.modules.pop("matcher", None)
        return importlib.import_module("matcher")
    finally:
        try:
            sys.path.remove(str(APP))
        except ValueError:
            pass
        if old_redis is None:
            sys.modules.pop("redis_store", None)
        else:
            sys.modules["redis_store"] = old_redis
        if old_code is None:
            sys.modules.pop("code_rules", None)
        else:
            sys.modules["code_rules"] = old_code


class MatcherRulePolicyV110Tests(unittest.TestCase):
    def test_plugin_guard_hook_is_used_before_core_fallback(self):
        matcher = load_matcher()
        matcher.call_hook = lambda *args, **kwargs: {
            "usage_notice": False,
            "closed_register_notice": True,
            "registration_success_notice": False,
        }

        analysis = matcher.analyze_message("注册状态：false")

        self.assertFalse(analysis["matched"])
        self.assertTrue(analysis["closed_register_notice"])

    def test_matching_rule_returns_its_policy(self):
        matcher = load_matcher()
        rule = r"(?m)^抽奖活动已开始！?$"
        policy = {
            "rule_type": "lottery",
            "label": "抽奖",
            "dedup_strategy": "lottery_identity",
            "ttl_minutes": 720,
            "lottery_template_mode": "global",
            "forward": True,
        }
        matcher.get_rule_policy = lambda value: policy if value == rule else None
        matcher.invalidate_rule_cache()

        analysis = matcher.analyze_message("抽奖活动已开始！")

        self.assertTrue(analysis["matched"])
        self.assertEqual(analysis["rule_type"], "lottery")
        self.assertEqual(analysis["rule_policy"], policy)

    def test_legacy_rule_without_policy_still_matches(self):
        matcher = load_matcher()
        matcher.get_rule_policy = lambda value: None
        matcher.invalidate_rule_cache()

        analysis = matcher.analyze_message("抽奖活动已开始！")

        self.assertTrue(analysis["matched"])
        self.assertEqual(analysis["rule_type"], "")
        self.assertEqual(analysis["rule_policy"], {})


if __name__ == "__main__":
    unittest.main()
