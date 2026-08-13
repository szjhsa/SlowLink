import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    path = ROOT / relative
    if not path.exists():
        fallback = ROOT / "deploy" / relative
        if fallback.exists():
            path = fallback
    return path.read_text(encoding="utf-8-sig")


class HttpsDistributionV1392Tests(unittest.TestCase):
    def test_runtime_dependencies_pin_the_verified_telethon_release(self):
        requirements = read("requirements.txt").splitlines()

        self.assertIn("telethon==1.44.0", requirements)
        self.assertFalse(any(line.startswith("telethon>=") for line in requirements))

    def test_compose_keeps_http_compatibility_and_adds_optional_caddy(self):
        compose = read("docker-compose.yml")

        self.assertIn(
            '"${SLOWLINK_BIND_HOST:-0.0.0.0}:${SLOWLINK_WEB_PORT:-8080}:8080"',
            compose,
        )
        self.assertIn("caddy:", compose)
        self.assertIn("image: caddy:2.10.2-alpine", compose)
        self.assertIn("container_name: slowlink_caddy", compose)
        self.assertIn('profiles: ["https"]', compose)
        self.assertIn('SLOWLINK_DOMAIN: "${SLOWLINK_DOMAIN:-}"', compose)
        self.assertIn('"80:80"', compose)
        self.assertIn('"443:443"', compose)
        self.assertIn('"443:443/udp"', compose)
        self.assertIn("../deploy/ops/Caddyfile:/etc/caddy/Caddyfile:ro", compose)
        self.assertIn("caddy_data:/data", compose)
        self.assertIn("caddy_config:/config", compose)

    def test_caddy_only_proxies_the_internal_app_service(self):
        caddyfile = read("ops/Caddyfile")

        self.assertIn("{$SLOWLINK_DOMAIN}", caddyfile)
        self.assertIn("reverse_proxy app:8080", caddyfile)
        self.assertEqual(caddyfile.splitlines()[0], "{$SLOWLINK_DOMAIN} {")
        self.assertNotRegex(caddyfile, r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

    def test_example_environment_documents_both_access_modes(self):
        example = read(".env.example")

        self.assertIn("SLOWLINK_WEB_MODE=http", example)
        self.assertIn("SLOWLINK_BIND_HOST=0.0.0.0", example)
        self.assertIn("SLOWLINK_WEB_PORT=8080", example)
        self.assertIn("SLOWLINK_DOMAIN=", example)

    def test_distribution_library_owns_atomic_web_access_configuration(self):
        library = read("scripts/distribution_lib.sh")

        for function in (
            "validate_web_mode",
            "validate_web_domain",
            "validate_web_bind_host",
            "validate_https_app_port",
            "read_web_mode",
            "read_web_domain",
            "read_web_bind_host",
            "save_web_access",
            "assert_https_ports_available",
            "wait_for_https_health",
            "ensure_web_proxy",
            "web_access_url",
        ):
            self.assertRegex(library, rf"(?m)^{function}\(\) \{{")

        save = re.search(
            r"save_web_access\(\) \{(?P<body>.*?)(?=\n\})",
            library,
            flags=re.S,
        )
        self.assertIsNotNone(save)
        for key in (
            "SLOWLINK_WEB_MODE",
            "SLOWLINK_BIND_HOST",
            "SLOWLINK_WEB_PORT",
            "SLOWLINK_DOMAIN",
        ):
            self.assertIn(key, save.group("body"))
        self.assertIn("mktemp", save.group("body"))
        self.assertIn("mv -f", save.group("body"))
        self.assertIn("validate_https_app_port", save.group("body"))

        bind_reader = re.search(
            r"read_web_bind_host\(\) \{(?P<body>.*?)(?=\n\})",
            library,
            flags=re.S,
        )
        self.assertIsNotNone(bind_reader)
        self.assertIn("read_env_value SLOWLINK_BIND_HOST", bind_reader.group("body"))

    def test_https_rejects_app_ports_reserved_for_the_proxy(self):
        library = read("scripts/distribution_lib.sh")
        validator = re.search(
            r"validate_https_app_port\(\) \{(?P<body>.*?)(?=\n\})",
            library,
            flags=re.S,
        )

        self.assertIsNotNone(validator)
        self.assertIn("80|443", validator.group("body"))

    def test_port_ownership_requires_a_running_compose_service(self):
        library = read("scripts/distribution_lib.sh")
        for function, service in (("app_owns_port", "app"), ("caddy_owns_port", "caddy")):
            owner = re.search(
                rf"{function}\(\) \{{(?P<body>.*?)(?=\n\}})",
                library,
                flags=re.S,
            )
            self.assertIsNotNone(owner)
            self.assertIn(".State.Running", owner.group("body"))
            self.assertIn("com.docker.compose.project", owner.group("body"))
            self.assertIn("com.docker.compose.service", owner.group("body"))
            self.assertIn(service, owner.group("body"))

    def test_https_proxy_start_is_idempotent_and_never_stops_other_services(self):
        library = read("scripts/distribution_lib.sh")
        proxy = re.search(
            r"ensure_web_proxy\(\) \{(?P<body>.*?)(?=\n\})",
            library,
            flags=re.S,
        )

        self.assertIsNotNone(proxy)
        body = proxy.group("body")
        self.assertIn("caddy_is_running", body)
        self.assertIn("current_caddy_domain", body)
        self.assertIn("保持现有 Caddy 容器", body)
        self.assertIn('compose --profile https up -d --no-deps "$CADDY_SERVICE"', body)
        self.assertNotIn("docker compose down", body)
        self.assertNotIn("REDIS_CONTAINER", body)
        self.assertNotIn("assistant", body.lower())

    def test_https_health_wait_uses_a_wall_clock_deadline(self):
        library = read("scripts/distribution_lib.sh")
        waiter = re.search(
            r"wait_for_https_health\(\) \{(?P<body>.*?)(?=\n\})",
            library,
            flags=re.S,
        )

        self.assertIsNotNone(waiter)
        body = waiter.group("body")
        self.assertIn("https_deadline", body)
        self.assertIn("date +%s", body)

    def test_installer_prompts_for_https_or_http_and_preserves_mode_on_update(self):
        install = read("install.sh")

        self.assertIn("1.域名 HTTPS（推荐）", install)
        self.assertIn("2.IP + 自定义端口 HTTP", install)
        self.assertIn("--domain DOMAIN", install)
        self.assertIn("--http", install)
        self.assertIn("select_web_access", install)
        self.assertIn("ORIGINAL_WEB_MODE", install)
        self.assertIn("ORIGINAL_BIND_HOST", install)
        self.assertIn("ORIGINAL_DOMAIN", install)
        self.assertIn(
            'save_web_access "$SLOWLINK_WEB_MODE" "$SLOWLINK_WEB_PORT" "$SLOWLINK_DOMAIN"',
            install,
        )
        self.assertIn("ensure_web_proxy", install)
        self.assertNotIn("docker compose down", install)

    def test_manage_web_command_can_switch_modes_without_touching_redis(self):
        manage = read("manage.sh")

        self.assertIn("web        切换域名 HTTPS 或 IP + 端口 HTTP", manage)
        self.assertIn("web)", manage)
        self.assertIn("configure_web_access", manage)
        self.assertIn("1.域名 HTTPS（推荐）", manage)
        self.assertIn("2.IP + 自定义端口 HTTP", manage)
        self.assertNotIn("docker compose down", manage)

        switcher = re.search(
            r"configure_web_access\(\) \{(?P<body>.*?)(?=\n\})",
            manage,
            flags=re.S,
        )
        self.assertIsNotNone(switcher)
        self.assertNotIn("REDIS_CONTAINER", switcher.group("body"))
        self.assertIn('docker compose --env-file "$INSTALL_DIR/.env" -f "$INSTALL_DIR/deploy/docker-compose.yml" up -d --no-deps "$APP_SERVICE"', switcher.group("body"))

    def test_uninstall_removes_only_slowlink_caddy_and_preserves_data_by_default(self):
        uninstall = read("uninstall.sh")

        self.assertIn('CADDY_CONTAINER="slowlink_caddy"', uninstall)
        self.assertIn("container_is_slowlink_service", uninstall)
        self.assertIn("volume_is_slowlink_owned", uninstall)
        self.assertIn('--profile https stop caddy', uninstall)
        self.assertIn('--profile https rm -f caddy', uninstall)

        preserve = uninstall.split("# 保留数据卸载", 1)[1]
        self.assertIn('remove_slowlink_container "$APP_CONTAINER" app', preserve)
        self.assertIn('remove_slowlink_container "$CADDY_CONTAINER" caddy', preserve)
        self.assertNotIn('docker stop "$REDIS_CONTAINER"', preserve)
        self.assertNotIn("docker volume rm", preserve)

        purge = uninstall.split("# 保留数据卸载", 1)[0]
        self.assertIn('"$redis_data_volume" slowlink_redis_data', purge)
        self.assertIn("com.docker.compose.volume", purge)
        self.assertIn("caddy_data", purge)
        self.assertIn("caddy_config", purge)

    def test_older_http_releases_remain_installable_but_cannot_enable_https(self):
        library = read("scripts/distribution_lib.sh")
        install = read("install.sh")
        extractor = re.search(
            r"extract_release_archive\(\) \{(?P<body>.*?)(?=\n\})",
            library,
            flags=re.S,
        )

        self.assertIsNotNone(extractor)
        self.assertNotIn("ops/Caddyfile", extractor.group("body"))
        self.assertIn('[ ! -f "$STAGE/deploy/ops/Caddyfile" ]', install)
        self.assertIn("不支持域名 HTTPS", install)

    def test_full_release_contains_the_caddy_configuration(self):
        builder = read("scripts/build_release.py")

        self.assertIn('"deploy",', builder)
        self.assertTrue((ROOT / "deploy" / "ops" / "Caddyfile").is_file())


if __name__ == "__main__":
    unittest.main()
