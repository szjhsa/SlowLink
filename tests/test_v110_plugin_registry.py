import io
import json
import os
import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path
from unittest.mock import call, patch

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
sys.path.insert(0, str(APP))

import plugin_registry


def make_plugin_zip(
    plugin_id: str = "test-pack",
    version: str = "0.1.0",
    min_core_version: str = "1.0",
    rules: dict | None = None,
) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        manifest = {
            "id": plugin_id,
            "name": "测试规则包",
            "version": version,
            "min_core_version": min_core_version,
            "description": "unit test",
            "author": "szjhsa",
        }
        rules = rules or {"matcher": {}, "code_rules": {}, "dedup": {}, "flow": {}}
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

    def test_invalid_active_plugin_id_is_rejected(self):
        os.environ.pop("SLOWLINK_ACTIVE_PLUGIN", None)
        original_redis_value = plugin_registry._redis_value
        plugin_registry._redis_value = lambda key, default: "../outside"
        try:
            self.assertEqual(plugin_registry.active_plugin_id(), "")
            with self.assertRaises(ValueError):
                plugin_registry.plugin_dir("../outside")
        finally:
            plugin_registry._redis_value = original_redis_value

    def test_invalid_zip_is_rejected(self):
        with self.assertRaises(ValueError):
            plugin_registry.install_plugin(b"not a zip")

    def test_invalid_regex_plugin_is_rejected_before_install(self):
        rules = {
            "matcher": {"code_line_pattern": "["},
            "code_rules": {},
            "dedup": {},
            "flow": {},
        }
        with tempfile.TemporaryDirectory() as tmp:
            original_root = plugin_registry.PLUGIN_ROOT
            original_upload_root = plugin_registry.UPLOAD_ROOT
            plugin_registry.PLUGIN_ROOT = Path(tmp)
            plugin_registry.UPLOAD_ROOT = Path(tmp) / "user"
            plugin_registry.invalidate()
            try:
                with self.assertRaises(ValueError):
                    plugin_registry.install_plugin(make_plugin_zip("bad-regex", rules=rules))
                self.assertFalse((plugin_registry.UPLOAD_ROOT / "bad-regex").exists())
            finally:
                plugin_registry.PLUGIN_ROOT = original_root
                plugin_registry.UPLOAD_ROOT = original_upload_root
                plugin_registry.invalidate()

    def test_install_staging_directories_are_unique(self):
        seen = []

        def reject_extract(_zf, target):
            seen.append(Path(target))
            raise ValueError("stop")

        with tempfile.TemporaryDirectory() as tmp:
            original_root = plugin_registry.PLUGIN_ROOT
            original_upload_root = plugin_registry.UPLOAD_ROOT
            plugin_registry.PLUGIN_ROOT = Path(tmp)
            plugin_registry.UPLOAD_ROOT = Path(tmp) / "user"
            plugin_registry.invalidate()
            try:
                with patch.object(plugin_registry, "_safe_extract", side_effect=reject_extract):
                    for _ in range(2):
                        with self.assertRaises(ValueError):
                            plugin_registry.install_plugin(make_plugin_zip("staging-test"))
                self.assertEqual(len(seen), 2)
                self.assertNotEqual(seen[0], seen[1])
                self.assertFalse(any(path.exists() for path in seen))
            finally:
                plugin_registry.PLUGIN_ROOT = original_root
                plugin_registry.UPLOAD_ROOT = original_upload_root
                plugin_registry.invalidate()

    def test_switch_plugin_rolls_back_active_plugin_on_reload_failure(self):
        with patch.object(plugin_registry, "active_plugin_id", return_value="old-plugin"), \
             patch.object(plugin_registry, "activate_plugin") as activate, \
             patch.object(plugin_registry, "reload_all", side_effect=[RuntimeError("bad"), None]):
            with self.assertRaises(RuntimeError):
                plugin_registry.switch_plugin("new-plugin")

        self.assertEqual(activate.call_args_list, [call("new-plugin"), call("old-plugin")])

    def test_builtin_plugin_metadata_is_compatible_with_core(self):
        item = plugin_registry.manifest("builtin")
        self.assertLessEqual(
            plugin_registry._version_tuple(str(item.get("min_core_version") or "")),
            plugin_registry._version_tuple(plugin_registry.APP_VERSION),
        )

    def test_plugin_requires_compatible_core_version(self):
        with self.assertRaises(ValueError):
            plugin_registry.install_plugin(make_plugin_zip(min_core_version="999.0"))

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

    def test_plugin_same_id_update_overwrites_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            original_root = plugin_registry.PLUGIN_ROOT
            original_upload_root = plugin_registry.UPLOAD_ROOT
            plugin_registry.PLUGIN_ROOT = Path(tmp)
            plugin_registry.UPLOAD_ROOT = Path(tmp) / "user"
            plugin_registry.invalidate()
            try:
                item = plugin_registry.install_plugin(make_plugin_zip("builtin"))
                self.assertEqual(item.get("id"), "builtin")
                self.assertTrue((plugin_registry.UPLOAD_ROOT / "builtin" / "plugin.json").exists())
                self.assertTrue(plugin_registry.uninstall_plugin("builtin"))
                self.assertFalse((plugin_registry.UPLOAD_ROOT / "builtin" / "plugin.json").exists())
                plugin_registry.install_plugin(make_plugin_zip("builtin"))
                plugin_registry.install_plugin(make_plugin_zip("builtin"))
                self.assertTrue((plugin_registry.UPLOAD_ROOT / "builtin" / "plugin.json").exists())
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

                self.assertFalse(matcher._guard_flags("成功注册 邀请码：ABC123", "成功注册邀请码:ABC123")[0])
                self.assertEqual(code_rules.DEFAULT_CODE_RULES, [])
                self.assertFalse(code_rules._strong_codes_enabled())
                self.assertEqual(dedup.LOTTERY_KWS, [])

                plugin_registry.PLUGIN_ROOT = original_root
                plugin_registry.UPLOAD_ROOT = original_upload_root
                os.environ["SLOWLINK_ACTIVE_PLUGIN"] = "builtin"
                plugin_registry.invalidate()
                plugin_registry.reload_all()
                self.assertTrue(matcher._guard_flags("成功注册 邀请码：ABC123", "成功注册邀请码:ABC123")[0])
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
