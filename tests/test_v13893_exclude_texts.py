import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"


def load_matcher(exclude_texts=None, regex_rules=None):
    sets = {
        "exclude_texts": set(exclude_texts or []),
        "regex_rules": set(regex_rules or []),
    }
    fake_store = types.ModuleType("redis_store")
    fake_store.smembers = lambda key: sets.get(key, set())
    fake_code_rules = types.ModuleType("code_rules")
    fake_code_rules.extract_code_detail = lambda _text: {}
    fake_code_rules.extract_trigger_code_detail = lambda _text: {}
    replacements = {
        "redis_store": fake_store,
        "code_rules": fake_code_rules,
    }
    old_modules = {name: sys.modules.get(name) for name in replacements}
    sys.modules.update(replacements)
    try:
        spec = importlib.util.spec_from_file_location("matcher_v13893", APP / "matcher.py")
        module = importlib.util.module_from_spec(spec)
        assert spec is not None and spec.loader is not None
        spec.loader.exec_module(module)
        return module
    finally:
        for name, old in old_modules.items():
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old


class ExcludeTextsV13893Tests(unittest.TestCase):
    def test_exclude_text_wins_even_when_positive_rule_matches(self):
        matcher = load_matcher(
            exclude_texts={"仅管理员可用"},
            regex_rules={"开放注册"},
        )
        text = "🎉 开放注册\n提示：仅管理员可用"

        result = matcher.analyze_message(text)

        self.assertFalse(result["matched"])
        self.assertTrue(result["excluded_text_notice"])
        self.assertEqual(result["excluded_keyword"], "仅管理员可用")

    def test_exclude_text_is_case_insensitive_after_normalization(self):
        matcher = load_matcher(
            exclude_texts={"TURN OFF"},
            regex_rules={"定时注册"},
        )

        result = matcher.match_rule_details("定时注册 | Turn off")

        self.assertFalse(result["matched"])
        self.assertTrue(result["excluded_text_notice"])
        self.assertEqual(result["excluded_keyword"], "TURN OFF")

    def test_all_matcher_entry_points_apply_exclude_text_first(self):
        matcher = load_matcher(
            exclude_texts={"内部测试"},
            regex_rules={"自由注册"},
        )
        text = "自由注册已开启，仅供内部测试"

        analysis = matcher.analyze_message(text)
        matched, rule = matcher.match_rules(text)
        details = matcher.match_rule_details(text)

        self.assertFalse(analysis["matched"])
        self.assertTrue(analysis["excluded_text_notice"])
        self.assertFalse(matched)
        self.assertEqual(rule, "")
        self.assertFalse(details["matched"])
        self.assertTrue(details["excluded_text_notice"])

    def test_message_without_exclude_text_keeps_existing_rule_behavior(self):
        matcher = load_matcher(
            exclude_texts={"内部测试"},
            regex_rules={"自由注册"},
        )

        result = matcher.analyze_message("🎟️ 自由注册已开启")

        self.assertTrue(result["matched"])
        self.assertEqual(result["rule"], "自由注册")
        self.assertFalse(result.get("excluded_text_notice", False))

    def test_web_ui_and_backup_include_exclude_texts(self):
        web = (APP / "web.py").read_text(encoding="utf-8-sig")
        template = (APP / "templates" / "index.html").read_text(encoding="utf-8-sig")

        self.assertIn('@app.post("/add_exclude_text")', web)
        self.assertIn('@app.post("/del_exclude_text")', web)
        self.assertIn('"exclude_texts": sorted(smembers("exclude_texts"))', web)
        self.assertIn('("exclude_texts", "exclude_texts", "排除文本")', web)
        self.assertIn('analysis.get("excluded_text_notice", False)', web)
        self.assertIn('analysis.get("excluded_keyword", "")', web)
        self.assertIn('action="/add_exclude_text"', template)
        self.assertIn("data.exclude_texts || []", template)
        self.assertIn("'/del_exclude_text'", template)
        self.assertIn("命中排除文本", template)


if __name__ == "__main__":
    unittest.main()
