import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"


def make_fake_store():
    plugin_rules = json.loads(
        (APP / "plugins" / "builtin" / "rules.json").read_text(
            encoding="utf-8-sig"
        )
    )
    code_rules = list(
        (plugin_rules.get("code_rules") or {}).get("default_rules") or []
    )
    code_rules.append({
        "name": "CK 完整码",
        "pattern": r"(?<![A-Za-z0-9])CK[^\s]{12}(?=$|\s|[，。！？？；：、】]|[,.;:)\]}>`~*](?![A-Za-z0-9_-]))",
        "group": "0",
        "enabled": True,
        "fast": True,
        "trigger": False,
        "strict_context": False,
        "note": "",
    })
    fake_store = types.ModuleType("redis_store")
    fake_store.get = lambda key, default=None: default
    fake_store.get_json = lambda key, default=None: code_rules if key == "code_rules" else default
    fake_store.set_json = lambda *args, **kwargs: None
    fake_store.smembers = lambda *args, **kwargs: set()
    fake_store.sha = lambda value: str(abs(hash(value)))
    fake_store.format_time = lambda *args, **kwargs: "2026-01-01 00:00:00"
    fake_store.log_line = lambda *args, **kwargs: None
    fake_store.r = types.SimpleNamespace()
    return fake_store


def plugin_match(text):
    fake_store = make_fake_store()
    old_store = sys.modules.get("redis_store")
    sys.modules["redis_store"] = fake_store
    sys.path.insert(0, str(APP))
    try:
        from plugin_runtime import call_hook

        return call_hook(
            "match_plugin_event",
            {"text": text, "normalized": text, "compact": text},
            default=None,
        )
    finally:
        try:
            sys.path.remove(str(APP))
        except ValueError:
            pass
        if old_store is None:
            sys.modules.pop("redis_store", None)
        else:
            sys.modules["redis_store"] = old_store


def load_matcher(plugin_match=None, regex_rules=None):
    fake_store = types.ModuleType("redis_store")
    fake_store.smembers = lambda key: set(regex_rules or []) if key == "regex_rules" else set()
    fake_code_rules = types.ModuleType("code_rules")
    fake_code_rules.extract_code_detail = lambda _text: {}
    fake_code_rules.extract_trigger_code_detail = lambda _text: {}
    replacements = {
        "redis_store": fake_store,
        "code_rules": fake_code_rules,
    }
    old_modules = {name: sys.modules.get(name) for name in replacements}
    sys.modules.update(replacements)
    sys.path.insert(0, str(APP))
    try:
        spec = importlib.util.spec_from_file_location(
            "matcher_business_match_test",
            APP / "matcher.py",
        )
        module = importlib.util.module_from_spec(spec)
        assert spec is not None and spec.loader is not None
        spec.loader.exec_module(module)
    finally:
        for name, old in old_modules.items():
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old
        try:
            sys.path.remove(str(APP))
        except ValueError:
            pass

    def fake_hook(name, payload=None, default=None):
        if name == "match_plugin_event":
            return plugin_match
        return default

    module.call_hook = fake_hook
    return module


class PluginBusinessMatchV111Tests(unittest.TestCase):
    def test_plugin_business_rules_compile_and_match_expected_types(self):
        samples = {
            "🎁 抽奖活动已开始！\n🎁 奖品\n ▸ 公益服注册码 x1": "lottery",
            "新的抽奖已经创建\n抽奖信息\n抽奖 ID：abc123456": "lottery",
            "已为您生成 Wlao-30-Register_Ab12Cd34Ef": "code",
            "SAKURA-Whitelist_HBLeB9jZ0d": "code",
            "https://ue2.taotu.ink/invite/5fbbddb9": "code",
            "📝 开放注册中": "keyword",
        }
        for text, expected_type in samples.items():
            with self.subTest(text=text):
                result = plugin_match(text)
                self.assertIsInstance(result, dict)
                self.assertTrue(result.get("matched"))
                self.assertEqual(result.get("rule_type"), expected_type)

    def test_generic_invite_field_does_not_auto_trigger(self):
        text = (
            "快来看看你的个人 AI 智能体 Muse 。在加入后的 48 小时内"
            "通过“设置”兑现我的邀请码，我们就能分别获得 10 亿个 Muse 词元。\n\n"
            "邀请码：Q11HYT\n"
            "https://muse.ai/join"
        )

        self.assertIsNone(plugin_match(text))

    def test_code_format_rules_require_safe_code_context(self):
        for text in (
            "登录 CKWIS3PD97M3F6",
            "api CKWIS3PD97M3F6",
            "验证码 CKWIS3PD97M3F6",
            "签到 CKWIS3PD97M3F6",
            "参与码 CKWIS3PD97M3F6",
            "登录 SAKURA-Whitelist_HBLeB9jZ0d",
        ):
            with self.subTest(text=text):
                self.assertIsNone(plugin_match(text))

        self.assertIsInstance(plugin_match("CKWIS3PD97M3F6"), dict)
        self.assertIsInstance(plugin_match("SAKURA-Whitelist_HBLeB9jZ0d"), dict)

    def test_core_uses_plugin_match_and_policy(self):
        matcher = load_matcher(plugin_match={
            "matched": True,
            "rule": "plugin:全局抽奖",
            "rule_type": "lottery",
            "candidate": "🎁 抽奖活动已开始！",
        })

        result = matcher.analyze_message("🎁 抽奖活动已开始！")

        self.assertTrue(result["matched"])
        self.assertEqual(result["rule"], "plugin:全局抽奖")
        self.assertEqual(result["rule_type"], "lottery")
        self.assertEqual(result["rule_policy"]["dedup_strategy"], "lottery_identity")

        details = matcher.match_rule_details("🎁 抽奖活动已开始！")
        self.assertTrue(details["matched"])
        self.assertEqual(details["rule"], "plugin:全局抽奖")
        matched, rule = matcher.match_rules("🎁 抽奖活动已开始！")
        self.assertTrue(matched)
        self.assertEqual(rule, "plugin:全局抽奖")

    def test_pure_mode_does_not_know_lottery_business(self):
        matcher = load_matcher(plugin_match=None, regex_rules=set())

        result = matcher.analyze_message("🎁 抽奖活动已开始！")

        self.assertFalse(result["matched"])
        self.assertEqual(result["code_detail"], {})

    def test_pure_mode_still_forwards_plain_user_regex(self):
        matcher = load_matcher(plugin_match=None, regex_rules={"今天天气不错"})

        result = matcher.analyze_message("今天天气不错")

        self.assertTrue(result["matched"])
        self.assertEqual(result["rule"], "今天天气不错")


if __name__ == "__main__":
    unittest.main()
