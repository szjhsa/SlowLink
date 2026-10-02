import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"

FIRST_BATCH = "\n".join([
    "https://t.me/Moonkkbot?start=30-Register_seeuhmOtV8",
    "https://t.me/Moonkkbot?start=30-Register_5MqeelLid2",
    "https://t.me/Moonkkbot?start=30-Register_n4CKbGXUgr",
    "https://t.me/Moonkkbot?start=30-Register_D6yUFCNnIA",
    "https://t.me/Moonkkbot?start=30-Register_FUaNEyHb0b",
    "https://t.me/Moonkkbot?start=30-Register_StwpKkMDOD",
    "https://t.me/Moonkkbot?start=30-Register_trl4Ps2fjo",
    "https://t.me/Moonkkbot?start=30-Register_n8qO1BGz8A",
    "https://t.me/Moonkkbot?start=30-Register_yNccjh789d",
    "https://t.me/Moonkkbot?start=30-Register_v3QQbVcofR",
])
SECOND_BATCH = "\n".join([
    "https://t.me/Moonkkbot?start=30-Register_AAAAAAAAAA",
    "https://t.me/Moonkkbot?start=30-Register_BBBBBBBBBB",
    "https://t.me/Moonkkbot?start=30-Register_CCCCCCCCCC",
])


def load_matcher_modules(regex_rules=()):
    fake_store = types.ModuleType("redis_store")
    fake_store.get_json = lambda _key, default=None: default
    fake_store.set_json = lambda *_args, **_kwargs: None
    fake_store.smembers = lambda key: set(regex_rules) if key == "regex_rules" else set()

    module_names = ("redis_store", "code_rules", "matcher", "telegram_start_links")
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

    module_names = ("redis_store", "dedup", "telegram_start_links")
    old_modules = {name: sys.modules.get(name) for name in module_names}
    sys.modules["redis_store"] = fake_store
    for name in module_names[1:]:
        sys.modules.pop(name, None)
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


class TelegramStartLinksV13898Tests(unittest.TestCase):
    def test_days_first_register_start_link_triggers_and_preserves_identity(self):
        code_rules, matcher = load_matcher_modules(
            regex_rules={r"https?://(?:t\.me|telegram\.me)/Moonkkbot\?start="}
        )

        detail = code_rules.extract_code_detail(FIRST_BATCH)
        result = matcher.analyze_message(FIRST_BATCH)

        self.assertEqual(detail.get("code"), "30-Register_seeuhmOtV8")
        self.assertEqual(
            detail.get("identity"),
            "strong_register_renew:30-Register_seeuhmOtV8",
        )
        self.assertTrue(result.get("matched"))
        self.assertEqual(result.get("code_detail", {}).get("code"), detail.get("code"))

    def test_telegram_me_renew_start_link_is_supported(self):
        code_rules, matcher = load_matcher_modules(
            regex_rules={r"https?://(?:t\.me|telegram\.me)/Moonkkbot\?start="}
        )
        text = "https://telegram.me/Moonkkbot?start=30-Renew_seeuhmOtV8"

        self.assertEqual(
            code_rules.extract_code_detail(text).get("code"),
            "30-Renew_seeuhmOtV8",
        )
        self.assertTrue(matcher.analyze_message(text).get("matched"))

    def test_days_first_payload_outside_a_telegram_bot_link_does_not_trigger(self):
        code_rules, matcher = load_matcher_modules()

        self.assertEqual(code_rules.extract_code_detail("30-Register_seeuhmOtV8"), {})
        self.assertFalse(matcher.analyze_message("30-Register_seeuhmOtV8").get("matched"))
        self.assertFalse(
            matcher.analyze_message(
                "https://t.me/not_a_channel?start=30-Register_seeuhmOtV8"
            ).get("matched")
        )

    def test_start_link_batches_keep_distinct_text_dedup_fingerprints(self):
        dedup = load_dedup()

        first = dedup.normalize_for_text_dedup(FIRST_BATCH)
        second = dedup.normalize_for_text_dedup(SECOND_BATCH)

        self.assertNotEqual(first, second)
        self.assertIn("register renew code fingerprints", first)

    def test_existing_project_prefixed_start_link_still_triggers(self):
        code_rules, matcher = load_matcher_modules(
            regex_rules={r"https?://(?:t\.me|telegram\.me)/Moonkkbot\?start="}
        )
        text = "https://t.me/Moonkkbot?start=SAKURA-30-Register_WXK6y4Pa1i"

        self.assertTrue(code_rules.extract_code_detail(text))
        self.assertTrue(matcher.analyze_message(text).get("matched"))


if __name__ == "__main__":
    unittest.main()
