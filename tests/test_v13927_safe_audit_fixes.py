import unittest
from pathlib import Path

from tests.test_v13871_cpu_dedup_stability import load_code_rules_with_fake_redis


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
EXPECTED_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8-sig").strip()


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


class SafeAuditFixesV13927Tests(unittest.TestCase):
    def test_versions_are_bumped_to_current_release(self):
        self.assertEqual(read(ROOT / "VERSION").strip(), EXPECTED_VERSION)
        self.assertIn(f'APP_VERSION = "{EXPECTED_VERSION}"', read(APP / "config.py"))

    def test_custom_rule_registers_all_matches(self):
        stored = [{
            "name": "多码测试",
            "pattern": r"(?:AAA|BBB)",
            "group": "0",
            "enabled": True,
            "fast": True,
            "trigger": False,
            "strict_context": False,
            "note": "",
        }]
        code_rules, _saved = load_code_rules_with_fake_redis(stored)

        identities = code_rules.extract_code_identities("AAA BBB")

        self.assertIn("code:AAA", identities)
        self.assertIn("code:BBB", identities)

    def test_field_code_is_not_polluted_by_whitelist(self):
        code_rules, _saved = load_code_rules_with_fake_redis()
        text = "\u6ce8\u518c\u7801\uff1aABC123\nX-Whitelist_1OTb0O0FMO"

        identities = code_rules.extract_code_identities(text)

        self.assertIn("field_code:ABC123", identities)
        self.assertIn("strong_whitelist:X-Whitelist_1OTb0O0FMO", identities)
        self.assertNotIn("strong_whitelist:ABC123", identities)

    def test_web_safe_fixes_are_present(self):
        web = read(APP / "web.py")
        template = read(APP / "templates" / "index.html")

        self.assertIn("removed = srem(redis_key, value)", web)
        self.assertIn("pipe = r.pipeline()", web)
        self.assertIn("for rule in value.split(\";;\")", web)
        self.assertIn("未找到对应的去重记录", web)
        self.assertIn("if len(text) > 8192:", web)
        self.assertIn("r.ping()", web)
        self.assertIn("_regex.compile(str(rule), _regex.I | _regex.M)", web)
        self.assertIn("migrate_known_regex_rules()", web)
        self.assertIn("只导入正则规则", template)

    def test_bot_and_redis_safe_fixes_are_present(self):
        runner = read(APP / "bot_runner.py")
        store = read(APP / "redis_store.py")
        dedup = read(APP / "dedup.py")

        self.assertIn('self._dedup_settings = (0.0, True, "strict", 20, 20)', runner)
        self.assertIn("failed_queue", runner)
        self.assertIn("批量写入 Redis 失败", store)
        self.assertIn('str(r.type(k) or "") == "string"', store)
        self.assertIn("return bool(deleted)", dedup)


if __name__ == "__main__":
    unittest.main()
