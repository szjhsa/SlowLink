import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
EXPECTED_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8-sig").strip()


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8-sig")


class _Pipeline:
    def __init__(self, client):
        self.client = client
        self.calls = []

    def lpush(self, key, value):
        self.calls.append(("lpush", key, value))
        return self

    def ltrim(self, key, start, end):
        self.calls.append(("ltrim", key, start, end))
        return self

    def hincrby(self, key, field, amount):
        self.calls.append(("hincrby", key, field, amount))
        return self

    def execute(self):
        self.client.pipelines.append(self.calls)
        return [1] * len(self.calls)


class _Redis:
    def __init__(self):
        self.pipelines = []
        self.sets = {}
        self.lists = {}
        self.hashes = {}

    def pipeline(self):
        return _Pipeline(self)

    def lrange(self, key, start, end):
        items = self.lists.get(key) or []
        if end < 0:
            end = len(items) - 1
        return items[start:end + 1]

    def rpush(self, key, *values):
        self.lists.setdefault(key, []).extend(values)
        return len(self.lists[key])

    def delete(self, *keys):
        for key in keys:
            self.lists.pop(key, None)
            self.hashes.pop(key, None)
        return 1

    def sadd(self, key, value):
        self.sets.setdefault(key, set()).add(value)
        return 1

    def sismember(self, key, value):
        return value in (self.sets.get(key) or set())

    def expire(self, key, seconds):
        return True

    def hgetall(self, key):
        return dict(self.hashes.get(key) or {})


class DedupStatsAndCollisionsV13910Tests(unittest.TestCase):
    def test_versions_are_bumped_to_v13910(self):
        self.assertEqual(read("VERSION").strip(), EXPECTED_VERSION)
        self.assertIn(f'APP_VERSION = "{EXPECTED_VERSION}"', read(APP / "config.py"))

    def test_hit_writes_rule_and_source_counters_in_batch(self):
        fake_redis_module = types.ModuleType("redis")
        fake_redis_module.Redis = lambda *args, **kwargs: _Redis()
        old_redis_module = sys.modules.get("redis")
        sys.modules["redis"] = fake_redis_module
        sys.path.insert(0, str(APP))
        try:
            sys.modules.pop("redis_store", None)
            import redis_store

            fake = _Redis()
            redis_store.r = fake
            redis_store._BATCH_THREAD_STARTED = True
            redis_store._BATCH_BUFFER = {
                "events": [], "hits": [], "fails": [], "perf_events": [],
                "daily": [], "counters": [],
            }
            redis_store.log_line = Mock()
            redis_store.add_hit({"source": "来源A", "rule": "规则A", "status": "重复跳过"})
            redis_store.flush_batch_records()

            calls = [item for pipeline in fake.pipelines for item in pipeline]
            counter_calls = {
                (args[0], args[1])
                for name, *args in calls
                if name == "hincrby"
            }
            for key in (
                "stats:rule_hits",
                "stats:rule_duplicates",
                "stats:source_hits",
                "stats:source_duplicates",
            ):
                self.assertTrue(
                    any(field_key == key for field_key, _field in counter_calls),
                    key,
                )
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

    def test_mark_distinct_stores_exemption_and_removes_collision(self):
        fake_redis_module = types.ModuleType("redis")
        fake_redis_module.Redis = lambda *args, **kwargs: _Redis()
        old_redis_module = sys.modules.get("redis")
        sys.modules["redis"] = fake_redis_module
        sys.path.insert(0, str(APP))
        try:
            sys.modules.pop("redis_store", None)
            import redis_store

            fake = _Redis()
            redis_store.r = fake
            collision = {"identity": "global-lottery:abc", "dedup_id": "text:blocked"}
            fake.lists["dedup:collisions"] = [
                json.dumps(collision, ensure_ascii=False),
                "keep-me",
            ]

            self.assertTrue(redis_store.mark_collision_distinct(
                collision["identity"],
                collision["dedup_id"],
            ))
            self.assertTrue(redis_store.is_collision_exempt(
                collision["identity"],
                collision["dedup_id"],
            ))
            self.assertEqual(fake.lists["dedup:collisions"], ["keep-me"])
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

    def test_dedup_stats_returns_sorted_tops(self):
        fake_redis_module = types.ModuleType("redis")
        fake_redis_module.Redis = lambda *args, **kwargs: _Redis()
        old_redis_module = sys.modules.get("redis")
        sys.modules["redis"] = fake_redis_module
        sys.path.insert(0, str(APP))
        try:
            sys.modules.pop("redis_store", None)
            import redis_store

            fake = _Redis()
            redis_store.r = fake
            fake.hashes["stats:rule_hits"] = {"a": "2", "b": "5"}
            stats = redis_store.dedup_stats(limit=1)

            self.assertEqual(stats["rule_hits"], [{"key": "b", "count": 5}])
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


if __name__ == "__main__":
    unittest.main()
