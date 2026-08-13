import contextlib
import io
import re
import sys
import time
import types
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"


def read(relative: str) -> str:
    path = ROOT / relative
    if not path.exists():
        fallback = ROOT / "deploy" / relative
        if fallback.exists():
            path = fallback
    return path.read_text(encoding="utf-8-sig")


class _FailingPipeline:
    def lpush(self, *args, **kwargs):
        return self

    def ltrim(self, *args, **kwargs):
        return self

    def execute(self):
        raise ConnectionError("redis unavailable")


class _FailingRedis:
    def get(self, *args, **kwargs):
        raise ConnectionError("redis unavailable")

    def pipeline(self):
        return _FailingPipeline()


class _TimezoneRedis:
    def __init__(self, timezone_name: str):
        self.timezone_name = timezone_name
        self.get_calls = 0

    def get(self, key):
        self.get_calls += 1
        return self.timezone_name


class InstallerAndRuntimeHardeningV13887Tests(unittest.TestCase):
    def test_compose_uses_persistent_custom_host_port_and_healthy_redis(self):
        compose = read("docker-compose.yml")
        self.assertIn('${SLOWLINK_WEB_PORT:-8080}:8080', compose)
        self.assertNotIn('"8080:8080"', compose)
        self.assertRegex(compose, r"depends_on:\s+redis:\s+condition: service_healthy")

    def test_installer_supports_port_flag_and_same_line_prompts(self):
        install = read("install.sh")
        uninstall = read("uninstall.sh")

        self.assertIn("--port", install)
        self.assertIn("REQUESTED_PORT", install)
        self.assertIn("select_web_port", install)
        self.assertIn("save_web_access", install)
        self.assertIn("printf '请选择：' > /dev/tty", install)
        self.assertIn("printf '网页端口 [默认 %s]：'", install)
        self.assertNotRegex(install, r"请选择：\nEOF")
        self.assertIn("printf '请输入 PURGE 确认：' > /dev/tty", uninstall)

    def test_distribution_library_preflights_port_and_keeps_existing_service(self):
        library = read("scripts/distribution_lib.sh")

        for fragment in (
            "validate_web_port",
            "read_web_port",
            "save_web_port",
            "port_in_use",
            "describe_port_owner",
            "assert_web_port_available",
            "当前端口已被占用",
            "不会停止占用端口的现有服务",
        ):
            self.assertIn(fragment, library)
        self.assertNotIn("fuser -k", library)
        self.assertNotIn("kill -9", library)

    def test_update_has_program_rollback_and_separate_build_start_diagnostics(self):
        install = read("install.sh")
        library = read("scripts/distribution_lib.sh")

        self.assertIn("backup_program_files", install)
        self.assertIn("restore_program_files", install)
        self.assertIn("rollback_previous_release", install)
        self.assertIn('compose build --no-cache "$APP_SERVICE"', library)
        self.assertIn('compose up -d --no-deps "$APP_SERVICE"', library)
        self.assertIn("slowlink_app 镜像构建失败", library)
        self.assertIn("slowlink_app 容器启动失败", library)
        self.assertIn("wait_for_redis_health", library)

    def test_release_copy_and_port_persistence_are_covered_by_rollback(self):
        install = read("install.sh")
        select_port = re.search(
            r"select_web_port\(\) \{(?P<body>.*?)(?=\n\})",
            install,
            flags=re.S,
        )
        self.assertIsNotNone(select_port)
        self.assertNotIn("save_web_port", select_port.group("body"))

        transaction = re.search(
            r"apply_release_transaction\(\) \{(?P<body>.*?)(?=\n\})",
            install,
            flags=re.S,
        )
        self.assertIsNotNone(transaction)
        transaction_body = transaction.group("body")
        self.assertIn('copy_release_files "$STAGE"', transaction_body)
        self.assertIn(
            'save_web_access "$SLOWLINK_WEB_MODE" "$SLOWLINK_WEB_PORT" "$SLOWLINK_DOMAIN"',
            transaction_body,
        )
        self.assertIn('perform_installation "$transaction_mode"', transaction_body)
        self.assertLess(
            transaction_body.index('copy_release_files "$STAGE"'),
            transaction_body.index('perform_installation "$transaction_mode"'),
        )

        rollback = re.search(
            r"rollback_previous_release\(\) \{(?P<body>.*?)(?=\n\})",
            install,
            flags=re.S,
        )
        self.assertIsNotNone(rollback)
        rollback_body = rollback.group("body")
        self.assertIn(
            'save_web_access "$ORIGINAL_WEB_MODE" "$ORIGINAL_PORT" "$ORIGINAL_DOMAIN" "$ORIGINAL_BIND_HOST"',
            rollback_body,
        )
        self.assertIn("SLOWLINK_WEB_MODE=$ORIGINAL_WEB_MODE", rollback_body)
        self.assertIn("SLOWLINK_BIND_HOST=$ORIGINAL_BIND_HOST", rollback_body)
        self.assertIn("SLOWLINK_WEB_PORT=$ORIGINAL_PORT", rollback_body)
        self.assertIn("SLOWLINK_DOMAIN=$ORIGINAL_DOMAIN", rollback_body)
        self.assertIn(
            "export SLOWLINK_WEB_MODE SLOWLINK_BIND_HOST SLOWLINK_WEB_PORT SLOWLINK_DOMAIN",
            rollback_body,
        )

        update_start = install.rfind('if [ "$UPDATE_ONLY" -eq 1 ]; then')
        self.assertGreaterEqual(update_start, 0)
        update_failure_body = install[update_start:]
        self.assertIn("KEEP_TMP_DIR=1", update_failure_body)
        self.assertIn("KEEP_TMP_DIR=0", update_failure_body)
        self.assertNotIn("rollback_previous_release || true", update_failure_body)

        cleanup = re.search(
            r"cleanup\(\) \{(?P<body>.*?)(?=\n\})",
            install,
            flags=re.S,
        )
        self.assertIsNotNone(cleanup)
        self.assertIn('"$KEEP_TMP_DIR" -ne 1', cleanup.group("body"))
        self.assertIn("旧程序备份保留在", install)

    def test_existing_app_cannot_use_unprotected_install_mode(self):
        install = read("install.sh")
        guard = re.search(
            r"ensure_docker \"\$TMP_DIR\"(?P<body>.*?)(?=\nORIGINAL_PORT=)",
            install,
            flags=re.S,
        )
        self.assertIsNotNone(guard)
        body = guard.group("body")
        self.assertIn('"$UPDATE_ONLY" -eq 0', body)
        self.assertIn('docker inspect "$APP_CONTAINER"', body)
        self.assertIn("请使用更新", body)

    def test_failed_fresh_install_removes_only_the_app_and_transactions_fail_closed(self):
        install = read("install.sh")
        library = read("scripts/distribution_lib.sh")

        cleanup = re.search(
            r"cleanup_failed_install\(\) \{(?P<body>.*?)(?=\n\})",
            install,
            flags=re.S,
        )
        self.assertIsNotNone(cleanup)
        cleanup_body = cleanup.group("body")
        self.assertIn('docker rm -f "$APP_CONTAINER"', cleanup_body)
        self.assertNotIn("REDIS_CONTAINER", cleanup_body)
        self.assertNotIn("docker compose down", cleanup_body)

        copy_release = re.search(
            r"copy_release_files\(\) \{(?P<body>.*?)(?=\n\})",
            library,
            flags=re.S,
        )
        self.assertIsNotNone(copy_release)
        copy_body = copy_release.group("body")
        for guarded_step in (
            'mkdir -p "$INSTALL_DIR" "$INSTALL_DIR/data/sessions" || die',
            'rm -rf -- "$INSTALL_DIR/$program_path" || die',
            'find "$INSTALL_DIR/app" -type f -exec touch {} + || die',
        ):
            self.assertIn(guarded_step, copy_body)

        watchdog = re.search(
            r"install_watchdog\(\) \{(?P<body>.*?)(?=\n\})",
            library,
            flags=re.S,
        )
        self.assertIsNotNone(watchdog)
        self.assertIn('|| die "CPU watchdog 服务文件安装失败"', watchdog.group("body"))
        self.assertIn('|| die "systemd 配置刷新失败"', watchdog.group("body"))

    def test_dependency_install_can_skip_apt_and_status_shows_web_port(self):
        library = read("scripts/distribution_lib.sh")
        manage = read("manage.sh")

        self.assertIn("基础工具已就绪，跳过 APT", library)
        self.assertIn("SLOWLINK_WEB_PORT", manage)
        self.assertIn("网页地址", manage)

    def test_diagnostic_redis_writes_are_best_effort(self):
        fake_redis_module = types.ModuleType("redis")
        fake_redis_module.Redis = lambda *args, **kwargs: _FailingRedis()
        old_redis_module = sys.modules.get("redis")
        sys.modules["redis"] = fake_redis_module
        sys.path.insert(0, str(APP))
        try:
            sys.modules.pop("redis_store", None)
            import redis_store

            redis_store.r = _FailingRedis()
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                redis_store.push_event("warning", "test event")
                redis_store.add_hit({"source": "test", "status": "sent"})
                redis_store.add_fail({"stage": "test", "error": "boom"})
            self.assertIn("test event", output.getvalue())
        finally:
            sys.modules.pop("redis_store", None)
            if old_redis_module is None:
                sys.modules.pop("redis", None)
            else:
                sys.modules["redis"] = old_redis_module
            try:
                sys.path.remove(str(APP))
            except ValueError:
                pass

    def test_listener_supervises_workers_and_reconnect_waits_for_active_jobs(self):
        runner = read("app/bot_runner.py")

        for fragment in (
            "self._active_jobs = 0",
            "def worker_status(self)",
            "def _ensure_workers(self, client",
            "self._active_jobs += 1",
            "self._active_jobs = max(0, self._active_jobs - 1)",
            "self._reconnect_in_progress = False",
            "async def _wait_for_reconnect(self)",
        ):
            self.assertIn(fragment, runner)
        periodic = re.search(
            r"# Proactive reconnect every 2h(?P<body>.*?)(?=# No message for 30 min)",
            runner,
            flags=re.S,
        )
        self.assertIsNotNone(periodic)
        self.assertIn("self._active_jobs == 0", periodic.group("body"))
        self.assertIn("if self._reconnect_requested:", runner)
        self.assertIn("self._reconnect_in_progress = True", runner)
        self.assertIn("while self._active_jobs > 0", runner)
        self.assertIn("self._reconnect_in_progress = False", runner)

        for start, end in (
            ("async def _priority_worker", "async def _normal_worker"),
            ("async def _normal_worker", "async def _handle_message"),
            ("async def _retry_failed_queue", "def _trim_caches"),
        ):
            worker = re.search(rf"{start}.*?(?=\n\s+{end})", runner, flags=re.S)
            self.assertIsNotNone(worker)
            self.assertIn("await self._wait_for_reconnect()", worker.group(0))

        retry_worker = re.search(
            r"async def _retry_failed_queue\(.*?\n    def _trim_caches",
            runner,
            flags=re.S,
        )
        self.assertIsNotNone(retry_worker)
        retry_body = retry_worker.group(0)
        self.assertIn("self._active_jobs += 1", retry_body)
        self.assertIn("self._active_jobs = max(0, self._active_jobs - 1)", retry_body)

    def test_health_checks_redis_listener_and_worker_state(self):
        web = read("app/web.py")
        health = re.search(
            r"def health\(\):(?P<body>.*?)(?=\n\n@app\.get\(\"/api/state\"\))",
            web,
            flags=re.S,
        )
        self.assertIsNotNone(health)
        body = health.group("body")
        self.assertIn("r.ping()", body)
        self.assertIn("manager.worker_status()", body)
        self.assertIn("manager.started_ok", body)
        self.assertIn('workers["expected"] > 0', body)
        self.assertIn("listener_desired_state", body)
        self.assertIn("else:\n        listener_ok = False", body)
        self.assertIn("client_connected", body)
        self.assertIn("heartbeat_age", body)
        self.assertIn('desired == "stopped"', body)
        self.assertIn("not listener_running", body)
        self.assertIn("503", body)

    def test_hot_path_defers_time_formatting_and_reuses_normalized_text(self):
        store = read("app/redis_store.py")
        runner = read("app/bot_runner.py")
        matcher = read("app/matcher.py")

        self.assertIn("_TIMEZONE_CACHE", store)
        self.assertIn("def clear_timezone_cache", store)
        perf_init = re.search(
            r"perf = \{(?P<body>.*?)\n\s+\}\n\s+"
            r"(?:reserved_code_keys = \[\]\n\s+dedup_profile = None\n\s+)?try:",
            runner,
            flags=re.S,
        )
        self.assertIsNotNone(perf_init)
        self.assertNotIn("format_time", perf_init.group("body"))
        self.assertIn('perf.update({', runner)
        self.assertIn('compact = re.sub(r"\\s+", "", normalized)', matcher)

    def test_clear_timezone_cache_forces_the_next_read_even_soon_after_boot(self):
        fake_redis_module = types.ModuleType("redis")
        fake_redis_module.Redis = lambda *args, **kwargs: _TimezoneRedis("UTC")
        old_redis_module = sys.modules.get("redis")
        sys.modules["redis"] = fake_redis_module
        sys.path.insert(0, str(APP))
        try:
            sys.modules.pop("redis_store", None)
            import redis_store

            timezone_redis = _TimezoneRedis("Asia/Shanghai")
            redis_store.r = timezone_redis
            redis_store._TIMEZONE_CACHE.update({"ts": 20.0, "value": "UTC"})
            with mock.patch.object(time, "monotonic", return_value=30.0):
                redis_store.clear_timezone_cache()
                rendered = redis_store.format_time(0)
            self.assertEqual(rendered, "1970-01-01 08:00:00")
            self.assertEqual(timezone_redis.get_calls, 1)
        finally:
            sys.modules.pop("redis_store", None)
            if old_redis_module is None:
                sys.modules.pop("redis", None)
            else:
                sys.modules["redis"] = old_redis_module
            try:
                sys.path.remove(str(APP))
            except ValueError:
                pass

    def test_periodic_reconnect_does_not_scan_expiring_dedup_keys(self):
        runner = read("app/bot_runner.py")
        reconnect = re.search(
            r"async def _reconnect_listener_client\(.*?(?=\n\s+async def _priority_worker)",
            runner,
            flags=re.S,
        )
        self.assertIsNotNone(reconnect)
        self.assertNotIn("cleanup_expired_dedup_keys", reconnect.group(0))


if __name__ == "__main__":
    unittest.main()
