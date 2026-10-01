import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"

INVITE_REGEX = r"(?i)https?://[^\s/]+/invite/(?:[a-z0-9]{6}|[a-z0-9]{8})(?![a-z0-9])"
INVITE_URLS = (
    "https://ep.whooping.top/invite/6153c0",
    "https://ep.whooping.top/invite/be9dbe",
    "https://ep.whooping.top/invite/486549",
)
EIGHT_CHARACTER_INVITE_URLS = (
    "https://ue2.taotu.ink/invite/5fbbddb9",
    "https://ue2.taotu.ink/invite/11bd3b99",
    "https://ue2.taotu.ink/invite/c7f82113",
)


def load_matcher_modules(regex_rules=()):
    fake_store = types.ModuleType("redis_store")
    fake_store.get_json = lambda _key, default=None: default
    fake_store.set_json = lambda *_args, **_kwargs: None
    fake_store.smembers = lambda key: set(regex_rules) if key == "regex_rules" else set()

    module_names = ("redis_store", "code_rules", "matcher")
    old_modules = {name: sys.modules.get(name) for name in module_names}
    sys.modules["redis_store"] = fake_store
    for name in module_names[1:]:
        sys.modules.pop(name, None)
    sys.path.insert(0, str(APP))
    try:
        import code_rules
        import matcher

        return code_rules, matcher
    finally:
        sys.path.remove(str(APP))
        for name, old in old_modules.items():
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old


def load_dedup():
    fake_store = types.ModuleType("redis_store")
    fake_store.r = None
    fake_store.sha = lambda value: value
    fake_store.format_time = lambda: ""

    module_names = ("redis_store", "dedup")
    old_modules = {name: sys.modules.get(name) for name in module_names}
    sys.modules["redis_store"] = fake_store
    sys.modules.pop("dedup", None)
    sys.path.insert(0, str(APP))
    try:
        import dedup

        return dedup
    finally:
        sys.path.remove(str(APP))
        for name, old in old_modules.items():
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old


class InvitePathCodesV1393Tests(unittest.TestCase):
    def test_six_character_invite_path_codes_are_extracted_for_dedup(self):
        code_rules, _matcher = load_matcher_modules()

        for url in INVITE_URLS:
            with self.subTest(url=url):
                code = url.rsplit("/", 1)[-1]
                detail = code_rules.extract_code_detail(url)

                self.assertEqual(detail.get("code"), code)
                self.assertEqual(detail.get("identity"), "url_invite:" + code)
                self.assertFalse(detail.get("can_trigger"))

    def test_eight_character_invite_path_codes_are_extracted_for_dedup(self):
        code_rules, _matcher = load_matcher_modules()

        for url in EIGHT_CHARACTER_INVITE_URLS:
            with self.subTest(url=url):
                code = url.rsplit("/", 1)[-1]
                detail = code_rules.extract_code_detail(url)

                self.assertEqual(detail.get("code"), code)
                self.assertEqual(detail.get("identity"), "url_invite:" + code)
                self.assertFalse(detail.get("can_trigger"))

    def test_plugin_invite_rule_triggers_without_a_user_regex(self):
        code_rules, matcher = load_matcher_modules()

        for url in (INVITE_URLS[0], EIGHT_CHARACTER_INVITE_URLS[0]):
            with self.subTest(url=url):
                self.assertEqual(code_rules.extract_trigger_code_detail(url), {})
                result = matcher.analyze_message(url)
                self.assertTrue(result.get("matched"))
                self.assertTrue(str(result.get("rule") or "").startswith("plugin:"))

    def test_configured_user_regex_still_takes_precedence(self):
        _code_rules, matcher = load_matcher_modules(regex_rules={INVITE_REGEX})

        result = matcher.analyze_message(EIGHT_CHARACTER_INVITE_URLS[2])

        self.assertTrue(result.get("matched"))
        self.assertEqual(result.get("rule"), INVITE_REGEX)
        self.assertEqual(result.get("code_detail", {}).get("code"), "c7f82113")

    def test_only_exact_six_or_eight_character_alphanumeric_path_codes_are_extracted(self):
        code_rules, _matcher = load_matcher_modules()
        rejected = (
            "https://example.com/invite/abc12",
            "https://example.com/invite/abc1234",
            "https://example.com/invite/abc123456",
            "https://example.com/invite/abc_12",
            "https://example.com/invites/abc123",
        )

        for url in rejected:
            with self.subTest(url=url):
                self.assertEqual(code_rules.extract_code_detail(url), {})

    def test_text_dedup_preserves_tail_identity_and_ignores_the_domain(self):
        dedup = load_dedup()
        first = dedup.normalize_for_text_dedup(INVITE_URLS[0])
        second = dedup.normalize_for_text_dedup(INVITE_URLS[1])
        same_code_other_domain = dedup.normalize_for_text_dedup(
            "https://another.example/invite/6153c0"
        )

        self.assertNotEqual(first, second)
        self.assertEqual(first, same_code_other_domain)
        self.assertIn("invite path code fingerprints", first)

    def test_eight_character_text_dedup_uses_the_tail_across_domains(self):
        dedup = load_dedup()
        first = dedup.normalize_for_text_dedup(EIGHT_CHARACTER_INVITE_URLS[0])
        second = dedup.normalize_for_text_dedup(EIGHT_CHARACTER_INVITE_URLS[1])
        same_code_other_domain = dedup.normalize_for_text_dedup(
            "https://another.example/invite/5fbbddb9"
        )

        self.assertNotEqual(first, second)
        self.assertEqual(first, same_code_other_domain)
        self.assertIn("invite path code fingerprints", first)


if __name__ == "__main__":
    unittest.main()
