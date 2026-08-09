import hashlib
import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
SEED = "0bd92a15d0236d7944cf20184b010be5832f162d2b63fb4876365a4bd264733d"

PRIMARY_MESSAGE = f"""🎟 抽奖 新抽奖活动

创建者: MinaseKanata
参与次数上限: 1
满 300 人开奖
参与关键词: 抽奖啦
随机种子哈希: {SEED}
已参与: 4人 (4票)
中奖概率(每张票): 25.0%
🎁 奖品内容：
1. +66Tg号 x1
"""

MIRROR_MESSAGE = f"""🎟️ 抽奖 新抽奖活动

创建者: MinaseKanata
开奖方式: 满 300 人开奖
随机种子哈希: {SEED}
参与次数上限: 1
参与条件: Minase Kanata
🎁 奖品内容:
1. +66Tg号 x1
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
        self.values = {
            "dedup_lottery_minutes": "720",
            "dedup_lottery_key_mode": "id_prize_keyword",
        }
        self.lists = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, ex=None, nx=False):
        if nx and key in self.values:
            return None
        self.values[key] = str(value)
        return True

    def setex(self, key, seconds, value):
        self.values[key] = str(value)
        return True

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
    fake_store.format_time = lambda: "2026-07-14 01:05:25"
    old = sys.modules.get("redis_store")
    sys.modules["redis_store"] = fake_store
    try:
        spec = importlib.util.spec_from_file_location("dedup_v13880", APP / "dedup.py")
        module = importlib.util.module_from_spec(spec)
        assert spec is not None and spec.loader is not None
        spec.loader.exec_module(module)
        return module, client
    finally:
        if old is None:
            sys.modules.pop("redis_store", None)
        else:
            sys.modules["redis_store"] = old


class StableLotteryIdentityV13880Tests(unittest.TestCase):
    def test_same_seed_uses_one_lottery_identity_across_template_variants(self):
        dedup, _client = load_dedup()

        primary = dedup.build_profile(PRIMARY_MESSAGE, "https://t.me/MinaseKanata_KM/27345")
        mirror = dedup.build_profile(MIRROR_MESSAGE, "https://t.me/MinaseKanata_KM/27346")

        self.assertNotEqual(primary["text_hash"], mirror["text_hash"])
        self.assertEqual(primary["lottery_identity"], "seed:" + SEED)
        self.assertEqual(primary["lottery_identity"], mirror["lottery_identity"])
        self.assertEqual(primary["dedup_id"], mirror["dedup_id"])
        self.assertTrue(primary["dedup_id"].startswith("lottery:"))

    def test_second_template_with_same_seed_is_blocked(self):
        dedup, _client = load_dedup()

        first_duplicate, _reason, _profile = dedup.check_and_mark(
            PRIMARY_MESSAGE,
            "https://t.me/MinaseKanata_KM/27345",
            None,
            "strict",
            "Minase Kanata",
        )
        second_duplicate, reason, profile = dedup.check_and_mark(
            MIRROR_MESSAGE,
            "https://t.me/MinaseKanata_KM/27346",
            None,
            "strict",
            "Minase Kanata",
        )

        self.assertFalse(first_duplicate)
        self.assertTrue(second_duplicate)
        self.assertIn("相同抽奖 ID", reason)
        self.assertEqual(profile["lottery_identity"], "seed:" + SEED)

    def test_different_seed_is_not_blocked(self):
        dedup, _client = load_dedup()
        other = MIRROR_MESSAGE.replace(SEED, "1" * 64)

        first_duplicate, _reason, first = dedup.check_and_mark(
            PRIMARY_MESSAGE, "https://t.me/a/1", None, "strict", "a"
        )
        second_duplicate, _reason, second = dedup.check_and_mark(
            other, "https://t.me/a/2", None, "strict", "a"
        )

        self.assertFalse(first_duplicate)
        self.assertFalse(second_duplicate)
        self.assertNotEqual(first["dedup_id"], second["dedup_id"])

    def test_explicit_lottery_id_is_also_stable(self):
        dedup, _client = load_dedup()
        lottery_id = "8f9a2776-4698-4f9d-a935-253660bcc531"
        first = dedup.build_profile(f"抽奖信息\n抽奖 ID：{lottery_id}\n奖品：A", "")
        second = dedup.build_profile(f"新的抽奖已经创建\n抽奖ID: {lottery_id}\n奖品：B", "")

        self.assertEqual(first["lottery_identity"], "id:" + lottery_id)
        self.assertEqual(first["dedup_id"], second["dedup_id"])


if __name__ == "__main__":
    unittest.main()
