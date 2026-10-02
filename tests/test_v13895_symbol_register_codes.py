import re
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
EXPECTED_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8-sig").strip()
SOURCE_CODE = "帝服-30-Register_mK@nxdnuwU"
OLD_MASKED_PATTERN = (
    r"(?:^|(?<=[\s:：，,]))[^\s*`-]+(?:-[^\s*`-]+)*-\d+"
    r"(?:-[^\s*`-]+)*-(?:Register|Renew)_(?:[A-Za-z0-9_-]|数字|字母)+"
    r"(?=$|\s|[，。！？？；：、）】]|[,.;:)\]}>`~*](?![A-Za-z0-9_-]))"
)
OLD_SERVER_MASKED_PATTERN = (
    r"(?:^|(?<=[\s:：，,]))[^\s*`-]+(?:-[^\s*`-]+)*-\d+"
    r"(?:-[^\s*`-]+)*-(?:Register|Renew)_(?:[A-Za-z0-9_-]|数字|字母)+"
    r"(?![A-Za-z0-9_-]|[\u3400-\u9fff])"
)


def load_modules(stored_code_rules=None, regex_rules=()):
    saved = []
    fake_store = types.ModuleType("redis_store")
    fake_store.get_json = lambda key, default=None: stored_code_rules if key == "code_rules" else default
    fake_store.set_json = lambda *args, **kwargs: saved.append(args)
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

        return code_rules, matcher, saved
    finally:
        sys.path.remove(str(APP))
        for name, old in old_modules.items():
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old


def load_dedup():
    fake_store = types.ModuleType("redis_store")
    fake_store.r = None
    fake_store.sha = lambda value: "x"
    fake_store.format_time = lambda: ""
    old = sys.modules.get("redis_store")
    sys.modules["redis_store"] = fake_store
    sys.path.insert(0, str(APP))
    try:
        sys.modules.pop("dedup", None)
        import dedup

        return dedup
    finally:
        sys.path.remove(str(APP))
        if old is None:
            sys.modules.pop("redis_store", None)
        else:
            sys.modules["redis_store"] = old


class SymbolRegisterCodeV13895Tests(unittest.TestCase):
    def test_version_is_v13895(self):
        self.assertEqual((ROOT / "VERSION").read_text(encoding="utf-8-sig").strip(), EXPECTED_VERSION)
        config = (APP / "config.py").read_text(encoding="utf-8-sig")
        self.assertIn(f'APP_VERSION = "{EXPECTED_VERSION}"', config)

    def test_at_symbol_code_is_extracted_and_triggers(self):
        code_rules, matcher, _saved = load_modules(
            regex_rules={r"帝服-30-Register_"}
        )
        text = SOURCE_CODE + " 艾特符号猜一个数字"

        detail = code_rules.extract_code_detail(text)
        result = matcher.analyze_message(text)

        self.assertEqual(detail.get("code"), SOURCE_CODE)
        self.assertEqual(detail.get("identity"), "strong_register_renew:" + SOURCE_CODE)
        self.assertTrue(result.get("matched"))

    def test_common_internal_symbols_are_preserved(self):
        code_rules, _matcher, _saved = load_modules()
        suffixes = (
            "mK@nxdnuwU",
            "mK.nxdnuwU",
            "mK#nxdnuwU",
            "mK$nxdnuwU",
            "mK%nxdnuwU",
            "mK+nxdnuwU",
            "mK=nxdnuwU",
            "mK&nxdnuwU",
            "mK^nxdnuwU",
            "mK(n)xdnuwU",
            "mK[n]xdnuwU",
            "mK{n}xdnuwU",
            "mK|nxdnuwU",
            "mK/nxdnuwU",
            "mK?nxdnuwU",
            "mK★nxdnuwU",
        )

        for suffix in suffixes:
            with self.subTest(suffix=suffix):
                code = "帝服-30-Register_" + suffix
                detail = code_rules.extract_code_detail(code)
                self.assertEqual(detail.get("code"), code)

    def test_sentence_punctuation_is_not_included_in_code(self):
        code_rules, _matcher, _saved = load_modules()

        detail = code_rules.extract_code_detail(SOURCE_CODE + "，艾特符号猜一个数字")

        self.assertEqual(detail.get("code"), SOURCE_CODE)

    def test_special_symbol_codes_get_distinct_dedup_fingerprints(self):
        dedup = load_dedup()
        text = "\n".join((SOURCE_CODE, "帝服-30-Register_mK#nxdnuwU"))

        fingerprints = dedup._register_renew_code_fingerprints(text)

        self.assertEqual(len(fingerprints), 2)
        self.assertEqual(len(set(fingerprints)), 2)

    def test_stored_masked_rule_migrates_to_symbol_rule(self):
        stored = [{
            "name": "Register/Renew 完整码",
            "pattern": OLD_MASKED_PATTERN,
            "group": "0",
            "enabled": True,
            "fast": True,
            "trigger": False,
            "strict_context": False,
        }]
        code_rules, _matcher, saved = load_modules(stored)

        rules = code_rules.get_code_rules()
        pattern = rules[0]["pattern"]

        self.assertNotEqual(pattern, OLD_MASKED_PATTERN)
        self.assertIsNotNone(re.compile(pattern, re.I | re.M).search(SOURCE_CODE))
        self.assertTrue(saved)

    def test_live_server_masked_rule_migrates_to_symbol_rule(self):
        stored = [{
            "name": "Register/Renew 完整码",
            "pattern": OLD_SERVER_MASKED_PATTERN,
            "group": "0",
            "enabled": True,
            "fast": True,
            "trigger": False,
            "strict_context": False,
        }]
        code_rules, _matcher, saved = load_modules(stored)

        rules = code_rules.get_code_rules()
        pattern = rules[0]["pattern"]

        self.assertNotEqual(pattern, OLD_SERVER_MASKED_PATTERN)
        self.assertIsNotNone(re.compile(pattern, re.I | re.M).search(SOURCE_CODE))
        self.assertTrue(saved)


if __name__ == "__main__":
    unittest.main()
