import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
OPS = ROOT / "deploy" / "ops"
EXPECTED_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8-sig").strip()


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


class WatchdogAutoRestoreV13866Tests(unittest.TestCase):
    def test_versions_are_bumped_to_current_release(self):
        self.assertEqual(read(ROOT / "VERSION").strip(), EXPECTED_VERSION)
        self.assertIn(f'APP_VERSION = "{EXPECTED_VERSION}"', read(APP / "config.py"))

    def test_listener_desired_state_is_persistent_and_restored_after_container_restart(self):
        redis_store = read(APP / "redis_store.py")
        web = read(APP / "web.py")

        self.assertIn('"listener_desired_state": "stopped"', redis_store)
        self.assertIn("def _restore_listener_after_startup()", web)
        self.assertIn('get("listener_desired_state", "stopped")', web)
        self.assertIn("manager.start()", web)
        self.assertIn("threading.Thread(target=_restore_listener_after_startup", web)
        self.assertIn('name="slowlink-restore-listener"', web)
        self.assertNotIn('push_event("info", f"容器启动后自动恢复监听：{msg}")', web)
        self.assertIn('set_value("listener_desired_state", "running")', web)
        self.assertIn('set_value("listener_desired_state", "stopped")', web)

    def test_startup_does_not_overwrite_desired_state_with_stopped(self):
        web = read(APP / "web.py")
        self.assertNotIn('set_value("bot_status", "stopped")\n\n\n@app.context_processor', web)
        self.assertIn('set_value("bot_status", "starting")', web)

    def test_watchdog_restarts_only_after_sustained_container_cpu(self):
        script = read(OPS / "slowlink_watchdog.sh")
        service = read(OPS / "slowlink-watchdog.service")

        self.assertIn('APP_CONTAINER="${APP_CONTAINER:-slowlink_app}"', script)
        self.assertIn('CHECK_INTERVAL="${CHECK_INTERVAL:-10}"', script)
        self.assertIn('CPU_THRESHOLD="${CPU_THRESHOLD:-80}"', script)
        self.assertIn('HIGH_COUNT_LIMIT="${HIGH_COUNT_LIMIT:-4}"', script)
        self.assertIn('COOLDOWN_SECONDS="${COOLDOWN_SECONDS:-600}"', script)
        self.assertIn("docker stats --no-stream", script)
        self.assertIn('docker stats --no-stream "$APP_CONTAINER" "$REDIS_CONTAINER"', script)
        self.assertIn('ps -eo pid,tid,ppid,comm,%cpu,%mem,etime --sort=-%cpu', script)
        self.assertIn('docker restart "$APP_CONTAINER"', script)
        self.assertIn("snapshot", script)
        self.assertIn("ExecStart=/bin/sh /opt/slowlink/deploy/ops/slowlink_watchdog.sh", service)
        self.assertIn("Environment=CHECK_INTERVAL=10", service)
        self.assertIn("Environment=CPU_THRESHOLD=80", service)
        self.assertIn("Restart=always", service)


if __name__ == "__main__":
    unittest.main()
