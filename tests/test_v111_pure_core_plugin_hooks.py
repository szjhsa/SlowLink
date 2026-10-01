import io
import json
import os
import subprocess
import sys
import textwrap
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
sys.path.insert(0, str(APP))

import plugin_registry
import plugin_runtime
import rule_generator
import rule_policy


def make_plugin_zip(
    plugin_id: str,
    hooks_source: str = "",
    extra_files: dict[str, str] | None = None,
) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            f"plugins/{plugin_id}/plugin.json",
            json.dumps(
                {
                    "id": plugin_id,
                    "name": "Hook Test",
                    "version": "1.1",
                    "min_core_version": "1.1",
                }
            ),
        )
        archive.writestr(
            f"plugins/{plugin_id}/rules.json",
            json.dumps({"matcher": {}, "code_rules": {}, "dedup": {}, "flow": {}}),
        )
        if hooks_source:
            archive.writestr(f"plugins/{plugin_id}/hooks.py", hooks_source)
        for name, source in (extra_files or {}).items():
            archive.writestr(f"plugins/{plugin_id}/{name}", source)
    return buffer.getvalue()


class PureCorePluginHooksTests(unittest.TestCase):
    def setUp(self):
        self.old_plugin = os.environ.get("SLOWLINK_ACTIVE_PLUGIN")
        os.environ["SLOWLINK_ACTIVE_PLUGIN"] = "builtin"
        plugin_registry.invalidate()
        plugin_runtime.invalidate()

    def tearDown(self):
        if self.old_plugin is None:
            os.environ.pop("SLOWLINK_ACTIVE_PLUGIN", None)
        else:
            os.environ["SLOWLINK_ACTIVE_PLUGIN"] = self.old_plugin
        plugin_registry.invalidate()
        plugin_runtime.invalidate()

    def test_rule_generator_prefers_plugin_hook(self):
        sample = "新的抽奖已经创建\n抽奖 ID：1ea59728-11f6-4748-9a32-b341129f2f3d"
        with patch.object(
            rule_generator,
            "call_hook",
            return_value={"pattern": r"HOOKED-[A-Z0-9]+", "reason": "hook"},
        ) as hook:
            result = rule_generator.generate_rule("lottery", sample)

        self.assertEqual(result["pattern"], r"HOOKED-[A-Z0-9]+")
        self.assertEqual(result["reason"], "hook")
        hook.assert_called_once()

    def test_rule_policy_defaults_are_plugin_driven(self):
        config = {
            "id": "custom_notice",
            "label": "自定义公告",
            "strategy": "line",
            "dedup_strategy": "custom_identity",
            "ttl_minutes": 135,
            "lottery_template_mode": "off",
            "forward": False,
            "code_dedup": False,
        }
        with patch.object(rule_policy, "get_rule_type_config", return_value=config), \
             patch.object(rule_policy, "_plugin_defaults", return_value={}):
            policy = rule_policy.default_policy("custom_notice", require_available=False)

        self.assertEqual(policy["dedup_strategy"], "custom_identity")
        self.assertEqual(policy["ttl_minutes"], 135)
        self.assertFalse(policy["forward"])

    def test_rule_policy_source_has_no_embedded_business_defaults(self):
        source = (APP / "rule_policy.py").read_text(encoding="utf-8-sig")

        self.assertNotIn("code_identity", source)
        self.assertNotIn("lottery_identity", source)

    def test_pure_mode_page_does_not_require_a_plugin_manifest(self):
        source = (APP / "web.py").read_text(encoding="utf-8-sig")

        self.assertIn(
            '"plugin_manifest": plugin_manifest(active_plugin) if active_plugin else {}',
            source,
        )
        self.assertEqual(plugin_registry.manifest(""), {})

    def test_pure_mode_disables_plugin_business_behavior(self):
        child = textwrap.dedent(
            f"""
            import json
            import sys
            import types

            fake = types.ModuleType("redis_store")
            fake.get_json = lambda key, default=None: default
            fake.set_json = lambda *args, **kwargs: None
            fake.smembers = lambda *args, **kwargs: set()
            fake.sha = lambda value: str(abs(hash(value)))
            fake.format_time = lambda *args, **kwargs: "2026-01-01 00:00:00"
            fake.log_line = lambda *args, **kwargs: None
            fake.r = types.SimpleNamespace()
            sys.modules["redis_store"] = fake
            sys.path.insert(0, {json.dumps(str(APP))})

            import code_rules
            import dedup
            import matcher
            import rule_generator

            print(json.dumps({{
                "detail": code_rules.extract_code_detail("Wlao-30-Register_jDNZjMTrA"),
                "types": [item["id"] for item in rule_generator.available_rule_types()],
                "strategy": dedup.build_profile("普通文本").get("dedup_strategy"),
                "guard": matcher.analyze_message("注册状态：false").get("closed_register_notice"),
            }}, ensure_ascii=False))
            """
        )
        env = dict(os.environ)
        env["SLOWLINK_PURE_MODE"] = "1"
        env["SLOWLINK_ACTIVE_PLUGIN"] = "off"
        proc = subprocess.run(
            [sys.executable, "-c", child],
            text=True,
            capture_output=True,
            timeout=30,
            env=env,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertEqual(result["detail"], {})
        self.assertEqual(result["types"], ["keyword"])
        self.assertEqual(result["strategy"], "normalized_text")
        self.assertFalse(result["guard"])

    def test_hook_runtime_loads_plugin_file_and_isolates_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin_id = "hook-test"
            upload_root = root / "user"
            target = upload_root / plugin_id
            target.mkdir(parents=True)
            (target / "plugin.json").write_text(
                json.dumps(
                    {
                        "id": plugin_id,
                        "name": "Hook Test",
                        "version": "1.1",
                        "min_core_version": "1.1",
                    }
                ),
                encoding="utf-8",
            )
            (target / "rules.json").write_text(
                json.dumps({"matcher": {}, "code_rules": {}, "dedup": {}, "flow": {}}),
                encoding="utf-8",
            )
            (target / "hooks.py").write_text(
                "def echo(payload):\n"
                "    return payload.get('value')\n\n"
                "def broken(payload):\n"
                "    raise RuntimeError('boom')\n",
                encoding="utf-8",
            )

            old_root = plugin_registry.PLUGIN_ROOT
            old_upload = plugin_registry.UPLOAD_ROOT
            plugin_registry.PLUGIN_ROOT = root
            plugin_registry.UPLOAD_ROOT = upload_root
            os.environ["SLOWLINK_ACTIVE_PLUGIN"] = plugin_id
            plugin_registry.invalidate()
            plugin_runtime.invalidate()
            try:
                self.assertEqual(plugin_runtime.call_hook("echo", {"value": 7}), 7)
                self.assertEqual(
                    plugin_runtime.call_hook("broken", {}, default="fallback"),
                    "fallback",
                )
            finally:
                plugin_registry.PLUGIN_ROOT = old_root
                plugin_registry.UPLOAD_ROOT = old_upload
                os.environ["SLOWLINK_ACTIVE_PLUGIN"] = "builtin"
                plugin_registry.invalidate()
                plugin_runtime.invalidate()

    def test_plugin_install_rejects_invalid_hooks_syntax(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_root = plugin_registry.PLUGIN_ROOT
            old_upload = plugin_registry.UPLOAD_ROOT
            plugin_registry.PLUGIN_ROOT = root
            plugin_registry.UPLOAD_ROOT = root / "user"
            plugin_registry.invalidate()
            try:
                with self.assertRaisesRegex(ValueError, "hooks.py"):
                    plugin_registry.install_plugin(
                        make_plugin_zip("broken-hooks", "def broken(:\n    pass\n")
                    )
            finally:
                plugin_registry.PLUGIN_ROOT = old_root
                plugin_registry.UPLOAD_ROOT = old_upload
                plugin_registry.invalidate()

    def test_plugin_install_rejects_invalid_sibling_module_syntax(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_root = plugin_registry.PLUGIN_ROOT
            old_upload = plugin_registry.UPLOAD_ROOT
            plugin_registry.PLUGIN_ROOT = root
            plugin_registry.UPLOAD_ROOT = root / "user"
            plugin_registry.invalidate()
            try:
                with self.assertRaisesRegex(ValueError, "code_rules_impl"):
                    plugin_registry.install_plugin(
                        make_plugin_zip(
                            "broken-impl",
                            extra_files={"code_rules_impl.py": "def broken(:\n    pass\n"},
                        )
                    )
            finally:
                plugin_registry.PLUGIN_ROOT = old_root
                plugin_registry.UPLOAD_ROOT = old_upload
                plugin_registry.invalidate()


if __name__ == "__main__":
    unittest.main()
