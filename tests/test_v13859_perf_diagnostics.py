import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
EXPECTED_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8-sig").strip()


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


class PerfDiagnosticsV13859Tests(unittest.TestCase):
    def test_versions_are_bumped_to_v13859(self):
        self.assertEqual(read(ROOT / "VERSION").strip(), EXPECTED_VERSION)
        self.assertIn(f'APP_VERSION = "{EXPECTED_VERSION}"', read(APP / "config.py"))

    def test_redis_store_has_bounded_perf_event_list(self):
        redis_store = read(APP / "redis_store.py")
        self.assertIn("def add_perf_event(item: dict, limit: int = 120) -> None:", redis_store)
        self.assertIn('_enqueue_record("perf_events"', redis_store)
        self.assertIn('pipe.lpush(key, raw)', redis_store)
        self.assertIn('pipe.ltrim(key, 0, limit - 1)', redis_store)
        self.assertIn("def list_perf_events(limit: int = 30) -> list[dict]:", redis_store)
        self.assertIn('r.lrange("perf_events", 0, limit - 1)', redis_store)
        self.assertIn('list_len("perf_events")', redis_store)
        self.assertIn('r.ltrim("perf_events", 0, 119)', redis_store)

    def test_bot_runner_records_perf_events_without_changing_matching_flow(self):
        bot_runner = read(APP / "bot_runner.py")
        self.assertRegex(bot_runner, r"from redis_store import .*add_perf_event")
        self.assertIn("def _record_perf_event(", bot_runner)
        self.assertIn('"queue_wait_ms": perf.get("queue_wait_ms", 0)', bot_runner)
        self.assertIn('"match_ms": perf.get("match_ms", 0)', bot_runner)
        self.assertIn('"pre_dedup_ms": perf.get("pre_dedup_ms", perf.get("dedup_ms", 0))', bot_runner)
        self.assertIn('"before_send_ms": perf.get("before_send_ms", 0)', bot_runner)
        self.assertIn('"total_ms": perf.get("total_ms", 0)', bot_runner)
        self.assertIn('"telegram_delay_sec": perf.get("telegram_delay_sec", 0)', bot_runner)
        self.assertIn('"slow": self._is_slow_perf(perf)', bot_runner)
        self.assertIn('self._record_perf_event(source_name, rule, link, "sent"', bot_runner)
        self.assertIn('self._record_perf_event(source_name, rule, link, "duplicate_identity"', bot_runner)
        self.assertIn('self._record_perf_event(source_name, rule, link, "duplicate"', bot_runner)
        self.assertIn('self._record_perf_event(source_name, rule, link, "send_failed"', bot_runner)
        self.assertIn('except Exception:', bot_runner, "perf diagnostics must be best-effort")

    def test_full_state_and_clear_logs_include_perf_events(self):
        web = read(APP / "web.py")
        self.assertIsNotNone(re.search(r"from redis_store import \([^)]*list_perf_events", web, flags=re.S))
        self.assertGreaterEqual(web.count('"perf_events": list_perf_events(30)'), 2)

        light_branch = re.search(
            r"def _state_payload\(light: bool = False\).*?if light:\s*return (?P<body>\{.*?\})\s*dialogs =",
            web,
            flags=re.S,
        )
        self.assertIsNotNone(light_branch)
        self.assertNotIn("perf_events", light_branch.group("body"))

        self.assertIn('if kind == "perf_events":', web)
        self.assertIn('delete("perf_events")', web)
        self.assertIn('delete("events", "hits", "fails", "dedup:recent", "perf_events")', web)

    def test_frontend_has_perf_diagnostics_panel(self):
        index = read(APP / "templates" / "index.html")
        self.assertIn('data-tab="perf"', index)
        self.assertIn('data-panel="perf"', index)
        self.assertIn('id="perfBox"', index)
        self.assertIn('name="kind" value="perf_events"', index)
        self.assertIn("const perfEvents = data.perf_events || []", index)
        self.assertIn("const hasHits = Object.prototype.hasOwnProperty.call(data, 'hits')", index)
        self.assertIn("const hasPerfEvents = Object.prototype.hasOwnProperty.call(data, 'perf_events')", index)
        self.assertIn("perfEvents.length ? perfEvents.map", index)
        self.assertIn("if(hasPerfEvents && perfBox)", index)
        self.assertIn("perf-badges", index)

        css = read(APP / "static" / "style.css")
        self.assertIn("V1.38.59 perf diagnostics panel", css)
        self.assertIn(".perf-badges", css)
        self.assertIn(".perf-badge.slow", css)


if __name__ == "__main__":
    unittest.main()


