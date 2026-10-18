import importlib.util
import re
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
MASKED_CODES = "\n".join(
    (
        "Wlao-30-Register_数字bGOG数字KGE3",
        "Wlao-30-Register_jDNZj数字MTr字母",
        "Wlao-30-Register_IFNUfq字母字母N8",
    )
)


def load_code_rules():
    fake = types.ModuleType("redis_store")
    fake.get_json = lambda key, default=None: default
    fake.set_json = lambda *args, **kwargs: None
    old = sys.modules.get("redis_store")
    sys.modules["redis_store"] = fake
    sys.path.insert(0, str(APP))
    try:
        sys.modules.pop("code_rules", None)
        import code_rules
        return code_rules
    finally:
        sys.path.remove(str(APP))
        if old is None:
            sys.modules.pop("redis_store", None)
        else:
            sys.modules["redis_store"] = old


def load_redis_store(client):
    fake_redis = types.ModuleType("redis")
    fake_redis.Redis = lambda *args, **kwargs: client
    fake_config = types.ModuleType("config")
    fake_config.REDIS_HOST = "redis"
    fake_config.REDIS_PORT = 6379
    fake_config.LISTENER_WORKERS = 2
    old_modules = {name: sys.modules.get(name) for name in ("redis", "config")}
    sys.modules["redis"] = fake_redis
    sys.modules["config"] = fake_config
    try:
        spec = importlib.util.spec_from_file_location("redis_store_v13878", APP / "redis_store.py")
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

    def setnx(self, key, value):
        return True

    def pipeline(self):
        return FakePipeline(self)


class MaskedRegisterCodeV13878Tests(unittest.TestCase):
    def test_masked_register_suffix_is_extracted_without_truncation(self):
        code_rules = load_code_rules()

        detail = code_rules.extract_code_detail(MASKED_CODES)

        self.assertEqual(detail.get("code"), "Wlao-30-Register_数字bGOG数字KGE3")
        self.assertEqual(
            detail.get("identity"),
            "strong_register_renew:Wlao-30-Register_数字bGOG数字KGE3",
        )

    def test_masked_register_lines_are_all_included_in_text_fingerprints(self):
        fake = types.ModuleType("redis_store")
        fake.r = None
        fake.sha = lambda value: "x"
        fake.format_time = lambda: ""
        old = sys.modules.get("redis_store")
        sys.modules["redis_store"] = fake
        sys.path.insert(0, str(APP))
        try:
            sys.modules.pop("dedup", None)
            import dedup
            fingerprints = dedup._register_renew_code_fingerprints(MASKED_CODES)
        finally:
            sys.path.remove(str(APP))
            if old is None:
                sys.modules.pop("redis_store", None)
            else:
                sys.modules["redis_store"] = old

        self.assertEqual(len(fingerprints), 3)
        self.assertEqual(len(set(fingerprints)), 3)

    def test_arbitrary_chinese_guess_hint_is_preserved_in_code_identity(self):
        code_rules = load_code_rules()
        code = "Wlao-30-Register_abc任意KGE3"

        detail = code_rules.extract_code_detail(code)

        self.assertEqual(detail.get("code"), code)
        self.assertEqual(detail.get("identity"), "strong_register_renew:" + code)

    def test_star_masked_register_code_is_fully_extracted(self):
        code_rules = load_code_rules()
        masked = "ChaPanda-30-Register_kLa**guKMq"
        expected = "ChaPanda-30-Register_kLaguKMq"

        detail = code_rules.extract_code_detail(masked)
        identities = code_rules.extract_code_identities(masked)

        self.assertEqual(detail.get("code"), expected)
        self.assertEqual(detail.get("identity"), "strong_register_renew:" + expected)
        self.assertIn(
            "wildcard:ChaPanda-30-Register_kLa**guKMq",
            identities,
        )
        self.assertEqual(
            code_rules.normalize_code_identity(identities[0]),
            "wildcard:chapanda-30-register_kla**gukmq",
        )

    def test_chinese_and_symbol_mask_pattern_is_extracted(self):
        code_rules = load_code_rules()
        masked = "帝服-30-Register_mK@*中x"

        detail = code_rules.extract_code_detail(masked)
        identities = code_rules.extract_code_identities(masked)

        self.assertEqual(detail.get("code"), "帝服-30-Register_mK@中x")
        self.assertIn(
            "wildcard:帝服-30-Register_mK@*中x",
            identities,
        )

    def test_existing_ascii_pure_code_rule_migrates_to_masked_rule(self):
        redis_store = load_redis_store(FakeRedisClient({
            r"^(?!.*码使用)(?:[^\s-]+-)+\d+(?:-[^\s-]+)*-(?:Register|Renew)_[A-Za-z0-9_-]+$",
        }))

        redis_store.ensure_defaults()

        rules = redis_store.r.smembers("regex_rules")
        self.assertIn(redis_store.SAFE_PURE_CODE_TRIGGER_RULE, rules)
        pattern = re.compile(redis_store.SAFE_PURE_CODE_TRIGGER_RULE, re.I | re.M)
        self.assertIsNotNone(pattern.search(MASKED_CODES))

    def test_existing_masked_rule_migrates_to_symbol_rule(self):
        old_rule = (
            r"^(?!.*码使用)(?:[^\s-]+-)+\d+(?:-[^\s-]+)*-"
            r"(?:Register|Renew)_(?:[A-Za-z0-9_-]|数字|字母)+$"
        )
        redis_store = load_redis_store(FakeRedisClient({old_rule}))

        redis_store.ensure_defaults()

        rules = redis_store.r.smembers("regex_rules")
        self.assertNotIn(old_rule, rules)
        self.assertIn(redis_store.SAFE_PURE_CODE_TRIGGER_RULE, rules)
        pattern = re.compile(redis_store.SAFE_PURE_CODE_TRIGGER_RULE, re.I | re.M)
        self.assertIsNotNone(pattern.search("帝服-30-Register_mK@nxdnuwU"))


if __name__ == "__main__":
    unittest.main()
