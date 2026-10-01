import importlib.util
import re
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"

REGISTER_CODES = (
    "CineTrail-30-Register_l83UX删除4vlP2",
    "CineTrail-30-Register_9TCg删除Zl7hBH",
    "CineTrail-30-Register_4QwWu0删除fMu5",
    "CineTrail-30-Register_4T42VL删除Sl21",
    "CineTrail-30-Register_mpnz删除jHSLqp",
    "CineTrail-30-Register_i0st删除kpYRkZ",
    "CineTrail-30-Register_RbquT删除Rxz9e",
    "CineTrail-30-Register_AHyKX删除i0hjD",
    "CineTrail-30-Register_rMjYA删除gL6WW",
    "CineTrail-30-Register_9kuSbC删除ewwN",
)

WHITELIST_CODES = (
    "CineTrail-Whitelist_POuZ公益白超级长需要删除kx07Uu",
    "CineTrail-Whitelist_BacC公益白超级长需要删除4hB9pv",
)

CURRENT_REGISTER_RULE = (
    r"^(?!.*码使用)(?:[^\s-]+-)+\d+(?:-[^\s-]+)*-"
    r"(?:Register|Renew)_(?:[^\s*`\u3400-\u9fff]|数字|字母)+$"
)

CURRENT_WHITELIST_RULE = (
    r"(?:^|(?<=[\s:：，,]))[^\s*`\-:：，,]+(?:-[^\s*`\-:：，,]+)*-Whitelist_"
    r"(?a:[A-Za-z0-9]{10})"
    r"(?=$|\s|[，。！？？；：、）】]|[,.;:)\]}>`~*](?![A-Za-z0-9_-]))"
)


def load_modules(regex_rules=()):
    fake_store = types.ModuleType("redis_store")
    fake_store.get_json = lambda _key, default=None: default
    fake_store.set_json = lambda *_args, **_kwargs: None
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

        return code_rules, matcher
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


class FakePipeline:
    def __init__(self, client):
        self.client = client
        self.operations = []

    def srem(self, key, value):
        self.operations.append(("srem", key, value))
        return self

    def sadd(self, key, value):
        self.operations.append(("sadd", key, value))
        return self

    def execute(self):
        return [getattr(self.client, operation)(key, value) for operation, key, value in self.operations]


class FakeRedisClient:
    def __init__(self, regex_rules):
        self.sets = {"regex_rules": set(regex_rules)}

    def setnx(self, _key, _value):
        return True

    def sismember(self, key, value):
        return value in self.sets.get(key, set())

    def smembers(self, key):
        return set(self.sets.get(key, set()))

    def srem(self, key, value):
        existed = value in self.sets.setdefault(key, set())
        self.sets[key].discard(value)
        return int(existed)

    def sadd(self, key, value):
        before = len(self.sets.setdefault(key, set()))
        self.sets[key].add(value)
        return int(len(self.sets[key]) != before)

    def pipeline(self):
        return FakePipeline(self)


def load_redis_store(client):
    fake_redis = types.ModuleType("redis")
    fake_redis.Redis = lambda *_args, **_kwargs: client
    fake_config = types.ModuleType("config")
    fake_config.REDIS_HOST = "redis"
    fake_config.REDIS_PORT = 6379
    fake_config.LISTENER_WORKERS = 2
    old_modules = {name: sys.modules.get(name) for name in ("redis", "config")}
    sys.modules["redis"] = fake_redis
    sys.modules["config"] = fake_config
    try:
        spec = importlib.util.spec_from_file_location("redis_store_v139", APP / "redis_store.py")
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


class ChineseGuessCodeV139Tests(unittest.TestCase):
    def test_register_guess_codes_are_extracted_and_match_the_system_rule(self):
        code_rules, matcher = load_modules(regex_rules={CURRENT_REGISTER_RULE})

        for code in REGISTER_CODES:
            with self.subTest(code=code):
                detail = code_rules.extract_code_detail(code)
                result = matcher.match_rule_details(code)

                self.assertEqual(detail.get("code"), code)
                self.assertEqual(detail.get("identity"), "strong_register_renew:" + code)
                self.assertTrue(result.get("matched"))

    def test_whitelist_guess_codes_are_extracted_and_plugin_triggers(self):
        code_rules, matcher_without_rule = load_modules()

        for code in WHITELIST_CODES:
            with self.subTest(code=code):
                detail = code_rules.extract_code_detail(code)
                result = matcher_without_rule.analyze_message(code)

                self.assertEqual(detail.get("code"), code)
                self.assertEqual(detail.get("identity"), "strong_whitelist:" + code)
                self.assertTrue(result.get("matched"))
                self.assertTrue(str(result.get("rule") or "").startswith("plugin:"))

    def test_known_system_rules_migrate_to_guess_code_rules(self):
        client = FakeRedisClient({CURRENT_REGISTER_RULE, CURRENT_WHITELIST_RULE})
        redis_store = load_redis_store(client)

        redis_store.ensure_defaults()

        self.assertTrue(hasattr(redis_store, "SAFE_WHITELIST_TRIGGER_RULE"))
        rules = client.smembers("regex_rules")
        self.assertNotIn(CURRENT_REGISTER_RULE, rules)
        self.assertNotIn(CURRENT_WHITELIST_RULE, rules)
        self.assertIn(redis_store.SAFE_PURE_CODE_TRIGGER_RULE, rules)
        self.assertIn(redis_store.SAFE_WHITELIST_TRIGGER_RULE, rules)
        compiled = re.compile(redis_store.SAFE_WHITELIST_TRIGGER_RULE, re.I | re.M)
        for code in WHITELIST_CODES:
            with self.subTest(code=code):
                self.assertIsNotNone(compiled.search(code))

    def test_chinese_guess_codes_have_distinct_dedup_fingerprints(self):
        dedup = load_dedup()

        fingerprints = dedup._register_renew_code_fingerprints("\n".join(REGISTER_CODES))

        self.assertEqual(len(fingerprints), len(REGISTER_CODES))
        self.assertEqual(len(set(fingerprints)), len(REGISTER_CODES))

    def test_usage_notice_with_a_chinese_guess_code_remains_blocked(self):
        _code_rules, matcher = load_modules(regex_rules={CURRENT_REGISTER_RULE})
        text = "注册码使用 - 已使用 " + REGISTER_CODES[0]

        result = matcher.analyze_message(text)

        self.assertFalse(result.get("matched"))
        self.assertTrue(result.get("usage_notice"))


if __name__ == "__main__":
    unittest.main()
