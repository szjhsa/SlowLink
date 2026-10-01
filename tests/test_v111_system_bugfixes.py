import importlib.util
import io
import json
import sys
import tempfile
import threading
import types
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


class _SetPipeline:
    def __init__(self, client):
        self.client = client
        self.ops = []

    def srem(self, key, value):
        self.ops.append(("srem", key, value))
        return self

    def sadd(self, key, value):
        self.ops.append(("sadd", key, value))
        return self

    def execute(self):
        for op, key, value in self.ops:
            target = self.client.sets.setdefault(key, set())
            if op == "srem":
                target.discard(value)
            else:
                target.add(value)
        count = len(self.ops)
        self.ops = []
        return [1] * count


class _SetRedis:
    def __init__(self):
        self.sets = {}

    def get(self, key, default=None):
        return default

    def sismember(self, key, value):
        return value in self.sets.get(key, set())

    def pipeline(self):
        return _SetPipeline(self)


class _BlockingPipeline:
    def __init__(self, client, block_event=None):
        self.client = client
        self.block_event = block_event
        self.ops = []

    def lpush(self, key, raw):
        self.ops.append(("lpush", key, raw))
        return self

    def ltrim(self, key, start, end):
        self.ops.append(("ltrim", key, start, end))
        return self

    def hincrby(self, key, field, amount=1):
        self.ops.append(("hincrby", key, field, amount))
        return self

    def execute(self):
        if self.block_event is not None:
            self.client.execute_started.set()
            self.block_event.wait(timeout=5)
        self.client.executed.extend(self.ops)
        count = len(self.ops)
        self.ops = []
        return [1] * count


class _BlockingRedis:
    def __init__(self):
        self.executed = []
        self.pipelines = []
        self.execute_started = threading.Event()
        self.release = threading.Event()
        self.block_next = True

    def get(self, key, default=None):
        return default

    def pipeline(self):
        block_event = self.release if self.block_next else None
        self.block_next = False
        pipeline = _BlockingPipeline(self, block_event)
        self.pipelines.append(pipeline)
        return pipeline


def load_redis_store(fake_client):
    old_redis_module = sys.modules.get("redis")
    fake_redis_module = types.ModuleType("redis")
    fake_redis_module.Redis = lambda *args, **kwargs: fake_client
    sys.modules["redis"] = fake_redis_module
    sys.path.insert(0, str(APP))
    try:
        sys.modules.pop("redis_store", None)
        spec = importlib.util.spec_from_file_location(
            "redis_store_bugfix_test",
            APP / "redis_store.py",
        )
        module = importlib.util.module_from_spec(spec)
        assert spec is not None and spec.loader is not None
        spec.loader.exec_module(module)
        module.r = fake_client
        return module
    finally:
        sys.modules.pop("redis_store", None)
        if old_redis_module is None:
            sys.modules.pop("redis", None)
        else:
            sys.modules["redis"] = old_redis_module
        try:
            sys.path.remove(str(APP))
        except ValueError:
            pass


