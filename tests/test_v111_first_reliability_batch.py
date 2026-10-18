import sys
import time
import unittest
from pathlib import Path

from tests.test_v13883_cross_template_lottery_dedup import load_dedup


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"


def load_delivery_state():
    sys.path.insert(0, str(APP))
    try:
        import delivery_state

        return delivery_state
    finally:
        try:
            sys.path.remove(str(APP))
        except ValueError:
            pass


class FakeRedis:
    def __init__(self):
        self.hashes = {}
        self.expires = {}

    def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    def hset(self, key, field, value):
        self.hashes.setdefault(key, {})[field] = value

    def expire(self, key, seconds):
        self.expires[key] = seconds


class FirstReliabilityBatchTests(unittest.TestCase):
    def test_delivery_state_skips_sent_and_recent_pending_targets(self):
        delivery = load_delivery_state()
        redis_client = FakeRedis()
        key = delivery.build_delivery_key(
            "https://t.me/example/1",
            "text",
            "source",
        )

        self.assertTrue(delivery.should_send_target(redis_client, key, "@target"))
        started = time.time()
        delivery.mark_target_state(redis_client, key, "@target", "pending")
        self.assertFalse(
            delivery.should_send_target(redis_client, key, "@target", now=started)
        )
        self.assertTrue(
            delivery.should_send_target(
                redis_client,
                key,
                "@target",
                now=started + delivery.DELIVERY_PENDING_STALE_SECONDS + 1,
            )
        )
        delivery.mark_target_state(redis_client, key, "@target", "sent")
        self.assertFalse(
            delivery.should_send_target(
                redis_client,
                key,
                "@target",
                now=started + delivery.DELIVERY_PENDING_STALE_SECONDS + 1,
            )
        )
        self.assertEqual(
            redis_client.expires[key],
            delivery.DELIVERY_STATE_TTL_SECONDS,
        )

    def test_delivery_state_distinguishes_targets_and_failed_retry(self):
        delivery = load_delivery_state()
        redis_client = FakeRedis()
        key = delivery.build_delivery_key("", "same text", "source", 123)

        delivery.mark_target_state(redis_client, key, "@one", "sent")
        delivery.mark_target_state(redis_client, key, "@two", "failed")

        self.assertFalse(delivery.should_send_target(redis_client, key, "@one"))
        self.assertTrue(delivery.should_send_target(redis_client, key, "@two"))

    def test_delivery_key_survives_public_link_domain_switch(self):
        delivery = load_delivery_state()

        first = delivery.build_delivery_key(
            "https://t.me/example/123",
            "same message",
            "source",
            123,
        )
        second = delivery.build_delivery_key(
            "https://telegram.me/example/123",
            "same message",
            "source",
            123,
        )

        self.assertEqual(first, second)

    def test_pending_duplicate_pruning_is_bounded_and_expires_entries(self):
        source = (APP / "bot_runner.py").read_text(encoding="utf-8-sig")

        self.assertIn("def _prune_pending_duplicates(", source)
        self.assertIn("PENDING_DUPLICATE_TTL_SECONDS", source)
        self.assertIn("PENDING_DUPLICATE_MAX_EVENTS", source)
        self.assertIn("self._prune_pending_duplicates(now)", source)

    def test_message_and_queue_age_gates_are_wired_before_matching(self):
        source = (APP / "bot_runner.py").read_text(encoding="utf-8-sig")
        compose = (ROOT / "deploy" / "docker-compose.yml").read_text(
            encoding="utf-8-sig"
        )
        gate = source.index("process_ts - enqueue_ts > QUEUE_MAX_WAIT_SECONDS")
        match = source.index("analysis = analyze_message(text)")

        self.assertLess(gate, match)
        self.assertIn("process_ts - message_ts > MESSAGE_MAX_AGE_SECONDS", source)
        self.assertIn(
            "process_ts - forward_ts > FORWARD_SOURCE_MAX_AGE_SECONDS",
            source,
        )
        self.assertIn("MESSAGE_MAX_AGE_SECONDS=${MESSAGE_MAX_AGE_SECONDS:-1800}", compose)
        self.assertIn("QUEUE_MAX_WAIT_SECONDS=${QUEUE_MAX_WAIT_SECONDS:-300}", compose)

    def test_main_matcher_passes_plugin_format_gates(self):
        source = (APP / "matcher.py").read_text(encoding="utf-8-sig")

        self.assertIn(
            '"format_gates": builtin_section("format_gates", {}) or {}',
            source,
        )

    def test_plugin_schema_validates_format_gates(self):
        sys.path.insert(0, str(APP))
        try:
            import plugin_registry

            with self.assertRaises(ValueError):
                plugin_registry.validate_rules_data(
                    {
                        "matcher": {},
                        "code_rules": {},
                        "dedup": {},
                        "flow": {},
                        "format_gates": {"broken": {"regex_min_hits": "bad"}},
                    }
                )
        finally:
            try:
                sys.path.remove(str(APP))
            except ValueError:
                pass

    def test_delivery_module_is_imported_by_runner(self):
        source = (APP / "bot_runner.py").read_text(encoding="utf-8-sig")

        self.assertIn("from delivery_state import", source)
        self.assertIn("build_delivery_key(", source)
        self.assertIn("should_send_target(", source)
        self.assertIn("mark_target_state(", source)

    def test_registration_open_time_is_part_of_dedup_identity(self):
        dedup, _client = load_dedup()
        policy = {
            "rule_type": "keyword",
            "dedup_strategy": "normalized_text",
            "ttl_minutes": 60,
        }
        first = (
            "🎉 开放注册已开启\n"
            "✨ 本服务器现已开放注册！\n"
            "⏰ 开启时间：2026-10-09 22:56"
        )
        second = first.replace("22:56", "23:27")

        first_profile = dedup.build_profile(first, policy=policy)
        second_profile = dedup.build_profile(second, policy=policy)

        self.assertEqual(first_profile["registration_open_time"], "2026-10-09 22:56")
        self.assertEqual(second_profile["registration_open_time"], "2026-10-09 23:27")
        self.assertNotEqual(first_profile["dedup_id"], second_profile["dedup_id"])


if __name__ == "__main__":
    unittest.main()
