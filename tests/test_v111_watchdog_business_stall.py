import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / "deploy" / "ops"


class WatchdogBusinessStallTests(unittest.TestCase):
    def test_watchdog_requires_cpu_high_and_business_stall(self):
        script = (OPS / "slowlink_watchdog.sh").read_text(encoding="utf-8-sig")
        service = (OPS / "slowlink-watchdog.service").read_text(
            encoding="utf-8-sig"
        )

        self.assertIn(
            'BUSINESS_STALL_SECONDS="${BUSINESS_STALL_SECONDS:-600}"',
            script,
        )
        self.assertIn("Environment=BUSINESS_STALL_SECONDS=600", service)
        self.assertIn("sample_business_stall()", script)
        self.assertIn("listener_last_progress_ts", script)
        self.assertIn("listener_last_received_ts", script)
        self.assertIn("listener_job_stalled", script)
        self.assertIn("business_stall_count", script)
        self.assertIn(
            "restart deferred: high CPU but business is still progressing",
            script,
        )
        self.assertIn(
            "restarting $APP_CONTAINER after sustained high CPU and business stall",
            script,
        )

    def test_watchdog_restart_condition_requires_both_counters(self):
        script = (OPS / "slowlink_watchdog.sh").read_text(encoding="utf-8-sig")
        restart_branch = script.split(
            'if [ "$high_count" -ge "$HIGH_COUNT_LIMIT" ]; then',
            1,
        )[1]

        self.assertLess(
            restart_branch.index(
                '"$business_stall_count" -lt "$HIGH_COUNT_LIMIT"'
            ),
            restart_branch.index('docker restart "$APP_CONTAINER"'),
        )


if __name__ == "__main__":
    unittest.main()