class SystemBugfixesV111Tests(unittest.TestCase):
    def test_regex_migration_preserves_disabled_state(self):
        client = _SetRedis()
        store = load_redis_store(client)
        store.KNOWN_REGEX_RULE_MIGRATIONS = {"old-rule": "new-rule"}

        client.sets["regex_rules"] = {"old-rule"}
        client.sets["regex_rules_disabled"] = {"old-rule"}
        self.assertEqual(store.migrate_known_regex_rules(), 1)
        self.assertNotIn("old-rule", client.sets["regex_rules"])
        self.assertNotIn("old-rule", client.sets["regex_rules_disabled"])
        self.assertIn("new-rule", client.sets["regex_rules"])
        self.assertIn("new-rule", client.sets["regex_rules_disabled"])

        client.sets = {
            "regex_rules": set(),
            "regex_rules_disabled": {"old-rule"},
        }
        self.assertEqual(store.migrate_known_regex_rules(), 1)
        self.assertNotIn("new-rule", client.sets["regex_rules"])
        self.assertIn("new-rule", client.sets["regex_rules_disabled"])

    def test_flush_wait_writes_records_queued_during_inflight_flush(self):
        client = _BlockingRedis()
        store = load_redis_store(client)
        store._BATCH_THREAD_STARTED = True
        store._BATCH_BUFFER = {
            "events": [],
            "hits": [],
            "fails": [],
            "perf_events": [],
            "daily": [],
            "counters": [],
        }
        store._enqueue_record("events", '{"seq":1}', 300)

        first = threading.Thread(target=store.flush_batch_records, daemon=True)
        first.start()
        self.assertTrue(client.execute_started.wait(timeout=3))

        store._enqueue_record("events", '{"seq":2}', 300)
        second = threading.Thread(
            target=lambda: store.flush_batch_records(wait=True),
            daemon=True,
        )
        second.start()
        client.release.set()
        first.join(timeout=3)
        second.join(timeout=3)

        pushed = [
            op[2]
            for op in client.executed
            if op[0] == "lpush" and op[1] == "events"
        ]
        self.assertIn('{"seq":1}', pushed)
        self.assertIn('{"seq":2}', pushed)

    def test_unavailable_policy_type_can_be_preserved(self):
        sys.path.insert(0, str(APP))
        try:
            import rule_policy
            import rule_types

            rule_types.clear_cache()
            policy = rule_policy.default_policy(
                "missing_type",
                require_available=False,
            )
            self.assertEqual(policy["rule_type"], "missing_type")
            self.assertEqual(policy["dedup_strategy"], "normalized_text")
            self.assertTrue(policy["forward"])

            normalized = rule_policy.normalize_policy({
                "rule_type": "missing_type",
                "label": "旧插件类型",
                "dedup_strategy": "custom_identity",
                "ttl_minutes": 5,
                "forward": False,
            })
            self.assertEqual(normalized["label"], "旧插件类型")
            self.assertEqual(normalized["dedup_strategy"], "custom_identity")
            self.assertEqual(normalized["ttl_minutes"], 5)
            self.assertFalse(normalized["forward"])
            self.assertEqual(rule_policy.normalize_policy({"rule_type": ""}), {})
        finally:
            try:
                sys.path.remove(str(APP))
            except ValueError:
                pass

    def test_dedup_profile_falls_back_when_plugin_profile_is_incomplete(self):
        from tests.test_v13883_cross_template_lottery_dedup import load_dedup

        dedup, _client = load_dedup()

        def broken_hook(name, payload=None, default=None):
            if name == "build_dedup_profile":
                return {"activity": "broken"}
            return default

        with patch.object(dedup, "call_hook", side_effect=broken_hook):
            broken = dedup.build_profile("普通文本")
        self.assertTrue(broken["dedup_id"].startswith("text:"))
        self.assertEqual(broken["activity"], "other")

        def minimal_hook(name, payload=None, default=None):
            if name == "build_dedup_profile":
                return {"dedup_id": "plugin:minimal"}
            return default

        with patch.object(dedup, "call_hook", side_effect=minimal_hook):
            minimal = dedup.build_profile("普通文本")
        self.assertEqual(minimal["dedup_id"], "plugin:minimal")
        self.assertEqual(minimal["activity"], "other")
        self.assertEqual(minimal["correlation_keys"], [])
        self.assertEqual(minimal["correlation_mode"], "off")
        self.assertEqual(minimal["reason_labels"], {})

    def test_plugin_registry_failure_paths_are_reported(self):
        import plugin_registry

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            upload_root = root / "user"
            target = upload_root / "demo-plugin"
            target.mkdir(parents=True)
            (target / "plugin.json").write_text("{}", encoding="utf-8")
            with patch.object(plugin_registry, "UPLOAD_ROOT", upload_root), \
                 patch.object(plugin_registry, "plugin_dir", return_value=target), \
                 patch.object(plugin_registry.shutil, "rmtree", side_effect=OSError("busy")):
                self.assertFalse(plugin_registry.uninstall_plugin("demo-plugin"))

        fake_store = types.ModuleType("redis_store")
        fake_store.set_value = Mock(side_effect=RuntimeError("redis down"))
        old_store = sys.modules.get("redis_store")
        sys.modules["redis_store"] = fake_store
        try:
            with patch.object(plugin_registry, "validate_plugin", return_value={}):
                with self.assertRaises(RuntimeError):
                    plugin_registry.activate_plugin("builtin")
        finally:
            if old_store is None:
                sys.modules.pop("redis_store", None)
            else:
                sys.modules["redis_store"] = old_store

    def test_list_plugins_skips_staging_directories(self):
        import plugin_registry

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("good-plugin", "_staging_123"):
                plugin_dir = root / name
                plugin_dir.mkdir(parents=True)
                (plugin_dir / "plugin.json").write_text(
                    json.dumps({"id": name, "name": name, "version": "1"}),
                    encoding="utf-8",
                )
            with patch.object(plugin_registry, "PLUGIN_ROOT", root), \
                 patch.object(plugin_registry, "UPLOAD_ROOT", root / "user"), \
                 patch.object(plugin_registry, "active_plugin_id", return_value=""):
                ids = {item["id"] for item in plugin_registry.list_plugins()}
            self.assertIn("good-plugin", ids)
            self.assertNotIn("_staging_123", ids)

    def test_plugin_install_ignores_bytecode_entries(self):
        import plugin_registry

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "plugins/demo/plugin.json",
                json.dumps({
                    "id": "demo",
                    "name": "Demo",
                    "version": "1.0",
                    "min_core_version": "1.0",
                }),
            )
            archive.writestr(
                "plugins/demo/rules.json",
                json.dumps({
                    "matcher": {},
                    "code_rules": {},
                    "dedup": {},
                    "flow": {},
                }),
            )
            archive.writestr("plugins/demo/hooks.py", "def echo(payload):\n    return payload\n")
            archive.writestr("plugins/demo/__pycache__/hooks.cpython-311.pyc", b"stale")
            archive.writestr("plugins/demo/legacy.pyc", b"stale")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(plugin_registry, "PLUGIN_ROOT", root / "builtin"), \
                 patch.object(plugin_registry, "UPLOAD_ROOT", root / "user"):
                manifest = plugin_registry.install_plugin(buffer.getvalue())
            self.assertEqual(manifest["id"], "demo")
            installed = root / "user" / "demo"
            self.assertTrue((installed / "hooks.py").is_file())
            self.assertFalse((installed / "__pycache__").exists())
            self.assertFalse((installed / "legacy.pyc").exists())

    def test_import_and_add_rule_regressions_are_wired(self):
        source = read(APP / "web.py")

        self.assertIn('"enabled": "dedup_enabled"', source)
        self.assertIn('"mode": "dedup_mode"', source)
        self.assertIn(
            'strict_context = request.form.get("strict_context") == "1"',
            source,
        )
        self.assertIn("hmac.compare_digest", source)
        self.assertIn("if not isinstance(d, dict):", source)
        self.assertIn(
            'for source_key in ("regex_rules", "disabled_regex_rules"):',
            source,
        )
        self.assertIn(
            'for source_key in ("code_rules", "code_rules_plugin", "code_rules_pure"):',
            source,
        )
        self.assertIn("正则规则过长", source)
        self.assertIn("码识别规则过长", source)
        self.assertIn("from chat_ids import dialog_id_variants", source)
        self.assertIn("variants |= dialog_id_variants(did)", source)


if __name__ == "__main__":
    unittest.main()
