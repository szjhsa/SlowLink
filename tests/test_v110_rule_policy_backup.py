import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


class RulePolicyBackupV110Tests(unittest.TestCase):
    def test_plugin_declares_generated_rule_defaults(self):
        rules = read(APP / "plugins" / "builtin" / "rules.json")

        self.assertIn('"rule_types"', rules)
        self.assertIn('"dedup_strategy": "code_identity"', rules)
        self.assertIn('"dedup_strategy": "lottery_identity"', rules)
        self.assertIn('"forward": false', rules)

    def test_export_and_import_include_rule_policies(self):
        source = read(APP / "web.py")

        self.assertIn('"rule_policies": [', source)
        self.assertIn('snap["rule_policies"] = ("hash", r.hgetall(RULE_POLICY_KEY) or {})', source)
        self.assertIn("save_rule_policy(rule, rule_type, overrides=policy)", source)

    def test_regex_test_uses_matched_rule_policy(self):
        source = read(APP / "web.py")

        self.assertIn('rule_policy = analysis.get("rule_policy") or {}', source)
        self.assertIn('profile = build_profile(text, "", policy=rule_policy)', source)


if __name__ == "__main__":
    unittest.main()
