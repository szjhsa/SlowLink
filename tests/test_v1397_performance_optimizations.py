import importlib.util
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


class _BatchPipeline:
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


class _BatchRedis:
    def __init__(self):
        self.pipelines = []

    def get(self, _key):
        return None

    def pipeline(self):
        return _BatchPipeline(self)


class FakeChannel:
    id = 111
    access_hash = 123
    broadcast = True
    megagroup = False
    username = "channel"
    title = "频道"


class FakeChat:
    id = 222
    access_hash = 789
    title = "普通群"


class FakeUser:
    id = 333
    access_hash = 456
    first_name = "用户"


class FakeDialog:
    def __init__(self, entity):
        self.entity = entity
        self.id = getattr(entity, "id", None)
        self.name = getattr(entity, "title", None) or getattr(entity, "first_name", "")


class PerformanceOptimizationsV1397Tests(unittest.TestCase):
    def test_versions_are_bumped_to_v1397(self):
        self.assertEqual(read("VERSION").strip(), EXPECTED_VERSION)
        self.assertIn(f'APP_VERSION = "{EXPECTED_VERSION}"', read(APP / "config.py"))

    def test_worker_pool_reconciles_between_minimum_and_burst_capacity(self):
        runner = read("app/bot_runner.py")

        for fragment in (
            "def _reconcile_worker_targets(self, client",
            "self._desired_normal_workers",
            "self._worker_idle_since",
            "def _retire_worker_spec",
            "worker_id >= (self._desired_normal_workers or 1)",
            "target = MIN_NORMAL_WORKERS",
        ):
            self.assertIn(fragment, runner)
        self.assertNotIn(
            "max(MIN_NORMAL_WORKERS, self._base_normal_workers - 1)",
            runner,
        )

    def test_redis_records_are_batched_and_flushed_together(self):
        fake_redis_module = types.ModuleType("redis")
        fake_redis_module.Redis = lambda *args, **kwargs: _BatchRedis()
        old_redis_module = sys.modules.get("redis")
        sys.modules["redis"] = fake_redis_module
        sys.path.insert(0, str(APP))
        try:
            sys.modules.pop("redis_store", None)
            import redis_store

            fake = _BatchRedis()
            redis_store.r = fake
            redis_store._BATCH_THREAD_STARTED = True
            redis_store._BATCH_BUFFER = {
                "events": [], "hits": [], "fails": [], "perf_events": [], "daily": []
            }
            redis_store.log_line = Mock()
            redis_store.add_hit({"source": "test", "status": "已转发"})
            redis_store.add_perf_event({"source": "test", "rule": "r", "result": "sent"})
            redis_store.push_event("success", "batch event")
            redis_store.flush_batch_records()

            self.assertTrue(fake.pipelines)
            calls = [item for pipeline in fake.pipelines for item in pipeline]
            pushed_keys = {key for name, key, *_ in calls if name == "lpush"}
            self.assertTrue({"hits", "perf_events", "events"} <= pushed_keys)
            self.assertTrue(any(name == "ltrim" for name, *_ in calls))
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

    def test_entity_index_rebuilds_input_peer_cache(self):
        fake_telethon = types.ModuleType("telethon")
        fake_tl = types.ModuleType("telethon.tl")
        fake_types = types.ModuleType("telethon.tl.types")
        class FakeInputPeerChannel:
            def __init__(self, channel_id, access_hash):
                self.channel_id = channel_id
                self.access_hash = access_hash
        class FakeInputPeerChat:
            def __init__(self, chat_id):
                self.chat_id = chat_id
        class FakeInputPeerUser:
            def __init__(self, user_id, access_hash):
                self.user_id = user_id
                self.access_hash = access_hash
        class FakePeerChannel:
            def __init__(self, channel_id):
                self.channel_id = channel_id
        fake_types.InputPeerChannel = FakeInputPeerChannel
        fake_types.InputPeerChat = FakeInputPeerChat
        fake_types.InputPeerUser = FakeInputPeerUser
        fake_types.PeerChannel = FakePeerChannel
        fake_tl.types = fake_types
        fake_telethon.tl = fake_tl
        old_telethon = sys.modules.get("telethon")
        old_tl = sys.modules.get("telethon.tl")
        old_types = sys.modules.get("telethon.tl.types")
        sys.modules["telethon"] = fake_telethon
        sys.modules["telethon.tl"] = fake_tl
        sys.modules["telethon.tl.types"] = fake_types
        sys.path.insert(0, str(APP))
        try:
            spec = importlib.util.spec_from_file_location("link_builder_v1397", APP / "link_builder.py")
            link_builder = importlib.util.module_from_spec(spec)
            assert spec is not None and spec.loader is not None
            spec.loader.exec_module(link_builder)

            index = link_builder.build_entity_index([
                FakeDialog(FakeChannel()),
                FakeDialog(FakeChat()),
                FakeDialog(FakeUser()),
            ])
            cache = link_builder.build_entity_cache_from_index(index)

            self.assertIn("111", cache)
            self.assertIn("222", cache)
            self.assertIn("333", cache)
            self.assertIn("InputPeerChannel", type(cache["111"]).__name__)
            self.assertIn("InputPeerChat", type(cache["222"]).__name__)
            self.assertIn("InputPeerUser", type(cache["333"]).__name__)
        finally:
            if old_types is None:
                sys.modules.pop("telethon.tl.types", None)
            else:
                sys.modules["telethon.tl.types"] = old_types
            if old_tl is None:
                sys.modules.pop("telethon.tl", None)
            else:
                sys.modules["telethon.tl"] = old_tl
            if old_telethon is None:
                sys.modules.pop("telethon", None)
            else:
                sys.modules["telethon"] = old_telethon
            try:
                sys.path.remove(str(APP))
            except ValueError:
                pass


if __name__ == "__main__":
    unittest.main()
