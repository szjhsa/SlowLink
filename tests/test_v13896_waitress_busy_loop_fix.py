import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WaitressBusyLoopFixTests(unittest.TestCase):
    def test_waitress_includes_half_open_socket_busy_loop_fix(self):
        requirements = {
            line.strip()
            for line in (ROOT / "deploy" / "requirements.txt").read_text(encoding="utf-8-sig").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }

        self.assertIn("waitress==3.0.2", requirements)


if __name__ == "__main__":
    unittest.main()
