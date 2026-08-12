import unittest
from pathlib import Path

from tests.test_v13917_hyphen_register_codes import load_real_matcher


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
EXPECTED_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8-sig").strip()


class RegisterExhaustedAndOpenStatusV13920Tests(unittest.TestCase):
    def test_versions_are_bumped_to_current_release(self):
        self.assertEqual((ROOT / "VERSION").read_text(encoding="utf-8-sig").strip(), EXPECTED_VERSION)
        config = (APP / "config.py").read_text(encoding="utf-8-sig")
        self.assertIn(f'APP_VERSION = "{EXPECTED_VERSION}"', config)

    def test_open_and_closed_status_use_same_detector(self):
        matcher = load_real_matcher()

        self.assertEqual(matcher._explicit_registration_status("当前开注状态：True"), "open")
        self.assertEqual(matcher._explicit_registration_status("当前开注状态：False"), "closed")
        self.assertEqual(matcher._explicit_registration_status("注册状态 | off"), "closed")

    def test_remaining_zero_is_treated_as_closed(self):
        matcher = load_real_matcher()
        text = (
            "🫧 管理员 精彩迪迦 已开启 **自由注册**\n"
            "🎫 总注册限制 | 700\n"
            "🎟️ 已注册人数 | 700\n"
            "🎭 剩余可注册 | **0**\n"
            "🤖 bot使用人数 | 1385"
        )

        result = matcher.match_rule_details(text)

        self.assertFalse(result["matched"])
        self.assertTrue(result["closed_register_notice"])

    def test_open_status_with_remaining_capacity_is_not_closed(self):
        matcher = load_real_matcher()
        text = (
            "🍉尊敬的 **水蜜桃** 您好!\n"
            "🃏当前开注状态：True\n"
            "🪢商店开放状态：True\n"
            "🎫剩余可注册人数：36"
        )

        result = matcher.match_rule_details(text)

        self.assertFalse(result["closed_register_notice"])


if __name__ == "__main__":
    unittest.main()
