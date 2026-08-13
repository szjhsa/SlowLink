import hashlib
import importlib.util
import subprocess
import tempfile
import re
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DistributionSystemTests(unittest.TestCase):
    def read_required(self, relative_path: str) -> str:
        path = ROOT / relative_path
        self.assertTrue(path.is_file(), f"missing {relative_path}")
        return path.read_text(encoding="utf-8")

    def test_install_script_has_public_release_contract(self):
        install_text = self.read_required("install.sh")
        library_text = self.read_required("scripts/distribution_lib.sh")
        text = install_text + "\n" + library_text

        for fragment in (
            'REPO="szjhsa/SlowLink"',
            'INSTALL_DIR="/opt/slowlink"',
            "/dev/tty",
            "SHA256SUMS.txt",
            "sha256sum -c",
            "application/octet-stream",
            "get.docker.com",
            "docker compose version",
            "slowlink-watchdog.service",
            'docker compose build --no-cache "$APP_SERVICE"',
            'docker compose up -d --no-deps "$APP_SERVICE"',
            "1.安装",
            "2.更新到最新版本",
            "3.卸载",
            "0.退出",
        ):
            self.assertIn(fragment, text)

        self.assertIn("ubuntu|debian", text)
        self.assertIn("--version", text)
        self.assertIn("--update", text)
        self.assertNotIn("browser_download_url", text)
        self.assertNotIn("docker compose down", text)
        self.assertNotIn("docker stop slowlink_redis", text)

    def test_install_script_protects_runtime_data_before_copying(self):
        install_text = self.read_required("install.sh")
        library_text = self.read_required("scripts/distribution_lib.sh")
        text = install_text + "\n" + library_text

        for fragment in (
            ".env",
            "data/",
            ".git/",
            "session",
            "sqlite",
            "*.log",
            "backup",
        ):
            self.assertIn(fragment, text)

        guard = install_text.index('validate_release_archive "$FULL_FILE"')
        transaction_call = install_text.index('apply_release_transaction update', guard)
        self.assertLess(guard, transaction_call)

        transaction = re.search(
            r"apply_release_transaction\(\) \{(?P<body>.*?)(?=\n\})",
            install_text,
            flags=re.S,
        )
        self.assertIsNotNone(transaction)
        self.assertIn('copy_release_files "$STAGE"', transaction.group("body"))

    def test_manage_script_exposes_scoped_commands(self):
        text = self.read_required("manage.sh")

        for command in ("status", "logs", "restart", "update", "backup", "uninstall", "purge"):
            self.assertIn(f"{command})", text)
        self.assertIn("slowlink_app", text)
        self.assertIn("slowlink_redis", text)
        self.assertIn("slowlink-watchdog.service", text)
        self.assertIn("docker compose restart app", text)
        self.assertIn("wait_for_app_health", text)
        self.assertIn("redis-cli BGSAVE", text)
        self.assertIn('docker cp "$REDIS_CONTAINER:/data/dump.rdb"', text)
        self.assertIn("/var/backups/slowlink", text)
        self.assertIn('https://raw.githubusercontent.com/$REPO/main/install.sh', text)
        self.assertNotIn("docker compose down", text)

    def test_uninstall_requires_confirmation_before_destructive_actions(self):
        text = self.read_required("uninstall.sh")

        for fragment in (
            '/opt/slowlink',
            'slowlink_app',
            'slowlink_redis',
            'slowlink-watchdog.service',
            '--purge',
            '/dev/tty',
            'PURGE',
            '保留配置、Telegram Session、Redis 数据和数据库',
        ):
            self.assertIn(fragment, text)

        confirmation = text.index('[ "$answer" = "PURGE" ]')
        fixed_path_guard = text.index('[ "$INSTALL_DIR" = "/opt/slowlink" ]')
        service_stop = text.index('systemctl disable --now "$WATCHDOG_SERVICE"')
        permanent_delete = text.index('rm -rf -- "$INSTALL_DIR"')
        self.assertLess(confirmation, fixed_path_guard)
        self.assertLess(fixed_path_guard, service_stop)
        self.assertLess(service_stop, permanent_delete)
        preserve_section = text.split("# 保留数据卸载", 1)[1]
        self.assertIn('docker compose stop app', preserve_section)
        self.assertIn('docker compose rm -f app', preserve_section)
        self.assertNotIn('docker stop "$REDIS_CONTAINER"', preserve_section)
        self.assertNotIn('docker volume rm', preserve_section)

    def test_release_workflow_publishes_only_required_assets_and_uses_changelog_body(self):
        text = self.read_required(".github/workflows/release.yml")

        for fragment in (
            'tags:',
            'v*',
            'contents: write',
            'python -m unittest discover -s tests',
            'python -m compileall -q app scripts tests',
            'python scripts/build_release.py',
            'sha256sum -c SHA256SUMS.txt',
            'gh release create',
            'GH_TOKEN:',
            '--notes-file "dist/slowlink_v${file_version}_update_log.txt"',
        ):
            self.assertIn(fragment, text)

        self.assertNotIn('--generate-notes', text)

        publish_assets = text.split('gh release create', 1)[1].split('--verify-tag', 1)[0]
        self.assertIn('"dist/slowlink_app_v${file_version}.zip"', publish_assets)
        self.assertIn('"dist/slowlink_v${file_version}_full.zip"', publish_assets)
        self.assertIn('dist/SHA256SUMS.txt', publish_assets)
        self.assertNotIn('"dist/slowlink_v${file_version}_update_log.txt"', publish_assets)

    def test_readme_lists_three_release_assets_and_keeps_update_log_offline(self):
        text = self.read_required("README.md")

        self.assertIn("三个资产", text)
        self.assertIn("本地归档", text)
        self.assertNotIn("| `slowlink_v*_update_log.txt` |", text)

    def test_release_builder_creates_exact_verified_assets(self):
        builder_path = ROOT / "scripts" / "build_release.py"
        self.assertTrue(builder_path.is_file(), "missing scripts/build_release.py")

        spec = importlib.util.spec_from_file_location("slowlink_build_release", builder_path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        suffix = version.replace(".", "_")
        expected_names = (
            f"slowlink_app_v{suffix}.zip",
            f"slowlink_v{suffix}_full.zip",
            f"slowlink_v{suffix}_update_log.txt",
            "SHA256SUMS.txt",
        )
        self.assertEqual(module.file_version(version), suffix)
        self.assertEqual(module.expected_asset_names(version), expected_names)
        self.assertIn(f"## [{version}]", module.extract_changelog(version))

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            assets = module.build(version, output_dir)
            self.assertEqual(tuple(path.name for path in assets), expected_names)
            self.assertEqual({path.name for path in output_dir.iterdir()}, set(expected_names))

            app_zip, full_zip, update_log, checksum_file = assets
            with zipfile.ZipFile(app_zip) as archive:
                app_members = archive.namelist()
            with zipfile.ZipFile(full_zip) as archive:
                full_members = archive.namelist()

            self.assertTrue(app_members)
            self.assertTrue(all(name == "LICENSE" or name.startswith("app/") for name in app_members))
            self.assertIn("LICENSE", app_members)
            for required in (
                "LICENSE",
                "VERSION",
                "Dockerfile",
                "docker-compose.yml",
                "install.sh",
                "manage.sh",
                "uninstall.sh",
                "scripts/distribution_lib.sh",
            ):
                self.assertIn(required, full_members)

            forbidden_parts = (
                "/.env",
                "/data/",
                "/sessions/",
                "/.git/",
                "/backups/",
                "/backup/",
                "/__pycache__/",
            )
            for member in app_members + full_members:
                normalized = f"/{member.lower()}"
                self.assertNotEqual(normalized, "/.env", member)
                self.assertNotIn("/.env/", normalized, member)
                self.assertFalse(any(part in normalized for part in forbidden_parts[1:]), member)
                self.assertFalse(normalized.endswith((".session", ".sqlite", ".sqlite3", ".db", ".rdb", ".log")), member)

            self.assertIn(f"## [{version}]", update_log.read_text(encoding="utf-8"))
            checksum_lines = checksum_file.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(checksum_lines), 2)
            self.assertEqual(
                {line.split("  ", 1)[1] for line in checksum_lines},
                {app_zip.name, full_zip.name},
            )
            for line in checksum_lines:
                digest, name = line.split("  ", 1)
                payload = (output_dir / name).read_bytes()
                self.assertEqual(hashlib.sha256(payload).hexdigest(), digest)

    def test_repository_root_ignores_runtime_data_without_requiring_local_deletion(self):
        version_dirs = [path.name for path in ROOT.iterdir() if path.is_dir() and path.name.startswith("V1.")]
        deploy_archives = [path.name for path in ROOT.iterdir() if path.is_file() and path.suffix.lower() == ".zip"]

        self.assertEqual(version_dirs, [])
        self.assertEqual(deploy_archives, [])

        tracked = subprocess.run(
            ["git", "ls-files"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        forbidden_prefixes = ("data/", "sessions/", "backups/", "backup/", "secrets/")
        forbidden_suffixes = (".session", ".sqlite", ".sqlite3", ".db", ".rdb", ".log", ".token", ".secret", ".pem", ".key")
        for path in tracked:
            self.assertNotEqual(path, ".env", path)
            self.assertNotEqual(path, "credentials.json", path)
            self.assertFalse(path.startswith(forbidden_prefixes), path)
            self.assertFalse(path.endswith(forbidden_suffixes), path)

        ignored_paths = (
            ".env",
            ".env.production",
            "data/runtime.sqlite",
            "sessions/account.session",
            "watchdog.log",
            "backups/slowlink.tar.gz",
            "secrets/token.txt",
            "token.secret",
            "private.key",
            "credentials.json",
        )
        for path in ignored_paths:
            ignored = subprocess.run(
                ["git", "check-ignore", "--quiet", "--no-index", path],
                cwd=ROOT,
            )
            self.assertEqual(ignored.returncode, 0, path)


if __name__ == "__main__":
    unittest.main()
