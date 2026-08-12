import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
EXPECTED_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8-sig").strip()


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


class LogAlignmentV13864Tests(unittest.TestCase):
    def test_versions_are_bumped_to_v13864(self):
        self.assertEqual(read(ROOT / "VERSION").strip(), EXPECTED_VERSION)
        self.assertIn(f'APP_VERSION = "{EXPECTED_VERSION}"', read(APP / "config.py"))

    def test_perf_events_store_queue_type_for_web_comparison(self):
        bot_runner = read(APP / "bot_runner.py")
        record_perf = re.search(
            r"def _record_perf_event\(.*?\n    def _release_pending_dedup",
            bot_runner,
            flags=re.S,
        )
        self.assertIsNotNone(record_perf)
        self.assertIn('"queue_type": perf.get("queue_type", "")', record_perf.group(0))

    def test_high_delay_warning_is_pushed_to_web_events(self):
        bot_runner = read(APP / "bot_runner.py")
        record_delay = re.search(
            r"def _record_telegram_delay\(.*?\n    async def _reconnect_listener_client",
            bot_runner,
            flags=re.S,
        )
        self.assertIsNotNone(record_delay)
        body = record_delay.group(0)
        self.assertGreaterEqual(body.count('push_event("warning", f"Telegram 推送高延迟'), 2)

    def test_duplicate_code_event_includes_link_for_docker_web_alignment(self):
        bot_runner = read(APP / "bot_runner.py")
        duplicate_branch = re.search(
            r"if duplicate_identity:(?P<body>.*?push_event\(\"info\", event_message\))",
            bot_runner,
            flags=re.S,
        )
        self.assertIsNotNone(duplicate_branch)
        body = duplicate_branch.group("body")
        self.assertIn('event_message = f"{message}：{link}" if link else message', body)
        self.assertIn('push_event("info", event_message)', body)
        self.assertNotIn('log_line("info", event_message)', body)


if __name__ == "__main__":
    unittest.main()


