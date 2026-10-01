import hashlib
import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"

DETAILED_VOUCHER_LOTTERY = """高德打车10元代金券
🎁 抽奖活动已开始！
━━━━━━━━━━━━━━

🎰 开奖模式：刮刮乐

🎁 奖品：
  ▸ 10元代金券 x1

📣 发布群组：
  ▸ 小姨子

🍀 祝所有参与者好运！
"""

COMPACT_VOUCHER_LOTTERY = """🎁 抽奖开始啦

详情
方式：刮刮乐
奖品：10元代金券
现在可以参与这场抽奖啦，祝你好运！
"""

NAMED_VOUCHER_LOTTERY = """高德打车10元代金券
🎁 抽奖活动已开始！
━━━━━━━━━━━━━━

🎰 开奖模式：刮刮乐

🎁 奖品：
  ▸ 高德打车10元代金券 x1

📣 发布群组：
  ▸ 小姨子

🍀 祝所有参与者好运！
"""

REGISTER_LOTTERY_DETAILED = """🎁 抽奖活动已开始！
🎰 开奖模式：刮刮乐
🎁 奖品：
  ▸ 公益服注册码 x1
📣 发布群组：
  ▸ 小姨子
"""

REGISTER_LOTTERY_COMPACT = """🎁 抽奖开始啦
方式：刮刮乐
奖品：公益服注册码
现在可以参与这场抽奖啦，祝你好运！
"""


class FakePipeline:
    def __init__(self, client):
        self.client = client
        self.operations = []

    def set(self, key, value, ex=None, nx=False):
        self.operations.append(("set", (key, value), {"ex": ex, "nx": nx}))
        return self

    def lpush(self, key, value):
        self.operations.append(("lpush", (key, value), {}))
        return self

    def ltrim(self, key, start, end):
        self.operations.append(("ltrim", (key, start, end), {}))
        return self

    def execute(self):
        return [getattr(self.client, name)(*args, **kwargs) for name, args, kwargs in self.operations]


class FakeRedis:
    def __init__(self):
        self.values = {"dedup_lottery_minutes": "720"}
        self.expires = {}
        self.lists = {}
        self.now = 1_000_000

    def _purge(self, key):
        expires_at = self.expires.get(key)
        if expires_at is not None and expires_at <= self.now:
            self.values.pop(key, None)
            self.expires.pop(key, None)

    def get(self, key):
        self._purge(key)
        return self.values.get(key)

    def set(self, key, value, ex=None, nx=False):
        self._purge(key)
        if nx and key in self.values:
            return None
        self.values[key] = str(value)
        if ex is not None:
            self.expires[key] = self.now + int(ex)
        return True

    def setex(self, key, seconds, value):
        return self.set(key, value, ex=seconds)

    def lpush(self, key, value):
        self.lists.setdefault(key, []).insert(0, value)
        return len(self.lists[key])

    def ltrim(self, key, start, end):
        items = self.lists.setdefault(key, [])
        self.lists[key] = items[start:end + 1]
        return True

    def pipeline(self):
        return FakePipeline(self)


def load_dedup():
    client = FakeRedis()
    fake_store = types.ModuleType("redis_store")
    fake_store.r = client
    fake_store.sha = lambda value: hashlib.sha256((value or "").encode("utf-8")).hexdigest()
    fake_store.format_time = lambda: "2026-08-08 16:26:36"
    old = sys.modules.get("redis_store")
    sys.modules["redis_store"] = fake_store
    sys.path.insert(0, str(APP))
    try:
        spec = importlib.util.spec_from_file_location("dedup_v1395", APP / "dedup.py")
        module = importlib.util.module_from_spec(spec)
        assert spec is not None and spec.loader is not None
        spec.loader.exec_module(module)
        return module, client
    finally:
        sys.path.remove(str(APP))
        if old is None:
            sys.modules.pop("redis_store", None)
        else:
            sys.modules["redis_store"] = old


