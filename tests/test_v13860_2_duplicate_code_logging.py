import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


class DuplicateCodeLoggingV138602Tests(unittest.TestCase):
    def test_duplicate_code_branch_writes_hit_and_event_log(self):
        bot_runner = read(APP / "bot_runner.py")
        start = bot_runner.index("if not is_new:")
        end = bot_runner.index("return", start)
        branch = bot_runner[start:end]

        self.assertIn('"duplicate_identity"', branch)
        self.assertIn('"status": message', branch)
        self.assertIn('event_message = f"{message}：{link}" if link else message', branch)
        self.assertIn('push_event("info", event_message)', branch)
        self.assertNotIn('log_line("info", event_message)', branch)


if __name__ == "__main__":
    unittest.main()


