import unittest

from tests.test_v13893_exclude_texts import load_matcher


LOTTERY_TEXT = """🎉 暑期抽奖
🎁 抽奖活动已开始！
━━━━━━━━━━━━━━

⏰ 截止时间：2026年7月30日 15:37:13

🎁 奖品
  ▸ 公益服注册码 (不可用于交易) x1
  ▸ 公益服白名单码 (不可用于交易
无号不提供注册码) x1

📣 发布群组
  ▸ CineTrail（影迹）💁🏾 — 待恢复模式
━━━━━━━━━━━━━━

🍀 祝所有参与者好运！
"""


class ExactUserRegexV138100Tests(unittest.TestCase):
    def test_plain_looking_rule_is_compiled_as_regex_and_matches_original_text(self):
        rule = "🍀 祝所有参与者好运！"
        matcher = load_matcher(regex_rules={rule})

        compiled = matcher._compiled_rules(ttl=0)
        result = matcher.match_rule_details(LOTTERY_TEXT)

        self.assertEqual([raw for raw, _compiled in compiled["regexes"]], [rule])
        self.assertTrue(result["matched"])
        self.assertEqual(result["rule"], rule)
        self.assertEqual(result["candidate"], "原始文本")

    def test_halfwidth_punctuation_does_not_match_fullwidth_original_text(self):
        matcher = load_matcher(regex_rules={"🍀 祝所有参与者好运!"})

        self.assertFalse(matcher.match_rule_details(LOTTERY_TEXT)["matched"])

    def test_regex_metacharacters_are_never_downgraded_to_keyword_text(self):
        rule = r"公益服.名单码"
        matcher = load_matcher(regex_rules={rule})

        result = matcher.match_rule_details(LOTTERY_TEXT)

        self.assertTrue(result["matched"])
        self.assertEqual(result["rule"], rule)

    def test_real_newline_inside_one_rule_is_preserved(self):
        rule = "第一行\n第二行"
        matcher = load_matcher(regex_rules={rule})

        self.assertEqual(matcher.expanded_rules(), [rule])
        self.assertEqual(matcher.match_rule_details(rule)["rule"], rule)

    def test_all_matcher_entry_points_use_the_same_original_regex_target(self):
        rule = "🍀 祝所有参与者好运！"
        matcher = load_matcher(regex_rules={rule})

        analysis = matcher.analyze_message(LOTTERY_TEXT)
        matched, matched_rule = matcher.match_rules(LOTTERY_TEXT)
        details = matcher.match_rule_details(LOTTERY_TEXT)

        self.assertTrue(analysis["matched"])
        self.assertEqual(analysis["rule"], rule)
        self.assertTrue(matched)
        self.assertEqual(matched_rule, rule)
        self.assertTrue(details["matched"])
        self.assertEqual(details["rule"], rule)

    def test_message_extraction_preserves_whitespace_for_anchored_regex(self):
        matcher = load_matcher()
        message = type("Message", (), {"message": "\n开始\n"})()

        self.assertEqual(matcher.get_text(message), "\n开始\n")

    def test_rule_diagnostics_reports_plain_pattern_as_regex(self):
        matcher = load_matcher(regex_rules={"🍀 祝所有参与者好运！"})

        self.assertEqual(matcher.rule_diagnostics()[0]["type"], "regex")


if __name__ == "__main__":
    unittest.main()
