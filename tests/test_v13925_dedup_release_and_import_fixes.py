import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
EXPECTED_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8-sig").strip()


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


class DedupReleaseAndImportFixesV13925Tests(unittest.TestCase):
    def test_versions_are_bumped_to_current_release(self):
        self.assertEqual(read(ROOT / "VERSION").strip(), EXPECTED_VERSION)
        self.assertIn(f'APP_VERSION = "{EXPECTED_VERSION}"', read(APP / "config.py"))

    def test_code_dedup_is_disabled_when_dedup_disabled_or_zero(self):
        bot_runner = read(APP / "bot_runner.py")

        self.assertIn("code_dedup_enabled = dedup_enabled and code_minutes > 0", bot_runner)
        self.assertIn("if code_identities and code_dedup_enabled:", bot_runner)

    def test_exceptions_release_pending_code_and_text_dedup(self):
        bot_runner = read(APP / "bot_runner.py")
        handle = re.search(
            r"async def _handle_message\(.*?(?=\n    def _source_name)",
            bot_runner,
            flags=re.S,
        )
        self.assertIsNotNone(handle)
        body = handle.group(0)
        flood_idx = body.rindex("except FloodWaitError as e:")
        generic_idx = body.rindex("except Exception as e:")
        release_call = "self._release_pending_dedup(reserved_code_keys, dedup_profile)"
        self.assertIn(release_call, body[flood_idx:flood_idx + 300])
        self.assertIn(release_call, body[generic_idx:generic_idx + 300])

    def test_start_bot_writes_desired_state_only_after_successful_start(self):
        web = read(APP / "web.py")
        start_bot = re.search(
            r"def start_bot\(\):.*?(?=\n@)",
            web,
            flags=re.S,
        )
        self.assertIsNotNone(start_bot)
        body = start_bot.group(0)
        self.assertLess(
            body.index("msg = manager.start()"),
            body.index('set_value("listener_desired_state", "running")'),
        )
        self.assertIn('set_value("listener_desired_state", "stopped")', body)

    def test_rules_only_import_does_not_touch_monitor_or_exclude(self):
        web = read(APP / "web.py")
        import_config = re.search(
            r"def import_config\(\):.*?(?=\n\s*@)",
            web,
            flags=re.S,
        )
        self.assertIsNotNone(import_config)
        body = import_config.group(0)
        self.assertIn('if mode == "rules_only":', body)
        rules_only_branch = re.search(
            r'if mode == "rules_only":.*?else:',
            body,
            flags=re.S,
        )
        self.assertIsNotNone(rules_only_branch)
        self.assertIn(
            'set_items = [("regex_rules", "regex_rules", "正则规则")]',
            rules_only_branch.group(0),
        )
        self.assertNotIn('"monitor_chats"', rules_only_branch.group(0))

    def test_dedup_import_clears_listener_ttl_cache(self):
        web = read(APP / "web.py")
        import_config = re.search(
            r"def import_config\(\):.*?(?=\n\s*@)",
            web,
            flags=re.S,
        )
        self.assertIsNotNone(import_config)
        body = import_config.group(0)
        changed_branch = re.search(
            r"if changed_dedup:.*?(?=\n\s+ui = )",
            body,
            flags=re.S,
        )
        self.assertIsNotNone(changed_branch)
        self.assertIn("clear_ttl_cache()", changed_branch.group(0))

    def test_partial_dialog_refresh_restores_persisted_entity_index(self):
        web = read(APP / "web.py")
        refresh = re.search(
            r"def refresh_dialogs\(\):.*?(?=\n\s*@)",
            web,
            flags=re.S,
        )
        self.assertIsNotNone(refresh)
        body = refresh.group(0)
        self.assertIn('old_entity_index = get_json("entity_index", {}) or {}', body)
        self.assertGreaterEqual(body.count('set_json("entity_index", old_entity_index)'), 2)

    def test_ajax_submit_honors_formaction_for_code_rule_delete(self):
        index = read(APP / "templates" / "index.html")
        self.assertIn("e.submitter && e.submitter.formAction", index)
        self.assertIn("const action = (e.submitter && e.submitter.formAction) || form.action;", index)
        self.assertIn("fetch(action, {method: form.method || 'POST'", index)


if __name__ == "__main__":
    unittest.main()
