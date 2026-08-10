import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
EXPECTED_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8-sig").strip()


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


class DiskCleanupV13916Tests(unittest.TestCase):
    def test_versions_are_bumped_to_current_release(self):
        self.assertEqual(read(ROOT / "VERSION").strip(), EXPECTED_VERSION)
        self.assertIn(f'APP_VERSION = "{EXPECTED_VERSION}"', read(APP / "config.py"))

    def test_deploy_prunes_docker_build_cache_after_success(self):
        lib = read(ROOT / "scripts" / "distribution_lib.sh")
        deploy = lib.split("deploy_application() {", 1)[1]
        deploy = deploy.split("\n}", 1)[0]

        self.assertIn("docker builder prune -af --filter until=24h", deploy)
        self.assertLess(deploy.index("verify_container_version || die"), deploy.index("docker builder prune"))
        self.assertIn('log "清理 Docker 构建缓存（保留 24h 以内）"', deploy)


if __name__ == "__main__":
    unittest.main()
