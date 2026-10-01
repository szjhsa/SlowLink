import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
SOURCE_MESSAGE = (
    "GuaiCum-30-Register_ZlB5*cqm*u\n\n"
    "提示：没事就喜欢整两炮\n\n"
    "怪 18+服，需要的猜猜"
)
EXPECTED_CODE = "GuaiCum-30-Register_ZlB5cqmu"


def inspect_with_empty_main_rules(text: str) -> dict:
    fake_store = types.ModuleType("redis_store")
    fake_store.get_json = lambda key, default=None: default
    fake_store.set_json = lambda *args, **kwargs: None
    fake_store.smembers = lambda key: set()

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


class ObfuscatedRegisterTriggerV13879Tests(unittest.TestCase):
    def test_embedded_asterisks_are_removed_from_complete_register_code(self):
        result = inspect_with_empty_main_rules(SOURCE_MESSAGE)

        self.assertEqual(result["detail"].get("code"), EXPECTED_CODE)
        self.assertEqual(
            result["detail"].get("identity"),
            "strong_register_renew:" + EXPECTED_CODE,
        )

    def test_complete_register_code_triggers_without_a_main_regex_rule(self):
        result = inspect_with_empty_main_rules(SOURCE_MESSAGE)

        self.assertTrue(result["trigger"].get("can_trigger"))
        self.assertEqual(result["trigger"].get("code"), EXPECTED_CODE)
        self.assertTrue(result["analysis"].get("matched"))
        self.assertTrue(str(result["analysis"].get("rule") or "").startswith("plugin:"))

    def test_usage_notice_with_obfuscated_code_remains_blocked(self):
        text = "注册码使用 - Roman 使用了 GuaiCum-30-Register_ZlB5*cqm*u"

        result = inspect_with_empty_main_rules(text)

        self.assertFalse(result["analysis"].get("matched"))
        self.assertTrue(result["analysis"].get("usage_notice"))

    def test_period_inside_complete_code_is_preserved(self):
        result = inspect_with_empty_main_rules("GuaiCum-30-Register_ZlB5.cqmu")

        self.assertTrue(result["analysis"].get("matched"))
        self.assertTrue(result["trigger"].get("can_trigger"))
        self.assertEqual(result["trigger"].get("code"), "GuaiCum-30-Register_ZlB5.cqmu")


if __name__ == "__main__":
    unittest.main()
