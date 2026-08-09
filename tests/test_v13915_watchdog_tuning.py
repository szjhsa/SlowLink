import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
OPS = ROOT / "ops"
EXPECTED_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8-sig").strip()


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


class WatchdogTuningV13915Tests(unittest.TestCase):
    def test_versions_are_bumped_to_current_release(self):
        self.assertEqual(read(ROOT / "VERSION").strip(), EXPECTED_VERSION)
        self.assertIn(f'APP_VERSION = "{EXPECTED_VERSION}"', read(APP / "config.py"))

    def test_watchdog_sampling_is_faster_and_threshold_is_lower(self):
        script = read(OPS / "slowlink_watchdog.sh")
        service = read(OPS / "slowlink-watchdog.service")

        self.assertIn('CHECK_INTERVAL="${CHECK_INTERVAL:-10}"', script)
        self.assertIn('CPU_THRESHOLD="${CPU_THRESHOLD:-80}"', script)
        self.assertIn("Environment=CHECK_INTERVAL=10", service)
        self.assertIn("Environment=CPU_THRESHOLD=80", service)

    def test_snapshot_covers_redis_and_host_processes(self):
        script = read(OPS / "slowlink_watchdog.sh")

        self.assertIn('docker stats --no-stream "$APP_CONTAINER" "$REDIS_CONTAINER"', script)
        self.assertIn('ps -eo pid,tid,ppid,comm,%cpu,%mem,etime --sort=-%cpu', script)
        self.assertIn("snapshot: load=", script)

    def test_first_high_cpu_sample_takes_host_snapshot_before_stack(self):
        script = read(OPS / "slowlink_watchdog.sh")
        branch = script.split('if [ "$high_count" -eq 1 ]; then', 1)[1]
        branch = branch.split("fi", 1)[0]

        self.assertLess(branch.index("snapshot"), branch.index("capture_python_state"))


if __name__ == "__main__":
    unittest.main()
