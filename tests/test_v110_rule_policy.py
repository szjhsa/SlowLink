import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
sys.path.insert(0, str(APP))

import rule_policy


class FakeRulePolicyStore:
    def __init__(self):
        self.values = {}

    def hget(self, key, field):
        return self.values.get((key, field))

    def hset(self, key, field, value):
        self.values[(key, field)] = value

    def hgetall(self, key):
        return {
            field: value
            for (stored_key, field), value in self.values.items()
            if stored_key == key
        }

    def hdel(self, key, field):
        return int(self.values.pop((key, field), None) is not None)


class RulePolicyV110Tests(unittest.TestCase):
    def setUp(self):
        self.store = FakeRulePolicyStore()
        rule_policy.clear_cache()

    def test_code_and_lottery_have_distinct_defaults(self):
        code = rule_policy.default_policy("code")
        lottery = rule_policy.default_policy("lottery")

        self.assertEqual(code["rule_type"], "code")
        self.assertEqual(code["dedup_strategy"], "code_identity")
        self.assertEqual(code["ttl_minutes"], 20)
        self.assertEqual(lottery["rule_type"], "lottery")
        self.assertEqual(lottery["dedup_strategy"], "lottery_identity")
        self.assertEqual(lottery["ttl_minutes"], 720)
        self.assertEqual(lottery["lottery_template_mode"], "global")

    def test_policy_round_trip_and_delete(self):
        rule = r"(?m)^抽奖活动已开始！?$"
        saved = rule_policy.save_rule_policy(rule, "lottery", store=self.store)

        loaded = rule_policy.get_rule_policy(rule, store=self.store)
        self.assertEqual(loaded, saved)
        self.assertEqual(rule_policy.all_rule_policies(store=self.store), {
            rule_policy.policy_key(rule): saved,
        })

        self.assertTrue(rule_policy.delete_rule_policy(rule, store=self.store))
        self.assertIsNone(rule_policy.get_rule_policy(rule, store=self.store))

    def test_invalid_type_is_rejected(self):
        with self.assertRaises(ValueError):
            rule_policy.save_rule_policy(r"test", "unknown", store=self.store)

    def test_stored_json_is_validated_and_normalized(self):
        rule = r"test-rule"
        field = rule_policy.policy_key(rule)
        self.store.values[(rule_policy.RULE_POLICY_KEY, field)] = json.dumps({
            "rule_type": "keyword",
            "dedup_strategy": "normalized_text",
            "ttl_minutes": "60",
        })

        loaded = rule_policy.get_rule_policy(rule, store=self.store)

        self.assertEqual(loaded["rule_type"], "keyword")
        self.assertEqual(loaded["ttl_minutes"], 60)
        self.assertEqual(loaded["dedup_strategy"], "normalized_text")


if __name__ == "__main__":
    unittest.main()
