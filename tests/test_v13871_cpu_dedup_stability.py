import json
import re
import subprocess
import sys
import textwrap
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
EXPECTED_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8-sig").strip()
SAFE_REGISTER_RENEW_PATTERN = (
    r"(?:^|(?<=[\s:：，,]))[^\s*`-]+(?:-[^\s*`-]+)*-\d+"
    r"(?:-[^\s*`-]+)*-(?:Register|Renew)_[^\s*`]+?"
    r"(?=$|\s|[，。！？？；：、）】]|[,.;:)\]}>`~*](?![A-Za-z0-9_-]))"
)
LEGACY_SERVER_PATTERN = r"[^\s]+(?:-[^\s]+)*-\d+-(?:Register|Renew)_[A-Za-z0-9_-]+"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def load_code_rules_with_fake_redis(stored_rules=None):
    saved = []
    fake_redis_store = types.ModuleType("redis_store")
    fake_redis_store.get_json = lambda key, default=None: stored_rules if stored_rules is not None else default
    fake_redis_store.set_json = lambda key, value: saved.append((key, value))

    old = sys.modules.get("redis_store")
    sys.modules["redis_store"] = fake_redis_store
    sys.path.insert(0, str(APP))
    try:
        sys.modules.pop("code_rules", None)
        import code_rules
        return code_rules, saved
    finally:
        try:
            sys.path.remove(str(APP))
        except ValueError:
            pass
        if old is None:
            sys.modules.pop("redis_store", None)
        else:
            sys.modules["redis_store"] = old


class CpuAndDedupStabilityV13871Tests(unittest.TestCase):
    def test_versions_are_bumped_to_v13871(self):
        self.assertEqual(read(ROOT / "VERSION").strip(), EXPECTED_VERSION)
        self.assertIn(f'APP_VERSION = "{EXPECTED_VERSION}"', read(APP / "config.py"))

    def test_register_renew_extraction_does_not_backtrack_on_hyphen_text(self):
        child = textwrap.dedent(
            f"""
            import json
            import sys
            import time
            import types

            fake_redis_store = types.ModuleType("redis_store")
            fake_redis_store.get_json = lambda key, default=None: default
            fake_redis_store.set_json = lambda *args, **kwargs: None
            sys.modules["redis_store"] = fake_redis_store
            sys.path.insert(0, {json.dumps(str(APP))})

            import code_rules

            text = ("ABCD-" * 40)[:200]
            started = time.perf_counter()
            result = code_rules._extract_markdown_register_renew_code(text)
            elapsed_ms = (time.perf_counter() - started) * 1000
            print(json.dumps({{"result": result, "elapsed_ms": elapsed_ms}}))
            """
        )
        try:
            proc = subprocess.run(
                [sys.executable, "-c", child],
                text=True,
                capture_output=True,
                timeout=1.5,
            )
        except subprocess.TimeoutExpired:
            self.fail("Register/Renew extraction exceeded 1.5s on 200-character hyphen text")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertEqual(result["result"], "")
        self.assertLess(result["elapsed_ms"], 100)

    def test_full_code_extraction_stays_fast_on_max_length_plain_text(self):
        child = textwrap.dedent(
            f"""
            import json
            import sys
            import time
            import types

            fake_redis_store = types.ModuleType("redis_store")
            fake_redis_store.get_json = lambda key, default=None: default
            fake_redis_store.set_json = lambda *args, **kwargs: None
            sys.modules["redis_store"] = fake_redis_store
            sys.path.insert(0, {json.dumps(str(APP))})

            import code_rules

            text = "\u5df2\u4e3a\u60a8\u751f\u6210\u4e86 " + ("A" * 8170)
            started = time.perf_counter()
            result = code_rules.extract_code_detail(text)
            elapsed_ms = (time.perf_counter() - started) * 1000
            print(json.dumps({{"result": result, "elapsed_ms": elapsed_ms}}))
            """
        )
        try:
            proc = subprocess.run(
                [sys.executable, "-c", child],
                text=True,
                capture_output=True,
                timeout=1.5,
            )
        except subprocess.TimeoutExpired:
            self.fail("Full code extraction exceeded 1.5s on max-length plain text")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertEqual(result["result"], {})
        self.assertLess(result["elapsed_ms"], 100)

    def test_legacy_server_register_rule_is_migrated_and_saved(self):
        stored = [{
            "name": "Register/Renew 完整码",
            "pattern": LEGACY_SERVER_PATTERN,
            "group": "0",
            "enabled": True,
            "fast": True,
            "trigger": False,
            "strict_context": False,
            "note": "legacy",
        }]
        code_rules, saved = load_code_rules_with_fake_redis(stored)

        rules = code_rules.get_code_rules()

        self.assertEqual(rules[0]["pattern"], SAFE_REGISTER_RENEW_PATTERN)
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0][0], "code_rules")
        self.assertEqual(saved[0][1][0]["pattern"], SAFE_REGISTER_RENEW_PATTERN)

    def test_text_duplicate_releases_only_pending_code_reservation(self):
        bot_runner = read(APP / "bot_runner.py")
        branch = re.search(
            r"if dedup_enabled:(?P<body>.*?)(?=\n\s+sent = \[\])",
            bot_runner,
            flags=re.S,
        )
        self.assertIsNotNone(branch)
        body = branch.group("body")
        duplicate_branch = re.search(r"if duplicate:(?P<body>.*?return)", body, flags=re.S)
        self.assertIsNotNone(duplicate_branch)
        duplicate_body = duplicate_branch.group("body")
        self.assertIn("self._release_pending_dedup(reserved_code_keys)", duplicate_body)
        self.assertNotIn("dedup_profile", duplicate_body)

    def test_high_delay_warning_is_logged_once_through_push_event(self):
        bot_runner = read(APP / "bot_runner.py")
        method = re.search(
            r"def _record_telegram_delay\(.*?(?=\n\s+async def _reconnect_listener_client)",
            bot_runner,
            flags=re.S,
        )
        self.assertIsNotNone(method)
        body = method.group(0)
        self.assertNotIn('log_line("warning", f"Telegram 推送高延迟', body)
        self.assertEqual(body.count('push_event("warning", f"Telegram 推送高延迟'), 2)


if __name__ == "__main__":
    unittest.main()
