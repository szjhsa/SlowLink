import importlib.util
import sys
import types
import unittest
from pathlib import Path

from tests.test_v13871_cpu_dedup_stability import load_code_rules_with_fake_redis


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
EXPECTED_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8-sig").strip()

SAMPLES = [
    "Cc-register-TwPaGelK-uSvj-eWY-uEhZA",
    "Peach-register-dvCi74pyjNDEH6K-t5uiFXU-gKkxP-Q0n",
    "Cc-register-Ugln数字字母fY-IUwx-Cax-j6rYe",
]


def load_real_matcher():
    code_rules, _saved = load_code_rules_with_fake_redis()
    fake_store = types.ModuleType("redis_store")
    fake_store.smembers = lambda key: set()
    fake_store.log_line = lambda *a, **k: None
    old_modules = {name: sys.modules.get(name) for name in ("redis_store", "code_rules")}
    sys.modules["redis_store"] = fake_store
    sys.modules["code_rules"] = code_rules
    try:
        spec = importlib.util.spec_from_file_location("matcher_v13917", APP / "matcher.py")
        module = importlib.util.module_from_spec(spec)
        assert spec is not None and spec.loader is not None
        spec.loader.exec_module(module)
        return module
    finally:
        for name, module in old_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


class HyphenRegisterCodesV13917Tests(unittest.TestCase):
    def test_versions_are_bumped_to_current_release(self):
        self.assertEqual(read_version(), EXPECTED_VERSION)
        config = (APP / "config.py").read_text(encoding="utf-8-sig")
        self.assertIn(f'APP_VERSION = "{EXPECTED_VERSION}"', config)

    def test_hyphen_codes_are_extracted_as_strong_register_renew(self):
        code_rules, _saved = load_code_rules_with_fake_redis()

        for sample in SAMPLES:
            detail = code_rules.extract_code_detail(sample)
            self.assertEqual(detail.get("code"), sample)
            self.assertEqual(detail.get("identity"), "strong_register_renew:" + sample)
            self.assertEqual(detail.get("name"), "Register/Renew 完整码")

    def test_hyphen_codes_share_strong_trigger_behavior(self):
        code_rules, _saved = load_code_rules_with_fake_redis()

        for sample in SAMPLES:
            detail = code_rules.extract_trigger_code_detail(sample)
            self.assertTrue(detail.get("can_trigger"))

    def test_usage_notification_containing_hyphen_code_is_filtered(self):
        matcher = load_real_matcher()
        usage = (
            "TGID:866086361\n"
            "用户:Saladdays ，使用注册码Cc-register-JsxZlI3M-EVSN-Ekr-zGhvT，成功注册账号"
        )

        result = matcher.match_rule_details(usage)

        self.assertFalse(result["matched"])
        self.assertTrue(result["usage_notice"])

    def test_generated_code_success_message_is_not_usage_filtered(self):
        matcher = load_real_matcher()
        generated = (
            "🎁恭喜,生成注册码成功!\n"
            "💰剩余积分为: 0\n"
            "✨注册码:Cc-register-Ugln数字字母fY-IUwx-Cax-j6rYe\n\n"
            "Tips:也可以在 **注册码-专属注册码** 那里查看!"
        )

        result = matcher.match_rule_details(generated)

        self.assertTrue(result["matched"])
        self.assertFalse(result["usage_notice"])

    def test_hyphen_code_fingerprints_are_in_register_renew_family(self):
        code_rules, _saved = load_code_rules_with_fake_redis()
        fake_store = types.ModuleType("redis_store")
        fake_store.r = types.SimpleNamespace()
        fake_store.get = lambda *a, **k: None
        fake_store.smembers = lambda key: set()
        fake_store.sha = lambda *a, **k: "x"
        fake_store.format_time = lambda *a, **k: "t"
        old = {name: sys.modules.get(name) for name in ("redis_store", "code_rules")}
        sys.modules["redis_store"] = fake_store
        sys.modules["code_rules"] = code_rules
        try:
            spec = importlib.util.spec_from_file_location("dedup_v13917", APP / "dedup.py")
            dedup = importlib.util.module_from_spec(spec)
            assert spec is not None and spec.loader is not None
            spec.loader.exec_module(dedup)
            fingerprints = dedup._register_renew_code_fingerprints(SAMPLES[0])
        finally:
            for name, module in old.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module

        self.assertEqual(len(fingerprints), 1)
        self.assertTrue(fingerprints[0].startswith("rrc"))


def read_version() -> str:
    return (ROOT / "VERSION").read_text(encoding="utf-8-sig").strip()


if __name__ == "__main__":
    unittest.main()
