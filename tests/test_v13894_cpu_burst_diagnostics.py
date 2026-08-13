import importlib.util
import re
import sys
import time
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
EXPECTED_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8-sig").strip()


def load_redis_store():
    fake_redis = types.ModuleType("redis")

    class FakeRedisClient:
        pass

    fake_redis.Redis = lambda **_kwargs: FakeRedisClient()
    old_redis = sys.modules.get("redis")
    sys.modules["redis"] = fake_redis
    sys.path.insert(0, str(APP))
    try:
        spec = importlib.util.spec_from_file_location("redis_store_v13894", APP / "redis_store.py")
        module = importlib.util.module_from_spec(spec)
        assert spec is not None and spec.loader is not None
        spec.loader.exec_module(module)
        return module
    finally:
        try:
            sys.path.remove(str(APP))
        except ValueError:
            pass
        if old_redis is None:
            sys.modules.pop("redis", None)
        else:
            sys.modules["redis"] = old_redis


class CpuBurstDiagnosticsV13894Tests(unittest.TestCase):
    def test_version_is_current(self):
        self.assertEqual((ROOT / "VERSION").read_text(encoding="utf-8-sig").strip(), EXPECTED_VERSION)
        config = (APP / "config.py").read_text(encoding="utf-8-sig")
        self.assertIn(f'APP_VERSION = "{EXPECTED_VERSION}"', config)

    def test_nested_registration_rule_is_migrated_to_linear_line_match(self):
        store = load_redis_store()

        legacy = store.LEGACY_REGISTRATION_ANNOUNCEMENT_RULE
        safe = store.SAFE_REGISTRATION_ANNOUNCEMENT_RULE

        self.assertEqual(store.KNOWN_REGEX_RULE_MIGRATIONS[legacy], safe)
        self.assertNotRegex(safe, re.compile(r"(?:\.\*|\.\*\?)[^\n]{0,80}[+*]"))
        compiled = re.compile(safe, re.I | re.M)
        self.assertIsNotNone(compiled.search("🫧 管理员 已开启 定时注册\n🎫 总注册限制 | 461"))
        self.assertIsNotNone(compiled.search("🎉 当前已经开放注册，欢迎加入"))
        self.assertIsNone(compiled.search("🫧 注册状态查询\n🎫 当前人数 | 461"))

        started = time.perf_counter()
        self.assertIsNone(compiled.search(("🫧x" * 4096)[:8192]))
        self.assertLess(time.perf_counter() - started, 0.1)

    def test_watchdog_uses_cgroup_window_and_captures_repeated_hot_stacks(self):
        source = (ROOT / "deploy" / "ops" / "slowlink_watchdog.sh").read_text(encoding="utf-8-sig")

        self.assertIn('STACK_SAMPLE_COUNT="${STACK_SAMPLE_COUNT:-3}"', source)
        self.assertIn('STACK_SAMPLE_INTERVAL="${STACK_SAMPLE_INTERVAL:-1}"', source)
        self.assertIn("sample_container_cpu()", source)
        self.assertIn("usage_usec", source)
        self.assertIn('/proc/$pid/cgroup', source)
        self.assertIn('while [ "$sample" -le "$STACK_SAMPLE_COUNT" ]', source)
        self.assertIn("kill -USR1 1", source)
        self.assertIn("thread CPU window", source)
        self.assertNotIn('docker kill --signal=USR1 "$APP_CONTAINER"', source)


if __name__ == "__main__":
    unittest.main()
