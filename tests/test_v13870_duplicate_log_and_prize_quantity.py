import re
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
EXPECTED_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8-sig").strip()


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def load_code_rules_with_fake_redis():
    fake_redis_store = types.ModuleType("redis_store")
    fake_redis_store.get_json = lambda key, default=None: default
    fake_redis_store.set_json = lambda *a, **k: None

    old = sys.modules.get("redis_store")
    sys.modules["redis_store"] = fake_redis_store
    sys.path.insert(0, str(APP))
    try:
        sys.modules.pop("code_rules", None)
        import code_rules
        return code_rules
    finally:
        try:
            sys.path.remove(str(APP))
        except ValueError:
            pass
        if old is None:
            sys.modules.pop("redis_store", None)
        else:
            sys.modules["redis_store"] = old


class DuplicateLogAndPrizeQuantityV13870Tests(unittest.TestCase):
    def test_versions_are_bumped_to_v13870(self):
        self.assertEqual(read(ROOT / "VERSION").strip(), EXPECTED_VERSION)
        self.assertIn(f'APP_VERSION = "{EXPECTED_VERSION}"', read(APP / "config.py"))

    def test_lottery_prize_quantity_x10_is_not_treated_as_invite_code(self):
        code_rules = load_code_rules_with_fake_redis()
        text = """🎟 抽奖 新抽奖活动

创建者: Js
参与次数上限: 1
满 200 票开奖

参与关键词: 拿下！

🎁 奖品内容：
1. 星影公益注册码 x10

参与要求: 茶话领域 (https://t.me/Jsoo8888)
"""

        detail = code_rules.extract_code_detail(text)

        self.assertEqual(detail, {})

    def test_duplicate_code_branch_lets_push_event_write_docker_log_once(self):
        bot_runner = read(APP / "bot_runner.py")
        branch = re.search(
            r"if duplicate_identity:(?P<body>.*?return)",
            bot_runner,
            flags=re.S,
        )
        self.assertIsNotNone(branch)
        body = branch.group("body")
        self.assertIn('push_event("info", event_message)', body)
        self.assertNotIn('log_line("info", event_message)', body)


if __name__ == "__main__":
    unittest.main()
