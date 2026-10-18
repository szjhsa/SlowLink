import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
EXPECTED_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8-sig").strip()


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


class OnDemandRefreshV13860Tests(unittest.TestCase):
    def test_versions_are_bumped_to_v13860(self):
        self.assertEqual(read(ROOT / "VERSION").strip(), EXPECTED_VERSION)
        self.assertIn(f'APP_VERSION = "{EXPECTED_VERSION}"', read(APP / "config.py"))

    def test_page_pauses_polling_when_hidden_and_resumes_on_visible(self):
        index = read(APP / "templates" / "index.html")
        self.assertIn("let pollTimer = null", index)
        self.assertIn("let stateAbort = null", index)
        self.assertIn("function isPageHidden()", index)
        self.assertIn("document.hidden || document.visibilityState === 'hidden'", index)
        self.assertIn("function startPolling()", index)
        self.assertIn("function stopPolling()", index)
        self.assertIn("function handleVisibilityChange()", index)
        self.assertIn("document.addEventListener('visibilitychange', handleVisibilityChange)", index)
        self.assertIn("if(isPageHidden())", index)
        self.assertIn("stopPolling()", index)
        self.assertIn("requestLightState('visible-resume')", index)
        self.assertNotIn("setInterval(()=>loadState(false), 5000)", index)

    def test_state_requests_are_light_by_default_and_full_only_on_demand(self):
        index = read(APP / "templates" / "index.html")
        self.assertIn("async function requestState(full=false, reason='poll')", index)
        self.assertIn("if(!full && isPageHidden()) return", index)
        self.assertIn("stateAbort.abort()", index)
        self.assertIn("new AbortController()", index)
        self.assertIn("const url = '/api/state' + (full ? '' : '?light=1')", index)
        self.assertIn("function requestLightState(reason='poll')", index)
        self.assertIn("function requestFullState(reason='manual')", index)
        self.assertIn("requestFullState('initial')", index)
        self.assertIn("requestFullState('form-submit')", index)
        self.assertIn("requestFullState('records-tab')", index)
        self.assertIn("requestFullState('monitor-open')", index)

    def test_full_state_still_avoids_heavy_work_in_light_branch(self):
        web = read(APP / "web.py")
        light_branch = re.search(
            r"def _state_payload\(light: bool = False\).*?if light:\s*return (?P<body>\{.*?\})\s*dialogs =",
            web,
            flags=re.S,
        )
        self.assertIsNotNone(light_branch)
        body = light_branch.group("body")
        self.assertNotIn("list_perf_events", body)
        self.assertNotIn("cache_stats", body)
        self.assertNotIn("_prepare_dialog_cache", body)

    def test_rule_and_dedup_caches_are_cleared_on_admin_changes(self):
        web = read(APP / "web.py")
        bot_runner = read(APP / "bot_runner.py")
        matcher = read(APP / "matcher.py")

        self.assertIn("invalidate_rule_cache()", web)
        self.assertIn("self._dedup_settings = (0.0, True, 20, 20)", bot_runner)
        self.assertIn("analyze_message(", bot_runner)
        self.assertNotIn("extract_code_detail(text)", bot_runner)
        self.assertIn("if cached_raw is not None and now - cached_ts <= ttl:", matcher)
        self.assertIn("raw = tuple(sorted(smembers(\"regex_rules\")))", matcher)
        self.assertLess(
            matcher.index("if cached_raw is not None and now - cached_ts <= ttl:"),
            matcher.index("raw = tuple(sorted(smembers(\"regex_rules\")))"),
        )

    def test_ui_has_quiet_console_navigation_and_less_visual_clutter(self):
        index = read(APP / "templates" / "index.html")
        css = read(APP / "static" / "style.css")

        self.assertIn('class="slowlink-main-nav"', index)
        self.assertIn('href="#runBoard"', index)
        self.assertIn('href="#accountBoard"', index)
        self.assertIn('href="#rulesBoard"', index)
        self.assertIn('href="#recordsBoard"', index)
        self.assertIn('id="runBoard"', index)
        self.assertIn('id="accountBoard"', index)
        self.assertIn('id="rulesBoard"', index)
        self.assertIn('id="recordsBoard"', index)
        self.assertIn('class="ui-board grouped-board tools-board low-frequency-board"', index)
        self.assertIn("V1.38.60 on-demand refresh and console polish", css)
        self.assertIn(".slowlink-main-nav", css)
        self.assertIn(".low-frequency-board", css)
        self.assertIn(".records-subpanel", css)
        self.assertIn(".slowlink-main-nav a", css)
        self.assertIn(".records-subpanel", css)


if __name__ == "__main__":
    unittest.main()


