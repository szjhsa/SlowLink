import io
import json
import os
import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
sys.path.insert(0, str(APP))

import plugin_registry


def make_plugin_zip(plugin_id: str = "test-pack", version: str = "0.1.0") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        manifest = {
            "id": plugin_id,
            "name": "测试规则包",
            "version": version,
            "min_core_version": "1.1",
            "description": "unit test",
            "author": "szjhsa",
        }
        rules = {"matcher": {}, "code_rules": {}, "dedup": {}, "flow": {}}
        zf.writestr(f"plugins/{plugin_id}/plugin.json", json.dumps(manifest, ensure_ascii=False))
        zf.writestr(f"plugins/{plugin_id}/rules.json", json.dumps(rules, ensure_ascii=False))
    return buf.getvalue()


class PluginRegistryV110Tests(unittest.TestCase):
    def setUp(self):
        os.environ["SLOWLINK_ACTIVE_PLUGIN"] = "builtin"

    def tearDown(self):
        os.environ.pop("SLOWLINK_ACTIVE_PLUGIN", None)
        plugin_registry.invalidate()

    def test_builtin_plugin_is_present_and_active(self):
        self.assertEqual(plugin_registry.active_plugin_id(), "builtin")
        item = plugin_registry.manifest("builtin")
        self.assertEqual(item.get("id"), "builtin")
        self.assertTrue(plugin_registry.rules("builtin").get("matcher"))
        self.assertTrue(plugin_registry.rules("builtin").get("code_rules"))
        self.assertTrue(plugin_registry.rules("builtin").get("dedup"))
        self.assertTrue(plugin_registry.rules("builtin").get("flow"))

    def test_builtin_sections_expose_domain_data(self):
        flow = plugin_registry.builtin_section("flow")
        self.assertIn("whitelist", flow.get("priority_keywords") or [])
        dedup = plugin_registry.builtin_section("dedup")
        self.assertIn("刮刮乐", dedup.get("lottery_keywords") or [])

    def test_off_means_no_active_plugin(self):
        os.environ.pop("SLOWLINK_ACTIVE_PLUGIN", None)
        original_redis_value = plugin_registry._redis_value
        plugin_registry._redis_value = lambda key, default: "off"
        try:
            self.assertEqual(plugin_registry.active_plugin_id(), "")
        finally:
            plugin_registry._redis_value = original_redis_value

    def test_invalid_zip_is_rejected(self):
        with self.assertRaises(ValueError):
            plugin_registry.install_plugin(b"not a zip")

    def test_valid_plugin_installs_into_plugin_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            original_root = plugin_registry.PLUGIN_ROOT
            original_upload_root = plugin_registry.UPLOAD_ROOT
            plugin_registry.PLUGIN_ROOT = Path(tmp)
            plugin_registry.UPLOAD_ROOT = Path(tmp) / "user"
            plugin_registry.invalidate()
            try:
                item = plugin_registry.install_plugin(make_plugin_zip())
                self.assertEqual(item.get("id"), "test-pack")
                self.assertTrue((plugin_registry.UPLOAD_ROOT / "test-pack" / "rules.json").exists())
                plugins = plugin_registry.list_plugins()
                self.assertTrue(any(p.get("id") == "test-pack" for p in plugins))
            finally:
                plugin_registry.PLUGIN_ROOT = original_root
                plugin_registry.UPLOAD_ROOT = original_upload_root
                plugin_registry.invalidate()

    def test_builtin_can_be_restored_when_missing_but_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            original_root = plugin_registry.PLUGIN_ROOT
            original_upload_root = plugin_registry.UPLOAD_ROOT
            plugin_registry.PLUGIN_ROOT = Path(tmp)
            plugin_registry.UPLOAD_ROOT = Path(tmp) / "user"
            plugin_registry.invalidate()
            try:
                item = plugin_registry.install_plugin(make_plugin_zip("builtin"))
                self.assertEqual(item.get("id"), "builtin")
                self.assertTrue((plugin_registry.PLUGIN_ROOT / "builtin" / "plugin.json").exists())
                with self.assertRaises(ValueError):
                    plugin_registry.install_plugin(make_plugin_zip("builtin"))
            finally:
                plugin_registry.PLUGIN_ROOT = original_root
                plugin_registry.UPLOAD_ROOT = original_upload_root
                plugin_registry.invalidate()

    def test_empty_plugin_clears_builtin_defaults(self):
        fake = types.ModuleType("redis_store")
        fake.smembers = lambda key: set()
        fake.get = lambda key, default=None: default
        fake.set_value = lambda *a, **k: None
        fake.get_json = lambda key, default=None: default
        fake.set_json = lambda *a, **k: None
        fake.r = None
        fake.sha = lambda text: "x"
        fake.format_time = lambda *a, **k: "2026-01-01 00:00:00"
        sys.modules["redis_store"] = fake
        for name in ("matcher", "code_rules", "dedup"):
            sys.modules.pop(name, None)

        with tempfile.TemporaryDirectory() as tmp:
            original_root = plugin_registry.PLUGIN_ROOT
            original_upload_root = plugin_registry.UPLOAD_ROOT
            plugin_registry.PLUGIN_ROOT = Path(tmp)
            plugin_registry.UPLOAD_ROOT = Path(tmp) / "user"
            plugin_registry.invalidate()
            try:
                plugin_registry.install_plugin(make_plugin_zip("empty-pack"))
                os.environ["SLOWLINK_ACTIVE_PLUGIN"] = "empty-pack"
                plugin_registry.invalidate()
                plugin_registry.reload_all()

                import matcher
                import code_rules
                import dedup

                self.assertEqual(matcher.USAGE_HARD_WORDS, [])
                self.assertEqual(matcher.CODE_LINE_RE.pattern, "(?!)")
                self.assertEqual(code_rules.DEFAULT_CODE_RULES, [])
                self.assertFalse(code_rules._strong_codes_enabled())
                self.assertEqual(dedup.LOTTERY_KWS, [])

                plugin_registry.PLUGIN_ROOT = original_root
                plugin_registry.UPLOAD_ROOT = original_upload_root
                os.environ["SLOWLINK_ACTIVE_PLUGIN"] = "builtin"
                plugin_registry.invalidate()
                plugin_registry.reload_all()
                self.assertIn("成功注册", matcher.USAGE_HARD_WORDS)
                self.assertNotEqual(code_rules.DEFAULT_CODE_RULES, [])
                self.assertTrue(code_rules._strong_codes_enabled())
                self.assertIn("抽奖", dedup.LOTTERY_KWS)
            finally:
                plugin_registry.PLUGIN_ROOT = original_root
                plugin_registry.UPLOAD_ROOT = original_upload_root
                plugin_registry.invalidate()
                for name in ("matcher", "code_rules", "dedup", "redis_store"):
                    sys.modules.pop(name, None)


if __name__ == "__main__":
    unittest.main()
