import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
EXPECTED_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8-sig").strip()


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


class CpuRootCauseMitigationV13874Tests(unittest.TestCase):
    def test_versions_are_bumped_to_v13874(self):
        self.assertEqual(read(ROOT / "VERSION").strip(), EXPECTED_VERSION)
        self.assertIn(f'APP_VERSION = "{EXPECTED_VERSION}"', read(APP / "config.py"))

    def test_telegram_event_entry_uses_dynamic_fast_chat_filter(self):
        source = read(APP / "bot_runner.py")

        self.assertIn("self._monitor_peer_ids = frozenset()", source)
        self.assertIn("self._monitor_filter_complete = False", source)
        self.assertIn("self._monitor_filter_dirty = True", source)
        self.assertIn("def _refresh_monitor_peer_filter(self)", source)
        self.assertIn("def _fast_event_filter(self, event)", source)
        self.assertIn("events.NewMessage(incoming=True, func=self._fast_event_filter)", source)
        self.assertIn("chat_id = getattr(event, \"chat_id\", None)", source)
        self.assertIn("chat_id not in self._monitor_peer_ids", source)

        clear_cache = re.search(
            r"def clear_runtime_cache\(self\):(?P<body>.*?)(?=\n\s+def )",
            source,
            flags=re.S,
        )
        self.assertIsNotNone(clear_cache)
        self.assertIn("self._monitor_filter_dirty = True", clear_cache.group("body"))

    def test_listener_publishes_one_minute_flow_diagnostics(self):
        source = read(APP / "bot_runner.py")

        for fragment in (
            '"received"',
            '"fast_filtered"',
            '"handler_entered"',
            '"enqueued"',
            '"queue_full"',
            '"matched"',
            '"forwarded"',
            '"listener_flow_stats"',
            "self.queue.qsize() if self.queue else 0",
            "self.priority_queue.qsize() if self.priority_queue else 0",
        ):
            self.assertIn(fragment, source)
        self.assertIn("def _publish_flow_stats(self", source)

    def test_python_stack_dump_is_enabled_for_watchdog_signal(self):
        source = read(APP / "main.py")

        self.assertIn("import faulthandler", source)
        self.assertIn("import signal", source)
        self.assertIn("faulthandler.enable()", source)
        self.assertIn("faulthandler.register(signal.SIGUSR1", source)

    def test_watchdog_captures_stack_flow_and_thread_state_without_process_args(self):
        source = read(ROOT / "deploy" / "ops" / "slowlink_watchdog.sh")

        self.assertIn("kill -USR1 1", source)
        self.assertIn("listener_flow_stats", source)
        self.assertIn("python stack series requested", source)
        self.assertIn("thread CPU window", source)
        self.assertNotIn('docker kill --signal=USR1 "$APP_CONTAINER"', source)
        self.assertNotIn("etime,args", source)
        self.assertIn('docker restart "$APP_CONTAINER"', source)
        self.assertNotIn("docker restart slowlink_redis", source)

    def test_manage_status_displays_latest_message_flow(self):
        source = read(ROOT / "deploy" / "manage.sh")

        self.assertIn("listener_flow_stats", source)
        self.assertIn("最近消息流", source)

    def test_dialog_cache_clear_also_clears_persisted_entity_index(self):
        web = read(APP / "web.py")
        clear_cache = re.search(
            r"def clear_cache\(\):.*",
            web,
            flags=re.S,
        )
        self.assertIsNotNone(clear_cache)
        body = clear_cache.group(0)
        dialogs_branch = re.search(
            r"elif kind == \"dialogs\":.*?elif kind == \"runtime\":",
            body,
            flags=re.S,
        )
        self.assertIsNotNone(dialogs_branch)
        self.assertIn('delete("dialog_cache", "entity_index")', dialogs_branch.group(0))
        self.assertIn("manager.entity_cache = {}", dialogs_branch.group(0))
        self.assertIn("manager._monitor_filter_dirty = True", dialogs_branch.group(0))

    def test_clear_record_actions_wait_for_batch_flush(self):
        web = read(APP / "web.py")
        self.assertGreaterEqual(web.count("flush_batch_records(wait=True)"), 2)
        self.assertIn("from redis_store import", web)


if __name__ == "__main__":
    unittest.main()
