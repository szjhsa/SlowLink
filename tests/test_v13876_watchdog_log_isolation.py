import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
EXPECTED_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8-sig").strip()


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


class WatchdogLogIsolationV13876Tests(unittest.TestCase):
    def test_versions_are_bumped_to_v13876(self):
        self.assertEqual(read(ROOT / "VERSION").strip(), EXPECTED_VERSION)
        self.assertIn(f'APP_VERSION = "{EXPECTED_VERSION}"', read(APP / "config.py"))

    def test_sigusr1_stack_is_written_to_a_dedicated_file(self):
        source = read(APP / "main.py")

        self.assertIn('STACK_DUMP_PATH', source)
        self.assertIn('open(STACK_DUMP_PATH, "a"', source)
        self.assertIn('faulthandler.register(', source)
        self.assertIn('file=_stack_dump_file', source)

    def test_watchdog_collects_isolated_stack_series_without_copying_docker_logs(self):
        source = read(ROOT / "deploy" / "ops" / "slowlink_watchdog.sh")

        self.assertIn('STACK_DUMP_PATH=', source)
        self.assertIn('STACK_SAMPLE_COUNT="${STACK_SAMPLE_COUNT:-3}"', source)
        self.assertIn("kill -USR1 1", source)
        self.assertNotIn('docker kill --signal=USR1 "$APP_CONTAINER"', source)
        self.assertIn('cat "$1"', source)
        self.assertIn(': > "$1"', source)
        self.assertEqual(source.count('capture_python_state "'), 1)
        self.assertNotIn('docker logs --tail', source)


if __name__ == "__main__":
    unittest.main()
