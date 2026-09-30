import os
import re
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
sys.path.insert(0, str(APP))
os.environ.setdefault("SLOWLINK_ACTIVE_PLUGIN", "builtin")

import rule_generator


class RuleGeneratorV110Tests(unittest.TestCase):
    def test_register_code_generates_reusable_code_rule(self):
        sample = "Wlao-30-Register_Ab12Cd34Ef"

        result = rule_generator.generate_rule("马子", sample)

        self.assertEqual(result["rule_type"], "code")
        self.assertEqual(result["policy"]["dedup_strategy"], "code_identity")
        self.assertIsNotNone(re.search(result["pattern"], sample))
        self.assertIsNotNone(
            re.search(result["pattern"], "Wlao-30-Register_Z9Y8X7W6V5")
        )
        self.assertIsNone(re.search(result["pattern"], "Wlao-30-Whitelist_Ab12Cd34Ef"))

    def test_lottery_message_prefers_stable_lottery_id(self):
        sample = (
            "新的抽奖已经创建\n"
            "抽奖 ID：1ea59728-11f6-4748-9a32-b341129f2f3d\n"
            "奖品内容：\n"
            "  公益服注册码 x1"
        )

        result = rule_generator.generate_rule("抽奖", sample)

        self.assertEqual(result["rule_type"], "lottery")
        self.assertEqual(result["policy"]["dedup_strategy"], "lottery_identity")
        self.assertEqual(result["policy"]["ttl_minutes"], 720)
        self.assertIsNotNone(re.search(result["pattern"], sample))
        self.assertIsNotNone(
            re.search(
                result["pattern"],
                "抽奖 ID：aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            )
        )

    def test_bare_ck_code_generates_reusable_prefix_rule(self):
        original = rule_generator._matching_existing_regex_pattern
        rule_generator._matching_existing_regex_pattern = lambda _sample: r"\bCK[A-Z0-9]{12}\b"
        try:
            result = rule_generator.generate_rule("码子", "CKWIS3PD97M3F6")
        finally:
            rule_generator._matching_existing_regex_pattern = original

        self.assertEqual(result["rule_type"], "code")
        self.assertIsNotNone(re.search(result["pattern"], "CKWIS3PD97M3F6"))
        self.assertIsNotNone(re.search(result["pattern"], "CK7F2Q9LMN4P1X"))
        self.assertIsNone(re.search(result["pattern"], "XXWIS3PD97M3F6"))

    def test_keyword_message_generates_escaped_keyword_rule(self):
        sample = "🍀 祝所有参与者好运！"

        result = rule_generator.generate_rule("关键词", sample)

        self.assertEqual(result["rule_type"], "keyword")
        self.assertEqual(result["policy"]["dedup_strategy"], "normalized_text")
        self.assertIsNotNone(re.search(result["pattern"], sample))
        self.assertIsNone(re.search(result["pattern"], "祝所有参与者顺利！"))

    def test_exclude_message_generates_non_forwarding_rule(self):
        sample = "暂时停止注册"

        result = rule_generator.generate_rule("排除", sample)

        self.assertEqual(result["rule_type"], "exclude")
        self.assertEqual(result["policy"]["dedup_strategy"], "none")
        self.assertFalse(result["policy"]["forward"])
        self.assertIsNotNone(re.search(result["pattern"], sample))

    def test_empty_sample_is_rejected(self):
        with self.assertRaises(ValueError):
            rule_generator.generate_rule("码子", "   ")


if __name__ == "__main__":
    unittest.main()
