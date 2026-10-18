import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"


def load_identity_store():
    sys.path.insert(0, str(APP))
    try:
        import identity_store

        return identity_store
    finally:
        try:
            sys.path.remove(str(APP))
        except ValueError:
            pass


class FakeZsetRedis:
    def __init__(self):
        self.zsets = {}
        self.expires = {}

    def zadd(self, key, mapping):
        target = self.zsets.setdefault(key, {})
        for member, score in mapping.items():
            target[str(member)] = float(score)
        return len(mapping)

    def zrangebyscore(self, key, minimum, maximum, start=0, num=None):
        low = float("-inf") if minimum == "-inf" else float(minimum)
        high = float("inf") if maximum == "+inf" else float(maximum)
        items = sorted(
            (
                (member, score)
                for member, score in self.zsets.get(key, {}).items()
                if low <= score <= high
            ),
            key=lambda item: (item[1], item[0]),
        )
        members = [member for member, _score in items][start:]
        return members if num is None else members[:num]

    def zremrangebyscore(self, key, minimum, maximum):
        low = float("-inf") if minimum == "-inf" else float(minimum)
        high = float("inf") if maximum == "+inf" else float(maximum)
        target = self.zsets.setdefault(key, {})
        removed = [
            member
            for member, score in target.items()
            if low <= score <= high
        ]
        for member in removed:
            target.pop(member, None)
        return len(removed)

    def zremrangebyrank(self, key, start, end):
        target = self.zsets.setdefault(key, {})
        ordered = sorted(target, key=lambda member: (target[member], member))
        stop = None if end == -1 else int(end) + 1
        removed = ordered[int(start):stop]
        for member in removed:
            target.pop(member, None)
        return len(removed)

    def zrem(self, key, member):
        target = self.zsets.setdefault(key, {})
        return int(target.pop(str(member), None) is not None)

    def expire(self, key, seconds):
        self.expires[key] = seconds
        return True


class MaskedIdentityStoreTests(unittest.TestCase):
    def setUp(self):
        self.store = load_identity_store()
        self.redis = FakeZsetRedis()
        self.now = 1_800_000_000.0

    def test_masked_then_unmasked_identity_is_duplicate(self):
        pattern = (
            "wildcard:"
            "chapanda-30-register_kla**gukmq"
        )
        exact = (
            "strong_register_renew:"
            "chapanda-30-register_kla51gukmq"
        )

        self.assertEqual(
            self.store.find_identity_match(self.redis, pattern, self.now),
            "",
        )
        self.store.remember_identity(self.redis, pattern, self.now + 3600)

        self.assertEqual(
            self.store.find_identity_match(self.redis, exact, self.now),
            pattern,
        )

    def test_unmasked_then_masked_identity_is_duplicate(self):
        pattern = (
            "wildcard:"
            "chapanda-30-register_kla**gukmq"
        )
        exact = (
            "strong_register_renew:"
            "chapanda-30-register_kla51gukmq"
        )

        self.store.remember_identity(self.redis, exact, self.now + 3600)

        self.assertEqual(
            self.store.find_identity_match(self.redis, pattern, self.now),
            exact,
        )

    def test_star_matches_exactly_one_character_and_respects_anchors(self):
        pattern = (
            "wildcard:"
            "chapanda-30-register_kla**gukmq"
        )
        self.store.remember_identity(self.redis, pattern, self.now + 3600)

        self.assertTrue(
            self.store.wildcard_matches(
                pattern,
                "strong_register_renew:chapanda-30-register_kla51gukmq",
            )
        )
        self.assertFalse(
            self.store.wildcard_matches(
                pattern,
                "strong_register_renew:chapanda-30-register_kla5gukmq",
            )
        )
        self.assertFalse(
            self.store.wildcard_matches(
                pattern,
                "strong_register_renew:other-30-register_kla51gukmq",
            )
        )

    def test_star_matches_chinese_and_symbol_guesses(self):
        pattern = (
            "wildcard:"
            "帝服-30-register_mk@*中x"
        )

        for guessed in ("符", "$", "#", "@"):
            with self.subTest(guessed=guessed):
                candidate = (
                    "strong_register_renew:"
                    f"帝服-30-register_mk@{guessed}中x"
                )
                self.assertTrue(
                    self.store.wildcard_matches(pattern, candidate)
                )

    def test_released_identity_no_longer_matches(self):
        pattern = (
            "wildcard:"
            "chapanda-30-register_kla**gukmq"
        )
        self.store.remember_identity(self.redis, pattern, self.now + 3600)
        self.store.release_identity(self.redis, pattern)

        self.assertEqual(
            self.store.find_identity_match(
                self.redis,
                "strong_register_renew:chapanda-30-register_kla51gukmq",
                self.now,
            ),
            "",
        )

    def test_core_identity_layer_has_no_business_prefix_names(self):
        core_source = (APP / "identity_store.py").read_text(encoding="utf-8-sig")
        runner_source = (APP / "bot_runner.py").read_text(encoding="utf-8-sig")

        self.assertIn('WILDCARD_IDENTITY_PREFIX = "wildcard:"', core_source)
        self.assertNotIn("register_renew", core_source.casefold())
        self.assertNotIn("register_renew", runner_source.casefold())


if __name__ == "__main__":
    unittest.main()
