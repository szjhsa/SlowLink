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
import rule_types


class RuleGeneratorV110Tests(unittest.TestCase):
    def setUp(self):
        rule_types.clear_cache()

    def test_builtin_plugin_exposes_code_lottery_and_exclude_types(self):
        old = os.environ.get("SLOWLINK_ACTIVE_PLUGIN")
        os.environ["SLOWLINK_ACTIVE_PLUGIN"] = "builtin"
        try:
            rule_types.clear_cache()
            ids = [item["id"] for item in rule_generator.available_rule_types()]
        finally:
            if old is None:
                os.environ.pop("SLOWLINK_ACTIVE_PLUGIN", None)
            else:
                os.environ["SLOWLINK_ACTIVE_PLUGIN"] = old

        self.assertEqual(ids[0], "keyword")
        self.assertIn("code", ids)
        self.assertIn("lottery", ids)
        self.assertIn("exclude", ids)

    def test_pure_mode_exposes_only_keyword_generator(self):
        old = os.environ.get("SLOWLINK_ACTIVE_PLUGIN")
        os.environ["SLOWLINK_ACTIVE_PLUGIN"] = "off"
        try:
            rule_types.clear_cache()
            ids = [item["id"] for item in rule_generator.available_rule_types()]
            with self.assertRaises(ValueError):
                rule_generator.generate_rule("码子", "CKWIS3PD97M3F6")
        finally:
            if old is None:
                os.environ.pop("SLOWLINK_ACTIVE_PLUGIN", None)
            else:
                os.environ["SLOWLINK_ACTIVE_PLUGIN"] = old

        self.assertEqual(ids, ["keyword"])

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
        result = rule_generator.generate_rule("码子", "CKWIS3PD97M3F6")

        self.assertEqual(result["rule_type"], "code")
        self.assertIsNotNone(re.search(result["pattern"], "CKWIS3PD97M3F6"))
        self.assertIsNotNone(re.search(result["pattern"], "CK7F2Q9LMN4P1X"))
        self.assertIsNotNone(re.search(result["pattern"], "CK中文*AB猜码12345"))
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
