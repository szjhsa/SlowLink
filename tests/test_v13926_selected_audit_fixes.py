import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
EXPECTED_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8-sig").strip()
sys.path.insert(0, str(APP))

from tests.test_v13871_cpu_dedup_stability import load_code_rules_with_fake_redis
from tests.test_v13883_cross_template_lottery_dedup import (
    NO_SEED_MESSAGE,
    SEEDED_MESSAGE,
    load_dedup,
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


class SelectedAuditFixesV13926Tests(unittest.TestCase):
    def test_versions_are_bumped_to_current_release(self):
        self.assertEqual(read(ROOT / "VERSION").strip(), EXPECTED_VERSION)
        self.assertIn(f'APP_VERSION = "{EXPECTED_VERSION}"', read(APP / "config.py"))

    def test_template_conflict_does_not_leave_new_keys(self):
        dedup, client = load_dedup()
        dedup.check_and_mark(
            NO_SEED_MESSAGE,
            "https://t.me/Petrichor_Embys_chat/243716",
            None,
            "strict",
            "集邮者联盟",
        )
        duplicate, _reason, profile2 = dedup.check_and_mark(
            SEEDED_MESSAGE,
            "https://t.me/Jsoo8888/115924",
            None,
            "strict",
            "茶话领域",
        )

        self.assertTrue(duplicate)
        leftovers = [
            key
            for key, value in client.values.items()
            if key.startswith("dedup:") and value == profile2["dedup_id"]
        ]
        self.assertEqual(leftovers, [])

    def test_x5_quantity_is_not_registered_as_code(self):
        code_rules, _saved = load_code_rules_with_fake_redis()
        header = "\u5df2\u4e3a\u60a8\u751f\u6210\u4e86 30\u5929 \u6ce8\u518c\u7801 x5 \u4e2a"
        text = header + "\n\nNONAY-30-Register_hepfJi5VD6"

        identities = code_rules.extract_code_identities(text)

        self.assertNotIn("field_code:x5", identities)
        self.assertNotIn("strong_register_renew:x5", identities)
        self.assertIn("strong_register_renew:NONAY-30-Register_hepfJi5VD6", identities)

    def test_pending_duplicate_requeue_logic_is_wired(self):
        runner = read(APP / "bot_runner.py")

        self.assertIn("_pending_duplicate_events", runner)
        self.assertIn("_remember_pending_duplicate(duplicate_code_key, event", runner)
        self.assertIn("self._requeue_pending_duplicates(reserved_code_keys)", runner)
        self.assertIn("self._clear_pending_duplicates(reserved_code_keys)", runner)
        self.assertGreaterEqual(
            runner.count("self._requeue_pending_duplicates(reserved_code_keys)"),
            3,
        )

    def test_refresh_dialogs_restores_entity_cache_on_failure(self):
        web = read(APP / "web.py")

        self.assertIn('old_entity_cache = dict(getattr(manager, "entity_cache", {}) or {})', web)
        self.assertIn("manager.entity_cache = old_entity_cache", web)
        self.assertIn("manager._monitor_filter_dirty = True", web)


if __name__ == "__main__":
    unittest.main()
