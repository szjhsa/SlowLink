import os
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
sys.path.insert(0, str(APP))
os.environ.setdefault("SLOWLINK_ACTIVE_PLUGIN", "builtin")

import rule_policy
from tests.test_v13883_cross_template_lottery_dedup import load_dedup


class PolicyDrivenDedupV110Tests(unittest.TestCase):
    def test_code_policy_uses_complete_code_identity(self):
        dedup, client = load_dedup()
        policy = rule_policy.default_policy("code")

        first, _, first_profile = dedup.check_and_mark(
            "前缀 A\nWlao-30-Register_Ab12Cd34Ef",
            "",
            None,
            "strict",
            "来源A",
            policy=policy,
            code_identities=["strong_register_renew:Wlao-30-Register_Ab12Cd34Ef"],
        )
        second, reason, second_profile = dedup.check_and_mark(
            "完全不同说明\nWlao-30-Register_Ab12Cd34Ef",
            "",
            None,
            "strict",
            "来源B",
            policy=policy,
            code_identities=["strong_register_renew:Wlao-30-Register_Ab12Cd34Ef"],
        )

        self.assertFalse(first)
        self.assertTrue(second)
        self.assertEqual(first_profile["dedup_id"], second_profile["dedup_id"])
        self.assertTrue(second_profile["dedup_id"].startswith("code:"))

    def test_lottery_policy_ignores_unrelated_code_identity(self):
        policy = rule_policy.default_policy("lottery")

        self.assertFalse(rule_policy.should_run_code_dedup(policy))
        self.assertTrue(rule_policy.should_run_code_dedup(rule_policy.default_policy("code")))
        self.assertTrue(rule_policy.should_run_code_dedup({}))

    def test_keyword_policy_uses_normalized_text_and_policy_ttl(self):
        dedup, _client = load_dedup()
        policy = rule_policy.default_policy("keyword")
        policy["ttl_minutes"] = 60

        profile = dedup.build_profile(
            "今日开放注册 100 个\n祝你好运",
            "",
            "",
            policy=policy,
        )

        self.assertEqual(profile["dedup_strategy"], "normalized_text")
        self.assertEqual(profile["ttl_minutes"], 60)
        self.assertTrue(profile["dedup_id"].startswith("text:"))

    def test_exclude_policy_disables_dedup_registration(self):
        dedup, _client = load_dedup()
        policy = rule_policy.default_policy("exclude")

        duplicate, reason, profile = dedup.check_and_mark(
            "暂时停止注册",
            "",
            None,
            "strict",
            "来源",
            policy=policy,
        )

        self.assertFalse(duplicate)
        self.assertEqual(profile["dedup_strategy"], "none")
        self.assertEqual(reason, "该类型已设置为不去重")

    def test_bot_runner_binds_matched_policy_to_dedup(self):
        source = (APP / "bot_runner.py").read_text(encoding="utf-8-sig")

        self.assertIn('rule_policy = analysis.get("rule_policy") or {}', source)
        self.assertIn("should_run_code_dedup(rule_policy)", source)
        self.assertIn("policy=rule_policy", source)
        self.assertIn("code_identities=code_identities", source)
        self.assertIn("effective_code_minutes", source)


if __name__ == "__main__":
    unittest.main()
