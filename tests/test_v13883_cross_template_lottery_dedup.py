import hashlib
import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
SEED = "5cbc340b7cf0874431c8883f2d67aa86fbaaf48d9162ba339b286639ff4b0b7c"

NO_SEED_MESSAGE = """🎰 茶话带集联来抽奖咯

创建者: 𝓟𝓮𝓽𝓻𝓲𝓬𝓱𝓸𝓻
参与次数上限: 1
跨群总参与次数上限: 1
已参与: 60人 (60票)
中奖概率(每张票): 95.0%

🔑 参与关键词: 吃瓜集邮两不误

📮 抽奖条件:
参与要求: 同时在 集邮者联盟 (https://t.me/Petrichor_Embys_chat)、茶话领域 (https://t.me/Jsoo8888)

🎁 奖品内容:
1. 月亮公益硬盘白名单 x5
2. 月亮公益硬盘注册码 x10
3. Eoos公费服月卡 x10
4. Eoos公益服注册 x10
5. 步兵营瑟瑟服注册 x14
6. WW播放器（win端） x8

😊 开奖条件:
定时开奖: 2026-07-15 21:30
"""

SEEDED_MESSAGE = f"""🎫 抽奖 茶话带集联来抽奖咯

创建者: 𝓟𝓮𝓽𝓻𝓲𝓬𝓱𝓸𝓻
参与次数上限: 1
跨群总参与次数上限: 1
定时开奖: 2026-07-15 21:30

参与关键词: 吃瓜集邮两不误

随机种子哈希: {SEED}

已参与: 62人 (62票)
中奖概率(每张票): 91.9%

🎁 奖品内容：
1. 月亮公益硬盘白名单 x5
2. 月亮公益硬盘注册码 x10
3. Eoos公费服月卡 x10
4. Eoos公益服注册 x10
5. 步兵营瑟瑟服注册 x14
6. WW播放器（win端） x8

参与要求: 同时在 集邮者联盟 (https://t.me/Petrichor_Embys_chat)、茶话领域 (https://t.me/Jsoo8888)
"""

LEWA_MESSAGE_WITHOUT_REQUIREMENT = """🎉 Levilde Luminia Emby注册码30天*1

🎁 抽奖活动已开始！
━━━━━━━━━━━━━━

⏰ 截止时间：2026年7月17日 19:59:59

🎁 奖品：
  ▸ Levilde Luminia Emby (Levilde Luminia Emby注册码30天*1) x5

📣 发布群组：
  ▸ 乐蛙影视站-群组

🔑 口令：Levilde LuminiaYYDS

📝 活动详情：
Levilde Luminia Emby注册码30天*1

━━━━━━━━━━━━━━
🍀 祝所有参与者好运！
"""

LEWA_MESSAGE_WITH_REQUIREMENT = LEWA_MESSAGE_WITHOUT_REQUIREMENT.replace(
    "🔑 口令：",
    "📋 参与要求：\n  ▸ 订阅 乐蛙影视站-群组\n\n🔑 口令：",
)


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
        self.now = 1_000_000
        self.values = {"dedup_lottery_minutes": "720"}
        self.expires = {}
        self.lists = {}

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

    def delete(self, *keys):
        removed = 0
        for key in keys:
            removed += int(key in self.values or key in self.lists)
            self.values.pop(key, None)
            self.lists.pop(key, None)
            self.expires.pop(key, None)
        return removed

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
    fake_store.format_time = lambda: "2026-07-14 19:30:12"
    old = sys.modules.get("redis_store")
    sys.modules["redis_store"] = fake_store
    try:
        spec = importlib.util.spec_from_file_location("dedup_v13883", APP / "dedup.py")
        module = importlib.util.module_from_spec(spec)
        assert spec is not None and spec.loader is not None
        spec.loader.exec_module(module)
        return module, client
    finally:
        if old is None:
            sys.modules.pop("redis_store", None)
        else:
            sys.modules["redis_store"] = old


