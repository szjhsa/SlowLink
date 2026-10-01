import re
import sys
import time
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
sys.path.insert(0, str(APP))

fake_redis = types.ModuleType("redis")
fake_redis.Redis = lambda *args, **kwargs: object()
old_redis_module = sys.modules.get("redis")
sys.modules["redis"] = fake_redis
try:
    import code_identity_plugin
finally:
    if old_redis_module is None:
        sys.modules.pop("redis", None)
    else:
        sys.modules["redis"] = old_redis_module


class FakeRedis:
    def __init__(self):
        self.zsets = {}

    def zadd(self, key, mapping):
        bucket = self.zsets.setdefault(key, {})
        for member, score in mapping.items():
            bucket[str(member)] = float(score)
        return 1

    def zrangebyscore(self, key, minimum, maximum):
        bucket = self.zsets.get(key, {})
        low = float("-inf") if minimum == "-inf" else float(minimum)
        high = float("inf") if maximum == "+inf" else float(maximum)
        return [
            member
            for member, score in sorted(bucket.items(), key=lambda item: item[1])
            if low <= score <= high
        ]

    def zrem(self, key, member):
        bucket = self.zsets.get(key, {})
        existed = str(member) in bucket
        bucket.pop(str(member), None)
        return int(existed)

    def expire(self, key, seconds):
        return True

    def zcard(self, key):
        return len(self.zsets.get(key, {}))

    def zremrangebyrank(self, key, start, end):
        bucket = self.zsets.get(key, {})
        ordered = [item[0] for item in sorted(bucket.items(), key=lambda item: item[1])]
        for member in ordered[start:end + 1]:
            bucket.pop(member, None)
        return 0

    def pipeline(self):
        return self

    def execute(self):
        return []


def configure(plugin, redis, *, min_fixed_chars=6):
    extract_pattern = re.compile(
        r"(?<![A-Za-z0-9_-])(?P<code>[^\s`：:，,]+(?:-[^\s`：:，,]+)*-\d+-(?:Register|Renew)_[^\s`]+?)"
        r"(?=$|\s|[，。！？？；：、）】]|[,.;:)\]}>`~](?![A-Za-z0-9_-]))"
    )
    plugin.r = redis
    plugin._CONFIG_CACHE.update({
        "ts": time.time(),
        "config": {
            "mask_char": "*",
            "mask_mode": "any_length",
            "mask_width": 1,
            "min_fixed_chars": min_fixed_chars,
            "ttl_seconds": 1200,
            "pending_seconds": 30,
            "max_candidates": 300,
            "scope_regex": re.compile(r"^(?P<scope>.+?-(?:Register|Renew)_)(?P<suffix>.+)$"),
            "extract_res": (extract_pattern,),
        },
    })


