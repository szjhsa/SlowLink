import unittest
from pathlib import Path

from tests.test_v13871_cpu_dedup_stability import load_code_rules_with_fake_redis


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
EXPECTED_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8-sig").strip()


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


class BatchCountDedupV13922Tests(unittest.TestCase):
    def test_versions_are_bumped_to_current_release(self):
        self.assertEqual(read(ROOT / "VERSION").strip(), EXPECTED_VERSION)
        self.assertIn(f'APP_VERSION = "{EXPECTED_VERSION}"', read(APP / "config.py"))

    def test_batch_count_5_is_not_registered_as_code_identity(self):
        code_rules, _saved = load_code_rules_with_fake_redis()
        header = "\u5df2\u4e3a\u60a8\u751f\u6210\u4e86 30\u5929 \u6ce8\u518c\u7801 5 \u4e2a"
        text = (
            header
            + "\n\nNONAY-30-Register_hepfJi5VD6\n"
            "NONAY-30-Register_B7R6dpsPC1"
        )

        identities = code_rules.extract_code_identities(text)

        self.assertNotIn("strong_register_renew:5", identities)
        self.assertNotIn("field_code:5", identities)
        self.assertIn("strong_register_renew:NONAY-30-Register_hepfJi5VD6", identities)
        self.assertIn("strong_register_renew:NONAY-30-Register_B7R6dpsPC1", identities)

    def test_batch_count_10_is_not_registered_as_code_identity(self):
        code_rules, _saved = load_code_rules_with_fake_redis()
        header = "\u5df2\u4e3a\u60a8\u751f\u6210\u4e86 30\u5929 \u6ce8\u518c\u7801 10 \u4e2a"
        text = header + "\n\nNONAY-30-Register_Vco3tLjscQ"

        identities = code_rules.extract_code_identities(text)

        self.assertNotIn("strong_register_renew:10", identities)
        self.assertNotIn("field_code:10", identities)
        self.assertIn("strong_register_renew:NONAY-30-Register_Vco3tLjscQ", identities)

    def test_real_field_code_still_gets_field_identity(self):
        code_rules, _saved = load_code_rules_with_fake_redis()
        text = "\u6ce8\u518c\u7801\uff1aABC123"

        detail = code_rules.extract_code_detail(text)

        self.assertEqual(detail.get("code"), "ABC123")
        self.assertEqual(detail.get("identity"), "field_code:ABC123")


if __name__ == "__main__":
    unittest.main()
