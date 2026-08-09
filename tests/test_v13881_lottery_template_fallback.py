import hashlib
import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"

DETAILED_SCRATCH_LOTTERY = """🎉 刮刮乐

🎁 抽奖活动已开始！
━━━━━━━━━━━━━━

🎰 开奖模式：刮刮乐

🎁 奖品：
  ▸ +20币 x1
  ▸ +10币 x1
  ▸ +30币 x1
  ▸ +5币 x1
  ▸ +1币 x1
  ▸ -10币 x1
  ▸ -5币 x1
  ▸ -8币 x1

📣 发布群组：
  ▸ 野草地

🍀 祝所有参与者好运！
"""

COMPACT_SCRATCH_LOTTERY = """🎁 抽奖开始啦

详情
方式：刮刮乐
奖品：
+20币
+10币
+30币
+5币
+1币
-10币
-5币
-8币

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
        self.now = 1_000_000
        self.values = {
            "dedup_lottery_minutes": "720",
            "dedup_lottery_key_mode": "id_prize_keyword",
        }
        self.expires = {}
        self.lists = {}

    def _purge(self, key):
        expires_at = self.expires.get(key)
        if expires_at is not None and expires_at <= self.now:
            self.values.pop(key, None)
            self.expires.pop(key, None)

    def advance(self, seconds):
        self.now += seconds

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
        else:
            self.expires.pop(key, None)
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
    fake_store.format_time = lambda: "2026-07-14 01:43:23"
    old = sys.modules.get("redis_store")
    sys.modules["redis_store"] = fake_store
    try:
        spec = importlib.util.spec_from_file_location("dedup_v13881", APP / "dedup.py")
        module = importlib.util.module_from_spec(spec)
        assert spec is not None and spec.loader is not None
        spec.loader.exec_module(module)
        return module, client
    finally:
        if old is None:
            sys.modules.pop("redis_store", None)
        else:
            sys.modules["redis_store"] = old


class LotteryTemplateFallbackV13881Tests(unittest.TestCase):
    def test_same_scratch_lottery_in_two_templates_is_blocked(self):
        dedup, _client = load_dedup()

        first_duplicate, _reason, first = dedup.check_and_mark(
            DETAILED_SCRATCH_LOTTERY,
            "https://t.me/ycdchannel/60397",
            None,
            "strict",
            "野草地",
        )
        second_duplicate, reason, second = dedup.check_and_mark(
            COMPACT_SCRATCH_LOTTERY,
            "https://t.me/ycdchannel/60401",
            None,
            "strict",
            "野草地",
        )

        self.assertFalse(first_duplicate)
        self.assertNotEqual(first["text_hash"], second["text_hash"])
        self.assertTrue(second_duplicate)
        self.assertIn("同一抽奖的不同模板", reason)

    def test_same_prizes_from_different_sources_are_blocked_globally(self):
        dedup, _client = load_dedup()

        first_duplicate, _reason, _profile = dedup.check_and_mark(
            DETAILED_SCRATCH_LOTTERY,
            "https://t.me/ycdchannel/60397",
            None,
            "strict",
            "野草地",
        )
        second_duplicate, _reason, _profile = dedup.check_and_mark(
            COMPACT_SCRATCH_LOTTERY,
            "https://t.me/another_channel/100",
            None,
            "strict",
            "另一个频道",
        )

        self.assertFalse(first_duplicate)
        self.assertTrue(second_duplicate)
        self.assertIn("同一抽奖的不同模板", _reason)

    def test_changed_prize_list_is_not_blocked(self):
        dedup, _client = load_dedup()
        changed = COMPACT_SCRATCH_LOTTERY.replace("+30币", "+50币")

        first_duplicate, _reason, _profile = dedup.check_and_mark(
            DETAILED_SCRATCH_LOTTERY,
            "https://t.me/ycdchannel/60397",
            None,
            "strict",
            "野草地",
        )
        second_duplicate, _reason, _profile = dedup.check_and_mark(
            changed,
            "https://t.me/ycdchannel/60402",
            None,
            "strict",
            "野草地",
        )

        self.assertFalse(first_duplicate)
        self.assertFalse(second_duplicate)

    def test_template_correlation_expires_after_ten_minutes(self):
        dedup, client = load_dedup()

        first_duplicate, _reason, _profile = dedup.check_and_mark(
            DETAILED_SCRATCH_LOTTERY,
            "https://t.me/ycdchannel/60397",
            None,
            "strict",
            "野草地",
        )
        client.advance(601)
        later_template = COMPACT_SCRATCH_LOTTERY.replace("详情", "新的详情")
        second_duplicate, _reason, _profile = dedup.check_and_mark(
            later_template,
            "https://t.me/ycdchannel/60500",
            None,
            "strict",
            "野草地",
        )

        self.assertFalse(first_duplicate)
        self.assertFalse(second_duplicate)


if __name__ == "__main__":
    unittest.main()