class CodeIdentityPluginV110Tests(unittest.TestCase):
    def test_listener_integrates_claim_commit_and_release(self):
        source = (APP / "bot_runner.py").read_text(encoding="utf-8-sig")

        self.assertIn("claim_code_identities(", source)
        self.assertIn("commit_code_identities(plugin_code_claim)", source)
        self.assertGreaterEqual(source.count("release_code_identities(plugin_code_claim)"), 3)

    def test_masked_code_then_plain_code_is_blocked(self):
        redis = FakeRedis()
        configure(code_identity_plugin, redis)

        duplicate, _reason, claim = code_identity_plugin.claim_code_identities(
            ["strong_register_renew:XYING-30-Register_sTUL**Vuvb"]
        )
        self.assertFalse(duplicate)
        code_identity_plugin.commit_code_identities(claim)

        duplicate, reason, _claim = code_identity_plugin.claim_code_identities(
            ["strong_register_renew:XYING-30-Register_sTUL51Vuvb"]
        )
        self.assertTrue(duplicate)
        self.assertIn("相同码特征重复", reason)

    def test_multiple_codes_then_one_known_code_is_blocked(self):
        redis = FakeRedis()
        configure(code_identity_plugin, redis)
        first_text = (
            "码1：XYING-30-Register_sTUL**Vuvb\n"
            "码2：WENJIAN-30-Register_abc**defg"
        )
        first_values = code_identity_plugin.extract_code_values(first_text)
        self.assertEqual(first_values, [
            "XYING-30-Register_sTUL**Vuvb",
            "WENJIAN-30-Register_abc**defg",
        ])

        duplicate, _reason, claim = code_identity_plugin.claim_code_identities(
            ["plugin_code:" + value for value in first_values]
        )
        self.assertFalse(duplicate)
        code_identity_plugin.commit_code_identities(claim)

        second_values = code_identity_plugin.extract_code_values(
            "XYING-30-Register_sTUL51Vuvb"
        )
        duplicate, reason, _claim = code_identity_plugin.claim_code_identities(
            ["plugin_code:" + value for value in second_values]
        )
        self.assertTrue(duplicate)
        self.assertIn("相同码特征重复", reason)

    def test_merge_replaces_truncated_core_identities_with_full_plugin_codes(self):
        redis = FakeRedis()
        configure(code_identity_plugin, redis)
        text = (
            "码1：XYING-30-Register_sTUL**Vuvb\n"
            "码2：WENJIAN-30-Register_abc**defg"
        )
        merged = code_identity_plugin.merge_code_identities(
            text,
            "strong_register_renew:码1：XYING-30-Register_sTUL",
            [
                "strong_register_renew:码1：XYING-30-Register_sTUL",
                "strong_register_renew:XYING-30-Register_sTUL",
                "strong_register_renew:码2：WENJIAN-30-Register_abc",
                "strong_register_renew:WENJIAN-30-Register_abc",
            ],
        )

        self.assertEqual(merged, [
            "plugin_code:XYING-30-Register_sTUL**Vuvb",
            "plugin_code:WENJIAN-30-Register_abc**defg",
        ])

    def test_second_message_with_known_and_new_code_is_blocked(self):
        redis = FakeRedis()
        configure(code_identity_plugin, redis)
        first = code_identity_plugin.extract_code_values(
            "XYING-30-Register_sTUL**Vuvb"
        )
        duplicate, _reason, claim = code_identity_plugin.claim_code_identities(
            ["plugin_code:" + value for value in first]
        )
        self.assertFalse(duplicate)
        code_identity_plugin.commit_code_identities(claim)

        second = code_identity_plugin.extract_code_values(
            "XYING-30-Register_sTUL51Vuvb\nWENJIAN-30-Register_new**Code"
        )
        self.assertEqual(len(second), 2)
        duplicate, _reason, _claim = code_identity_plugin.claim_code_identities(
            ["plugin_code:" + value for value in second]
        )
        self.assertTrue(duplicate)

    def test_plain_code_then_masked_code_is_blocked(self):
        redis = FakeRedis()
        configure(code_identity_plugin, redis)

        duplicate, _reason, claim = code_identity_plugin.claim_code_identities(
            ["strong_register_renew:XYING-30-Register_Guess51Code"]
        )
        self.assertFalse(duplicate)
        code_identity_plugin.commit_code_identities(claim)

        duplicate, _reason, _claim = code_identity_plugin.claim_code_identities(
            ["strong_register_renew:XYING-30-Register_Guess**Code"]
        )
        self.assertTrue(duplicate)

    def test_chinese_and_symbols_can_live_between_fixed_anchors(self):
        redis = FakeRedis()
        configure(code_identity_plugin, redis)

        duplicate, _reason, claim = code_identity_plugin.claim_code_identities(
            ["strong_register_renew:Z-30-Register_猜谜**Vuvb"]
        )
        self.assertFalse(duplicate)
        code_identity_plugin.commit_code_identities(claim)

        duplicate, _reason, _claim = code_identity_plugin.claim_code_identities(
            ["strong_register_renew:Z-30-Register_猜谜中文-@Vuvb"]
        )
        self.assertTrue(duplicate)

    def test_short_fixed_part_does_not_use_wildcard_matching(self):
        redis = FakeRedis()
        configure(code_identity_plugin, redis, min_fixed_chars=10)

        duplicate, _reason, claim = code_identity_plugin.claim_code_identities(
            ["strong_register_renew:Z-30-Register_a**b"]
        )
        self.assertFalse(duplicate)
        self.assertIsNone(claim)

    def test_pending_claim_blocks_second_worker_before_send_finishes(self):
        redis = FakeRedis()
        configure(code_identity_plugin, redis)

        duplicate, _reason, first_claim = code_identity_plugin.claim_code_identities(
            ["strong_register_renew:XYING-30-Register_sTUL**Vuvb"]
        )
        self.assertFalse(duplicate)
        self.assertIsNotNone(first_claim)

        duplicate, _reason, second_claim = code_identity_plugin.claim_code_identities(
            ["strong_register_renew:XYING-30-Register_sTUL51Vuvb"]
        )
        self.assertTrue(duplicate)
        self.assertIsNone(second_claim)

        code_identity_plugin.release_code_identities(first_claim)


if __name__ == "__main__":
    unittest.main()
