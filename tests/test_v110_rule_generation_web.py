import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


class RuleGenerationWebV110Tests(unittest.TestCase):
    def test_generation_routes_exist_and_validate_before_save(self):
        source = read(APP / "web.py")

        self.assertIn('@app.post("/rule_generate")', source)
        self.assertIn('@app.post("/add_generated_rule")', source)
        self.assertIn("generate_rule(rule_type, sample)", source)
        self.assertIn("save_rule_policy(pattern, rule_type)", source)
        self.assertIn('_regex.compile(pattern, _regex.I | _regex.M)', source)
        self.assertIn('sadd("regex_rules", pattern)', source)

    def test_regex_tab_contains_type_first_generator_form(self):
        template = read(APP / "templates" / "index.html")

        self.assertIn("按原消息生成规则", template)
        self.assertIn("先选择规则类型，再粘贴原消息", template)
        self.assertIn('<select name="rule_type">', template)
        self.assertIn('<textarea name="sample"', template)
        self.assertIn('data-result="rule_generate"', template)
        self.assertIn("renderRuleGenerateResult", template)

    def test_rule_list_accepts_policy_badges(self):
        template = read(APP / "templates" / "index.html")

        self.assertIn("renderRegexList(data.regex_rules || [], data.disabled_regex_rules || [], data.rule_policies || {})", template)
        self.assertIn("rule-policy-badge", template)

    def test_rule_policy_can_be_modified_from_rule_list(self):
        source = read(APP / "web.py")
        template = read(APP / "templates" / "index.html")

        self.assertIn('@app.post("/update_rule_policy")', source)
        self.assertIn('overrides={"ttl_minutes": ttl_minutes},', source)
        self.assertIn('action="/update_rule_policy"', template)
        self.assertIn("修改策略", template)
        self.assertIn('name="rule_type"', template)
        self.assertIn('name="ttl_minutes"', template)


if __name__ == "__main__":
    unittest.main()