class CrossTemplateLotteryDedupV13883Tests(unittest.TestCase):
    def test_seeded_and_no_seed_templates_share_event_identity(self):
        dedup, _client = load_dedup()

        no_seed = dedup.build_profile(NO_SEED_MESSAGE, "https://t.me/Petrichor_Embys_chat/243716")
        seeded = dedup.build_profile(SEEDED_MESSAGE, "https://t.me/Jsoo8888/115924")

        self.assertNotEqual(no_seed["dedup_id"], seeded["dedup_id"])
        self.assertTrue(no_seed["dedup_id"].startswith("text:"))
        self.assertTrue(seeded["dedup_id"].startswith("lottery:"))
        self.assertTrue(no_seed["lottery_template_identity"].startswith("event:"))
        self.assertEqual(no_seed["lottery_template_identity"], seeded["lottery_template_identity"])

    def test_second_cross_group_template_is_blocked(self):
        dedup, _client = load_dedup()

        first_duplicate, _reason, _profile = dedup.check_and_mark(
            NO_SEED_MESSAGE,
            "https://t.me/Petrichor_Embys_chat/243716",
            None,
            "strict",
            "集邮者联盟",
        )
        second_duplicate, reason, _profile = dedup.check_and_mark(
            SEEDED_MESSAGE,
            "https://t.me/Jsoo8888/115924",
            None,
            "strict",
            "茶话领域",
        )

        self.assertFalse(first_duplicate)
        self.assertTrue(second_duplicate)
        self.assertIn("同一抽奖的不同模板", reason)

    def test_changed_draw_time_or_prize_is_not_blocked(self):
        dedup, _client = load_dedup()
        changed_time = SEEDED_MESSAGE.replace("2026-07-15 21:30", "2026-07-16 21:30")
        changed_prize = SEEDED_MESSAGE.replace("月亮公益硬盘白名单 x5", "月亮公益硬盘白名单 x6")

        original = dedup.build_profile(NO_SEED_MESSAGE)
        later = dedup.build_profile(changed_time)
        different_prize = dedup.build_profile(changed_prize)

        self.assertNotEqual(original["lottery_template_identity"], later["lottery_template_identity"])
        self.assertNotEqual(original["lottery_template_identity"], different_prize["lottery_template_identity"])

    def test_decimal_prize_value_is_not_collapsed_into_an_integer(self):
        dedup, _client = load_dedup()
        decimal_prize = NO_SEED_MESSAGE.replace("月亮公益硬盘白名单 x5", "容量 1.5TB x5")
        integer_prize = NO_SEED_MESSAGE.replace("月亮公益硬盘白名单 x5", "容量 15TB x5")

        decimal_profile = dedup.build_profile(decimal_prize)
        integer_profile = dedup.build_profile(integer_prize)

        self.assertNotEqual(
            decimal_profile["lottery_template_identity"],
            integer_profile["lottery_template_identity"],
        )

    def test_different_explicit_seeds_are_not_blocked_by_event_identity(self):
        dedup, _client = load_dedup()
        another_seed = SEEDED_MESSAGE.replace(SEED, "1" * 64)

        first_duplicate, _reason, _profile = dedup.check_and_mark(
            SEEDED_MESSAGE, "https://t.me/a/1", None, "strict", "a"
        )
        second_duplicate, _reason, _profile = dedup.check_and_mark(
            another_seed, "https://t.me/b/2", None, "strict", "b"
        )

        self.assertFalse(first_duplicate)
        self.assertFalse(second_duplicate)

    def test_optional_participation_requirement_does_not_change_lottery_template_identity(self):
        dedup, _client = load_dedup()

        original = dedup.build_profile(
            LEWA_MESSAGE_WITHOUT_REQUIREMENT,
            "https://t.me/Lewa_movie/390202",
            "乐蛙影视站-群组",
        )
        edited = dedup.build_profile(
            LEWA_MESSAGE_WITH_REQUIREMENT,
            "https://t.me/Lewa_movie/390205",
            "乐蛙影视站-群组",
        )

        self.assertNotEqual(original["dedup_id"], edited["dedup_id"])
        self.assertTrue(original["lottery_template_identity"].startswith("deadline:"))
        self.assertEqual(
            original["lottery_template_identity"],
            edited["lottery_template_identity"],
        )

    def test_edited_requirement_variant_is_blocked_within_template_window(self):
        dedup, _client = load_dedup()

        first_duplicate, _reason, _profile = dedup.check_and_mark(
            LEWA_MESSAGE_WITHOUT_REQUIREMENT,
            "https://t.me/Lewa_movie/390202",
            None,
            "strict",
            "乐蛙影视站-群组",
        )
        second_duplicate, reason, _profile = dedup.check_and_mark(
            LEWA_MESSAGE_WITH_REQUIREMENT,
            "https://t.me/Lewa_movie/390205",
            None,
            "strict",
            "乐蛙影视站-群组",
        )

        self.assertFalse(first_duplicate)
        self.assertTrue(second_duplicate)
        self.assertEqual(reason, "同一抽奖的不同模板重复（10分钟内）")

    def test_deadline_prize_or_passphrase_change_remains_a_distinct_lottery(self):
        dedup, _client = load_dedup()
        original = dedup.build_profile(LEWA_MESSAGE_WITHOUT_REQUIREMENT)

        variants = (
            LEWA_MESSAGE_WITHOUT_REQUIREMENT.replace("Levilde Luminia Emby注册码30天*1", "新的抽奖活动", 1),
            LEWA_MESSAGE_WITHOUT_REQUIREMENT.replace("2026年7月17日", "2026年7月18日"),
            LEWA_MESSAGE_WITHOUT_REQUIREMENT.replace(") x5", ") x6"),
            LEWA_MESSAGE_WITHOUT_REQUIREMENT.replace("乐蛙影视站-群组", "乐蛙影视站-二群", 1),
            LEWA_MESSAGE_WITHOUT_REQUIREMENT.replace("LuminiaYYDS", "LuminiaNEW"),
            LEWA_MESSAGE_WITHOUT_REQUIREMENT.replace(
                "活动详情：\nLevilde Luminia Emby注册码30天*1",
                "活动详情：\n新的活动详情",
            ),
        )

        self.assertTrue(original["lottery_template_identity"])
        for variant in variants:
            with self.subTest(variant=variant):
                changed = dedup.build_profile(variant)
                self.assertNotEqual(
                    original["lottery_template_identity"],
                    changed["lottery_template_identity"],
                )

    def test_lewa_template_correlation_expires_after_ten_minutes(self):
        dedup, client = load_dedup()

        first_duplicate, _reason, _profile = dedup.check_and_mark(
            LEWA_MESSAGE_WITHOUT_REQUIREMENT,
            "https://t.me/Lewa_movie/390202",
            None,
            "strict",
            "乐蛙影视站-群组",
        )
        client.now += 601
        second_duplicate, _reason, _profile = dedup.check_and_mark(
            LEWA_MESSAGE_WITH_REQUIREMENT,
            "https://t.me/Lewa_movie/390205",
            None,
            "strict",
            "乐蛙影视站-群组",
        )

        self.assertFalse(first_duplicate)
        self.assertFalse(second_duplicate)


if __name__ == "__main__":
    unittest.main()
