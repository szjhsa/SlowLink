import unittest
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from test_v13883_cross_template_lottery_dedup import load_dedup


FIRST_POST = """瞬影公费新服上线

🎁 抽奖活动已开始！
━━━━━━━━━━━━━━

⏰ 截止时间：2026年8月10日 time utc 8

🎁 奖品：
  ▸ 瞬影pro月卡 自动发卡 卡密再发奖后72小时自动过期 x15
  ▸ 瞬影pro周卡 自动发卡 卡密再发奖后72小时自动过期 x40
  ▸ afusekt播放器兑换码 x1

📣 发布群组：
  ▸ 瞬影emby交流群
  ▸ afusekt

参与要求
"""

SECOND_POST = """瞬影公费新服上线

🎁 抽奖活动已开始！
━━━━━━━━━━━━━━

⏰ 截止时间：2026年8月10日 time utc 8

🎁 奖品：
  ▸ 瞬影pro月卡 自动发卡 卡密再发奖后72小时自动过期 x15
  ▸ 瞬影pro周卡 自动发卡 卡密再发奖后72小时自动过期 x40
  ▸ afusekt播放器兑换码 x1

📣 发布群组：
  ▸ afusekt
  ▸ 瞬影emby交流群

参与要求
"""

FULL_FIRST = FIRST_POST + "\n🔑 口令：LOTTERYKEY\n📑 活动详情：详情A"
FULL_SECOND = SECOND_POST + "\n🔑 口令：NEWKEY\n📑 活动详情：详情B"


class GlobalStartedLotteryDedupV1398Tests(unittest.TestCase):
    def test_group_order_change_keeps_one_global_lottery_identity(self):
        dedup, _client = load_dedup()

        first = dedup.build_profile(FIRST_POST, "https://t.me/syemby/100396", "瞬影EMBY交流群")
        second = dedup.build_profile(SECOND_POST, "https://t.me/ShardCatDen/661290", "碎片谷雨小窝")

        self.assertNotEqual(first["dedup_id"], second["dedup_id"])
        self.assertTrue(first["lottery_template_identity"])
        self.assertEqual(
            first["lottery_template_identity"],
            second["lottery_template_identity"],
        )

    def test_second_crosspost_is_blocked(self):
        dedup, _client = load_dedup()

        first_duplicate, _reason, _profile = dedup.check_and_mark(
            FIRST_POST, "https://t.me/syemby/100396", None, "strict", "瞬影EMBY交流群"
        )
        second_duplicate, reason, _profile = dedup.check_and_mark(
            SECOND_POST, "https://t.me/ShardCatDen/661290", None, "strict", "碎片谷雨小窝"
        )

        self.assertFalse(first_duplicate)
        self.assertTrue(second_duplicate)
        self.assertIn("同一抽奖的不同模板", reason)

    def test_changed_deadline_or_prize_remains_distinct(self):
        dedup, _client = load_dedup()
        original = dedup.build_profile(FIRST_POST)["lottery_template_identity"]

        changed_deadline = dedup.build_profile(
            FIRST_POST.replace("2026年8月10日", "2026年8月11日")
        )["lottery_template_identity"]
        changed_prize = dedup.build_profile(
            FIRST_POST.replace("afusekt播放器兑换码 x1", "afusekt播放器兑换码 x2")
        )["lottery_template_identity"]

        self.assertTrue(original)
        self.assertNotEqual(original, changed_deadline)
        self.assertNotEqual(original, changed_prize)

    def test_global_identity_still_blocks_when_optional_fields_differ(self):
        dedup, _client = load_dedup()

        first_identity = dedup.build_profile(FULL_FIRST)["lottery_template_identity"]
        second_identity = dedup.build_profile(FULL_SECOND)["lottery_template_identity"]
        first_global = dedup.build_profile(FULL_FIRST)["lottery_global_identity"]
        second_global = dedup.build_profile(FULL_SECOND)["lottery_global_identity"]

        self.assertNotEqual(first_identity, second_identity)
        self.assertTrue(first_global)
        self.assertEqual(first_global, second_global)

        first_duplicate, _reason, _profile = dedup.check_and_mark(
            FULL_FIRST, "https://t.me/syemby/100400", None, "strict", "瞬影EMBY交流群"
        )
        second_duplicate, reason, _profile = dedup.check_and_mark(
            FULL_SECOND, "https://t.me/ShardCatDen/661300", None, "strict", "碎片谷雨小窝"
        )

        self.assertFalse(first_duplicate)
        self.assertTrue(second_duplicate)
        self.assertIn("同一抽奖的不同模板", reason)


if __name__ == "__main__":
    unittest.main()
