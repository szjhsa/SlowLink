import json
import os
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

from tests.test_v13883_cross_template_lottery_dedup import load_dedup


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"


class RulePluginHardeningV120Tests(unittest.TestCase):
    def test_unavailable_plugin_type_policy_is_not_active(self):
        old = os.environ.get("SLOWLINK_ACTIVE_PLUGIN")
        os.environ["SLOWLINK_ACTIVE_PLUGIN"] = "off"
        sys.path.insert(0, str(APP))
        try:
            import rule_types

            rule_types.clear_cache()
            self.assertFalse(rule_types.is_type_available("code"))
            self.assertTrue(rule_types.is_type_available("keyword"))
        finally:
            try:
                sys.path.remove(str(APP))
            except ValueError:
                pass
            if old is None:
                os.environ.pop("SLOWLINK_ACTIVE_PLUGIN", None)
            else:
                os.environ["SLOWLINK_ACTIVE_PLUGIN"] = old

    def test_generated_code_rule_also_registers_code_recognizer(self):
        source = (APP / "web.py").read_text(encoding="utf-8-sig")

        self.assertIn('add_code_rule("生成码规则", pattern', source)

    def test_plugin_generator_schema_rejects_unknown_strategy(self):
        sys.path.insert(0, str(APP))
        try:
            import plugin_registry

            with self.assertRaises(ValueError):
                plugin_registry.validate_rules_data({
                    "matcher": {},
                    "code_rules": {},
                    "dedup": {},
                    "rule_types": {},
                    "flow": {},
                    "rule_generator": {
                        "types": {
                            "broken": {
                                "label": "错误类型",
                                "strategy": "unknown",
                            }
                        }
                    },
                })
        finally:
            try:
                sys.path.remove(str(APP))
            except ValueError:
                pass

    def test_editor_preserves_unavailable_policy_type(self):
        template = (APP / "templates" / "index.html").read_text(encoding="utf-8-sig")

        self.assertIn("当前插件未提供", template)
        self.assertIn("currentTypeKnown", template)

    def test_code_policy_without_identity_uses_explicit_text_fallback(self):
        sys.path.insert(0, str(APP))
        try:
            dedup, _client = load_dedup()
        finally:
            try:
                sys.path.remove(str(APP))
            except ValueError:
                pass
        policy = {
            "rule_type": "code",
            "label": "码子",
            "dedup_strategy": "code_identity",
            "ttl_minutes": 60,
            "lottery_template_mode": "off",
            "forward": True,
        }

        profile = dedup.build_profile("没有完整码的消息", policy=policy)

        self.assertEqual(profile["dedup_strategy"], "code_fallback_text")
        self.assertTrue(profile["identity_fallback"])
        self.assertTrue(profile["dedup_id"].startswith("code-fallback:"))

    def test_normalize_for_text_dedup_has_bounded_regex_runtime(self):
        child = textwrap.dedent(
            f"""
            import sys
            import time
            import types

            fake_redis = types.ModuleType("redis_store")
            fake_redis.r = types.SimpleNamespace(
                get=lambda *args, **kwargs: None,
                set=lambda *args, **kwargs: None,
                setex=lambda *args, **kwargs: None,
                lpush=lambda *args, **kwargs: None,
                ltrim=lambda *args, **kwargs: None,
                delete=lambda *args, **kwargs: None,
                pipeline=lambda: None,
                sismember=lambda *args, **kwargs: False,
            )
            fake_redis.sha = lambda value: str(abs(hash(value)))
            fake_redis.format_time = lambda *args, **kwargs: "2026-01-01 00:00:00"
            sys.modules["redis_store"] = fake_redis
            sys.path.insert(0, {json.dumps(str(APP))})

            import dedup

            text = ("ABCD-" * 40)[:200]
            started = time.perf_counter()
            dedup.normalize_for_text_dedup(text)
            print(json.dumps({{"elapsed_ms": (time.perf_counter() - started) * 1000}}))
            """  # noqa: E501
        ).replace("import sys\n", "import json\nimport sys\n", 1)
        try:
            proc = subprocess.run(
                [sys.executable, "-c", child],
                text=True,
                capture_output=True,
                timeout=2.0,
            )
        except subprocess.TimeoutExpired:
            self.fail("normalize_for_text_dedup hung on 200-character hyphen text")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertLess(result["elapsed_ms"], 500)


if __name__ == "__main__":
    unittest.main()
