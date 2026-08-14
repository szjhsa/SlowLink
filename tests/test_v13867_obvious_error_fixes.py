import re
import shutil
import subprocess
import tempfile
import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
EXPECTED_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8-sig").strip()


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


class ObviousErrorFixesV13867Tests(unittest.TestCase):
    def test_versions_are_bumped_to_v13867(self):
        self.assertEqual(read(ROOT / "VERSION").strip(), EXPECTED_VERSION)
        self.assertIn(f'APP_VERSION = "{EXPECTED_VERSION}"', read(APP / "config.py"))

    def test_telegram_logout_clears_listener_desired_state(self):
        web = read(APP / "web.py")
        route = re.search(
            r"def tg_logout\(\):(?P<body>.*?)(?=\n\n@app\.post\(\"/start_bot\"\))",
            web,
            flags=re.S,
        )
        self.assertIsNotNone(route)
        body = route.group("body")
        self.assertIn('set_value("listener_desired_state", "stopped")', body)
        self.assertLess(
            body.index('set_value("listener_desired_state", "stopped")'),
            body.index("manager.stop()"),
        )

    def test_count_patterns_scans_each_requested_pattern(self):
        redis_store = read(APP / "redis_store.py")
        body = re.search(
            r"def count_patterns\(patterns: list\[str\]\) -> int:(?P<body>.*?)(?=\n\nACTIVE_DEDUP_PATTERNS)",
            redis_store,
            flags=re.S,
        )
        self.assertIsNotNone(body)
        text = body.group("body")
        self.assertIn("for pattern in patterns", text)
        self.assertIn("match=pattern", text)
        self.assertNotIn('match="dedup:*"', text)

    def test_index_template_script_has_valid_javascript_after_jinja_substitution(self):
        if not shutil.which("node"):
            self.skipTest("node is not available")
        html = read(APP / "templates" / "index.html")
        script = re.search(r"<script>(?P<script>[\s\S]*)</script>", html)
        self.assertIsNotNone(script)
        js = script.group("script")
        replacements = {
            "{{ dialog_cache|tojson }}": "[]",
            "{{ monitor_chats|tojson }}": "[]",
            "{{ exclude_chats|tojson }}": "[]",
            "{{ regex_rules|tojson }}": "[]",
            "{{ code_rules|tojson }}": "[]",
            "{{ csrf_token() }}": "test-csrf-token",
        }
        for old, new in replacements.items():
            js = js.replace(old, new)
        self.assertNotIn("{{", js)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".js", delete=False) as fh:
            fh.write(js)
            temp_path = fh.name
        try:
            proc = subprocess.run(["node", "--check", temp_path], text=True, capture_output=True, timeout=15)
        finally:
            Path(temp_path).unlink(missing_ok=True)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)

    def test_dialog_stats_counts_visible_checked_dialogs_separately_from_saved_monitor_keys(self):
        store = {
            "monitor_chats": {"a", "@source", "stale-old-chat"},
            "exclude_chats": set(),
        }

        fake_redis_store = types.ModuleType("redis_store")
        fake_redis_store.clear_timezone_cache = lambda: None
        fake_redis_store.ACTIVE_DEDUP_PATTERNS = []
        fake_redis_store.DEDUP_META_PATTERNS = []
        fake_redis_store.r = types.SimpleNamespace()
        fake_redis_store.add_fail = lambda *a, **k: None
        fake_redis_store.cache_stats = lambda: {}
        fake_redis_store.clear_stats_cache = lambda: None
        fake_redis_store.delete = lambda *a, **k: None
        fake_redis_store.delete_pattern = lambda *a, **k: 0
        fake_redis_store.ensure_defaults = lambda: None
        fake_redis_store.format_time = lambda *a, **k: "2026-07-09 00:00:00"
        fake_redis_store.get = lambda key, default=None: default
        fake_redis_store.get_json = lambda key, default=None: []
        fake_redis_store.list_events = lambda *a, **k: []
        fake_redis_store.list_fails = lambda *a, **k: []
        fake_redis_store.list_hits = lambda *a, **k: []
        fake_redis_store.list_perf_events = lambda *a, **k: []
        fake_redis_store.migrate_known_regex_rules = lambda *a, **k: 0
        fake_redis_store.log_line = lambda *a, **k: None
        fake_redis_store.push_event = lambda *a, **k: None
        fake_redis_store.sadd = lambda key, value: store.setdefault(key, set()).add(value)
        fake_redis_store.scan_keys = lambda *a, **k: []
        fake_redis_store.set_json = lambda *a, **k: None
        fake_redis_store.set_value = lambda *a, **k: None
        fake_redis_store.smembers = lambda key: set(store.get(key, set()))
        fake_redis_store.srem = lambda key, value: store.setdefault(key, set()).discard(value)
        fake_redis_store.trim_runtime_lists = lambda: None
        fake_redis_store.flush_batch_records = lambda wait=False: None

        fake_bot_runner = types.ModuleType("bot_runner")
        fake_bot_runner.manager = types.SimpleNamespace(
            is_running=lambda: False,
            start=lambda: "ok",
            stop=lambda: "ok",
            clear_runtime_cache=lambda: None,
            test_send=lambda: "ok",
            test_source=lambda value: "ok",
            list_dialogs=lambda force=False: [],
        )

        fake_dedup = types.ModuleType("dedup")
        fake_dedup.build_profile = lambda *a, **k: {}
        fake_dedup.clear_ttl_cache = lambda: None
        fake_dedup.list_dedup_recent = lambda *a, **k: []
        fake_dedup.release_dedup = lambda *a, **k: True
        fake_dedup.ttl_minutes_for_activity = lambda *a, **k: 20
        fake_dedup.ttl_minutes_for_profile = lambda *a, **k: 20

        class FakeFlask:
            def __init__(self, *args, **kwargs):
                self.secret_key = ""

            def route(self, *args, **kwargs):
                return lambda fn: fn

            def get(self, *args, **kwargs):
                return lambda fn: fn

            def post(self, *args, **kwargs):
                return lambda fn: fn

            def context_processor(self, fn):
                return fn

            def before_request(self, fn):
                return fn

            def after_request(self, fn):
                return fn

            def errorhandler(self, *args, **kwargs):
                return lambda fn: fn

        fake_flask = types.ModuleType("flask")
        fake_flask.Flask = FakeFlask
        fake_flask.Response = lambda *a, **k: ("response", a, k)
        fake_flask.jsonify = lambda *a, **k: {"args": a, **k}
        fake_flask.redirect = lambda *a, **k: "redirect"
        fake_flask.render_template = lambda *a, **k: "template"
        fake_flask.request = types.SimpleNamespace(headers={}, args={}, form={}, files={})
        fake_flask.session = {}
        fake_flask.url_for = lambda *a, **k: "url"

        fake_werkzeug = types.ModuleType("werkzeug")
        fake_werkzeug_exceptions = types.ModuleType("werkzeug.exceptions")
        fake_werkzeug_exceptions.HTTPException = type("HTTPException", (Exception,), {})

        fake_modules = {
            "flask": fake_flask,
            "werkzeug": fake_werkzeug,
            "werkzeug.exceptions": fake_werkzeug_exceptions,
            "redis_store": fake_redis_store,
            "telegram_login": types.ModuleType("telegram_login"),
            "bot_runner": fake_bot_runner,
            "dedup": fake_dedup,
            "matcher": types.ModuleType("matcher"),
            "code_rules": types.ModuleType("code_rules"),
        }
        fake_modules["matcher"].analyze_message = lambda *a, **k: {}
        fake_modules["matcher"].match_rule_details = lambda *a, **k: {}
        fake_modules["matcher"].rule_diagnostics = lambda *a, **k: []
        fake_modules["matcher"].invalidate_rule_cache = lambda *a, **k: None
        fake_modules["code_rules"].add_code_rule = lambda *a, **k: None
        fake_modules["code_rules"].code_rule_diagnostics = lambda *a, **k: []
        fake_modules["code_rules"].delete_code_rule = lambda *a, **k: True
        fake_modules["code_rules"].get_code_rules = lambda *a, **k: []
        fake_modules["code_rules"].reset_code_rules = lambda *a, **k: None
        fake_modules["code_rules"].save_code_rules = lambda *a, **k: None
        fake_modules["code_rules"].update_code_rule = lambda *a, **k: True

        old_modules = {name: sys.modules.get(name) for name in fake_modules}
        sys.modules.update(fake_modules)
        sys.path.insert(0, str(APP))
        try:
            spec = importlib.util.spec_from_file_location("web_stats_under_test", APP / "web.py")
            web = importlib.util.module_from_spec(spec)
            self.assertIsNotNone(spec)
            self.assertIsNotNone(spec.loader)
            spec.loader.exec_module(web)
            dialogs = [
                {"value": "a", "id": "1", "username": ""},
                {"value": "b", "id": "2", "username": "source"},
                {"value": "c", "id": "3", "username": ""},
            ]

            prepared = []
            for item in dialogs:
                d = dict(item)
                variants = web._variants_for_dialog(d)
                d["checked"] = bool(variants & store["monitor_chats"])
                d["excluded"] = False
                prepared.append(d)

            stats = web._dialog_stats(prepared)
        finally:
            try:
                sys.path.remove(str(APP))
            except ValueError:
                pass
            for name, module in old_modules.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module

        self.assertEqual(stats["listening"], 2)
        self.assertEqual(stats["unlistened"], 1)
        self.assertEqual(stats["monitor_saved"], 3)


if __name__ == "__main__":
    unittest.main()
