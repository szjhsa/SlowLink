import unittest
from pathlib import Path

from tests.test_v13871_cpu_dedup_stability import load_code_rules_with_fake_redis


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
EXPECTED_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8-sig").strip()


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


class MultiCodeDedupV13921Tests(unittest.TestCase):
    def test_versions_are_bumped_to_current_release(self):
        self.assertEqual(read(ROOT / "VERSION").strip(), EXPECTED_VERSION)
        self.assertIn(f'APP_VERSION = "{EXPECTED_VERSION}"', read(APP / "config.py"))

    def test_all_codes_in_one_message_get_identities(self):
        code_rules, _saved = load_code_rules_with_fake_redis()
        text = (
            "NONAY-30-Register_isMSRojTaz\n"
            "NONAY-30-Register_QLmne0nC5W\n"
            "NONAY-30-Register_AiiLXFiEZ1"
        )

        identities = code_rules.extract_code_identities(text)

        self.assertEqual(
            set(identities),
            {
                "strong_register_renew:NONAY-30-Register_isMSRojTaz",
                "strong_register_renew:NONAY-30-Register_QLmne0nC5W",
                "strong_register_renew:NONAY-30-Register_AiiLXFiEZ1",
            },
        )

    def test_single_code_message_identity_is_a_subset(self):
        code_rules, _saved = load_code_rules_with_fake_redis()

        identities = code_rules.extract_code_identities("NONAY-30-Register_QLmne0nC5W")

        self.assertEqual(
            identities,
            ["strong_register_renew:NONAY-30-Register_QLmne0nC5W"],
        )

    def test_bot_runner_reserves_all_code_keys(self):
        bot_runner = read(APP / "bot_runner.py")

        self.assertIn("from code_rules import extract_code_identities", bot_runner)
        self.assertIn("for identity in extract_code_identities(text):", bot_runner)
        self.assertIn("reserved_code_keys.append(code_key)", bot_runner)
        self.assertIn("duplicate_identity", bot_runner)
        self.assertIn("self._release_pending_dedup(reserved_code_keys)", bot_runner)


if __name__ == "__main__":
    unittest.main()
