import json
import re
import sys
import time
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
SAMPLE = "WindMoon-Whitelist_1OTb0O0FMO"
WHITELIST_MAIN_RULE = (
    r"(?:^|(?<=[\s:：，,]))[^\s*`\-:：，,]+(?:-[^\s*`\-:：，,]+)*-"
    r"Whitelist_(?a:[A-Za-z0-9]{10})"
    r"(?=$|\s|[，。！？？；：、）】]|[,.;:)\]}>`~*](?![A-Za-z0-9_-]))"
)


def inspect_message(text: str, regex_rules=()) -> dict:
    fake_store = types.ModuleType("redis_store")
    fake_store.get_json = lambda key, default=None: default
    fake_store.set_json = lambda *args, **kwargs: None
    fake_store.smembers = lambda key: set(regex_rules) if key == "regex_rules" else set()

    module_names = ("redis_store", "code_rules", "matcher")
    old_modules = {name: sys.modules.get(name) for name in module_names}
    sys.modules["redis_store"] = fake_store
    sys.modules.pop("code_rules", None)
    sys.modules.pop("matcher", None)
    sys.path.insert(0, str(APP))
    try:
        import code_rules
        import matcher

        return {
            "module": code_rules,
            "detail": code_rules.extract_code_detail(text),
            "trigger": code_rules.extract_trigger_code_detail(text),
            "analysis": matcher.analyze_message(text),
        }
    finally:
        sys.path.remove(str(APP))
        for name, old in old_modules.items():
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old


def inspect_with_empty_main_rules(text: str) -> dict:
    return inspect_message(text, ())


class WhitelistCodeV13888Tests(unittest.TestCase):
    def test_exact_code_is_identified_but_does_not_trigger_without_main_rule(self):
        result = inspect_with_empty_main_rules(SAMPLE)

        self.assertEqual(result["detail"].get("code"), SAMPLE)
        self.assertEqual(
            result["detail"].get("identity"),
            "strong_whitelist:" + SAMPLE,
        )
        self.assertFalse(result["trigger"].get("can_trigger"))
        self.assertFalse(result["analysis"].get("matched"))

    def test_matching_main_regex_triggers_and_attaches_code_identity(self):
        result = inspect_message(SAMPLE, (WHITELIST_MAIN_RULE,))

        self.assertTrue(result["analysis"].get("matched"))
        self.assertEqual(result["analysis"].get("rule"), WHITELIST_MAIN_RULE)
        self.assertEqual(result["analysis"]["code_detail"].get("code"), SAMPLE)
        self.assertEqual(
            result["analysis"]["code_detail"].get("identity"),
            "strong_whitelist:" + SAMPLE,
        )

    def test_multi_segment_project_name_and_punctuation_are_supported(self):
        code = "Wind-Moon-Pro-Whitelist_a1B2c3D4e5"
        result = inspect_with_empty_main_rules("领取：" + code + "。")

        self.assertEqual(result["detail"].get("code"), code)
        self.assertEqual(
            result["detail"].get("identity"),
            "strong_whitelist:" + code,
        )

    def test_suffix_must_be_exactly_ten_alphanumeric_characters(self):
        invalid = (
            "WindMoon-Whitelist_1OTb0O0FM",
            "WindMoon-Whitelist_1OTb0O0FMO1",
            "WindMoon-Whitelist_1OTb0O0FM_",
            "WindMoon-Whitelist_1OTb0O0FMİ",
        )

        for text in invalid:
            with self.subTest(text=text):
                result = inspect_with_empty_main_rules(text)
                self.assertFalse(result["trigger"].get("can_trigger"))
                self.assertFalse(result["analysis"].get("matched"))

    def test_usage_notice_with_whitelist_code_remains_blocked(self):
        result = inspect_with_empty_main_rules("注册码使用 - Roman 使用了 " + SAMPLE)

        self.assertFalse(result["analysis"].get("matched"))
        self.assertTrue(result["analysis"].get("usage_notice"))

    def test_random_suffix_case_is_preserved_in_dedup_identity(self):
        first = inspect_with_empty_main_rules(SAMPLE)["detail"]
        second_code = "WindMoon-Whitelist_1otb0o0fmo"
        second = inspect_with_empty_main_rules(second_code)["detail"]

        self.assertEqual(first.get("identity"), "strong_whitelist:" + SAMPLE)
        self.assertEqual(second.get("identity"), "strong_whitelist:" + second_code)
        self.assertNotEqual(first.get("identity"), second.get("identity"))

    def test_whitelist_pattern_stays_fast_on_long_hyphen_text(self):
        result = inspect_with_empty_main_rules(SAMPLE)
        pattern = getattr(result["module"], "SAFE_WHITELIST_PATTERN", "")
        self.assertTrue(pattern)
        compiled = re.compile(pattern, re.I | re.M)
        adversarial = ("ABCD-" * 1700)[:8192]

        started = time.perf_counter()
        self.assertIsNone(compiled.search(adversarial))
        self.assertLess((time.perf_counter() - started) * 1000, 100)

    def test_whitelist_messages_use_priority_queue(self):
        source = (APP / "bot_runner.py").read_text(encoding="utf-8-sig")
        rules = json.loads(
            (APP / "plugins" / "builtin" / "rules.json").read_text(encoding="utf-8-sig")
        )

        self.assertIn("whitelist", rules["flow"]["priority_keywords"])
        self.assertIn("priority_keywords = _plugin_builtin_value", source)

    def test_page_documents_builtin_whitelist_protection(self):
        source = (APP / "templates" / "index.html").read_text(encoding="utf-8-sig")

        self.assertIn("Whitelist 十位完整码需先命中正则", source)

    def test_version_sources_are_consistent(self):
        version = (ROOT / "VERSION").read_text(encoding="utf-8-sig").strip()
        config = (APP / "config.py").read_text(encoding="utf-8-sig")

        self.assertIn(f'APP_VERSION = "{version}"', config)


if __name__ == "__main__":
    unittest.main()