class GlobalScratchDedupV1396Tests(unittest.TestCase):
    def test_three_real_templates_share_one_lottery_identity(self):
        dedup, _client = load_dedup()

        profiles = (
            dedup.build_profile(DETAILED_VOUCHER_LOTTERY, "https://t.me/xyz_emby/568383", "小姨子"),
            dedup.build_profile(COMPACT_VOUCHER_LOTTERY, "https://t.me/xyz_push/786", "小姨子推送"),
            dedup.build_profile(NAMED_VOUCHER_LOTTERY, "https://t.me/xyz_emby/568398", "小姨子"),
        )

        identities = {profile["lottery_template_identity"] for profile in profiles}
        self.assertEqual(len(identities), 1)
        self.assertTrue(next(iter(identities)).startswith("scratch-event:"))

    def test_registration_code_prize_uses_the_same_global_identity(self):
        dedup, _client = load_dedup()

        detailed = dedup.build_profile(
            REGISTER_LOTTERY_DETAILED,
            "https://t.me/xyz_emby/568410",
            "小姨子",
        )
        compact = dedup.build_profile(
            REGISTER_LOTTERY_COMPACT,
            "https://t.me/xyz_push/790",
            "小姨子推送",
        )

        self.assertEqual(
            detailed["lottery_template_identity"],
            compact["lottery_template_identity"],
        )
        self.assertTrue(detailed["lottery_template_identity"].startswith("scratch-event:"))

    def test_multiple_non_money_prizes_are_normalized_as_one_global_identity(self):
        dedup, _client = load_dedup()
        first = REGISTER_LOTTERY_DETAILED.replace(
            "  ▸ 公益服注册码 x1",
            "  ▸ 公益服注册码 x1\n  ▸ 公益服白名单码 x1",
        )
        second = REGISTER_LOTTERY_COMPACT.replace(
            "奖品：公益服注册码",
            "奖品：\n公益服白名单码 x1\n公益服注册码 x1\n🍀 祝所有参与者好运！",
        )

        first_identity = dedup.build_profile(first)["lottery_template_identity"]
        second_identity = dedup.build_profile(second)["lottery_template_identity"]

        self.assertEqual(first_identity, second_identity)

    def test_scratch_mode_without_the_word_lottery_is_still_classified(self):
        dedup, _client = load_dedup()
        scratch_only = REGISTER_LOTTERY_DETAILED.replace("🎁 抽奖活动已开始！\n", "")

        profile = dedup.build_profile(scratch_only)

        self.assertEqual(profile["activity"], "lottery")
        self.assertTrue(profile["lottery_template_identity"].startswith("scratch-event:"))

    def test_later_cross_template_posts_are_blocked_within_ten_minutes(self):
        dedup, _client = load_dedup()

        first_duplicate, _reason, _profile = dedup.check_and_mark(
            DETAILED_VOUCHER_LOTTERY, "https://t.me/xyz_emby/568383", None, "strict", "小姨子"
        )
        second_duplicate, second_reason, _profile = dedup.check_and_mark(
            COMPACT_VOUCHER_LOTTERY, "https://t.me/xyz_push/786", None, "strict", "小姨子推送"
        )
        third_duplicate, third_reason, _profile = dedup.check_and_mark(
            NAMED_VOUCHER_LOTTERY, "https://t.me/xyz_emby/568398", None, "strict", "小姨子"
        )

        self.assertFalse(first_duplicate)
        self.assertTrue(second_duplicate)
        self.assertTrue(third_duplicate)
        self.assertIn("同一抽奖的不同模板", second_reason)
        self.assertIn("同一抽奖的不同模板", third_reason)

    def test_different_voucher_value_is_not_correlated(self):
        dedup, _client = load_dedup()
        different_prize = COMPACT_VOUCHER_LOTTERY.replace("10元代金券", "20元代金券")

        original = dedup.build_profile(DETAILED_VOUCHER_LOTTERY)["lottery_template_identity"]
        changed = dedup.build_profile(different_prize)["lottery_template_identity"]

        self.assertTrue(original)
        self.assertTrue(changed)
        self.assertNotEqual(original, changed)

    def test_non_lottery_voucher_chat_has_no_template_identity(self):
        sys.path.insert(0, str(APP))
        try:
            from plugin_runtime import call_hook

            identity = call_hook(
                "extract_lottery_template_identity",
                {"text": "今天刮刮乐送10元代金券，大家聊聊"},
                default="",
            )
        finally:
            sys.path.remove(str(APP))

        self.assertEqual(identity, "")

    def test_lottery_template_mode_off_disables_global_correlation(self):
        dedup, client = load_dedup()
        client.values["dedup_lottery_template_mode"] = "off"

        first_duplicate, _reason, _profile = dedup.check_and_mark(
            DETAILED_VOUCHER_LOTTERY, "https://t.me/xyz_emby/568383", None, "strict", "小姨子"
        )
        second_duplicate, _reason, _profile = dedup.check_and_mark(
            COMPACT_VOUCHER_LOTTERY, "https://t.me/xyz_push/786", None, "strict", "小姨子推送"
        )

        self.assertFalse(first_duplicate)
        self.assertFalse(second_duplicate)


if __name__ == "__main__":
    unittest.main()
