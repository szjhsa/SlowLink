import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"


class FakeChat:
    id = -123456
    access_hash = 11
    username = None
    title = "普通群"
    first_name = None
    broadcast = False
    megagroup = False


class FakeChannel:
    id = 123456
    access_hash = 22
    username = None
    title = "频道"
    first_name = None
    broadcast = True
    megagroup = False


class FakeDialog:
    def __init__(self, entity, dialog_id):
        self.entity = entity
        self.id = dialog_id
        self.name = getattr(entity, "title", "")


def load_link_builder():
    fake_telethon = types.ModuleType("telethon")
    fake_tl = types.ModuleType("telethon.tl")
    fake_types = types.ModuleType("telethon.tl.types")

    class FakePeerChannel:
        def __init__(self, channel_id):
            self.channel_id = channel_id

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

    fake_types.PeerChannel = FakePeerChannel
    fake_types.InputPeerChannel = FakeInputPeerChannel
    fake_types.InputPeerChat = FakeInputPeerChat
    fake_types.InputPeerUser = FakeInputPeerUser
    fake_tl.types = fake_types
    fake_telethon.tl = fake_tl

    old_telethon = sys.modules.get("telethon")
    old_tl = sys.modules.get("telethon.tl")
    old_types = sys.modules.get("telethon.tl.types")
    sys.modules["telethon"] = fake_telethon
    sys.modules["telethon.tl"] = fake_tl
    sys.modules["telethon.tl.types"] = fake_types
    try:
        spec = importlib.util.spec_from_file_location(
            "link_builder_collision_test",
            APP / "link_builder.py",
        )
        module = importlib.util.module_from_spec(spec)
        assert spec is not None and spec.loader is not None
        spec.loader.exec_module(module)
        return module
    finally:
        if old_telethon is None:
            sys.modules.pop("telethon", None)
        else:
            sys.modules["telethon"] = old_telethon
        if old_tl is None:
            sys.modules.pop("telethon.tl", None)
        else:
            sys.modules["telethon.tl"] = old_tl
        if old_types is None:
            sys.modules.pop("telethon.tl.types", None)
        else:
            sys.modules["telethon.tl.types"] = old_types


class EntityCacheCollisionV111Tests(unittest.TestCase):
    def test_id_variants_do_not_invent_the_other_chat_form(self):
        link_builder = load_link_builder()

        self.assertNotIn("-100123456", link_builder.dialog_id_variants(-123456))
        self.assertNotIn("-123456", link_builder.dialog_id_variants(123456))
        self.assertEqual(
            link_builder.ordered_dialog_id_variants("-100123456"),
            ["-100123456", "123456"],
        )

    def test_entity_cache_keeps_explicit_group_and_channel_keys_distinct(self):
        link_builder = load_link_builder()
        group = FakeChat()
        channel = FakeChannel()
        group_dialog = FakeDialog(group, group.id)
        channel_dialog = FakeDialog(channel, -100123456)

        for dialogs in (
            [group_dialog, channel_dialog],
            [channel_dialog, group_dialog],
        ):
            cache = link_builder.build_entity_cache(dialogs)
            self.assertIs(cache.get("-123456"), group)
            self.assertIs(cache.get("-100123456"), channel)
            self.assertIsNone(cache.get("123456"))

    def test_entity_index_omits_ambiguous_positive_numeric_key(self):
        link_builder = load_link_builder()
        group = FakeChat()
        channel = FakeChannel()
        index = link_builder.build_entity_index([
            FakeDialog(group, group.id),
            FakeDialog(channel, -100123456),
        ])

        self.assertEqual(index["-123456"]["kind"], "chat")
        self.assertEqual(index["-123456"]["id"], -123456)
        self.assertEqual(index["-100123456"]["kind"], "channel")
        self.assertEqual(index["-100123456"]["id"], 123456)
        self.assertNotIn("123456", index)

    def test_resolve_entity_prefers_explicit_negative_forms(self):
        link_builder = load_link_builder()
        group = FakeChat()
        channel = FakeChannel()
        cache = {
            "-123456": group,
            "-100123456": channel,
            "123456": None,
        }

        class Client:
            async def get_entity(self, value):
                raise AssertionError(f"cache should have resolved {value}")

        async def run():
            group_result = await link_builder.resolve_entity(Client(), "-123456", cache)
            channel_result = await link_builder.resolve_entity(Client(), "-100123456", cache)
            positive_result = await link_builder.resolve_entity(Client(), "123456", cache)
            return group_result, channel_result, positive_result

        group_result, channel_result, positive_result = asyncio.run(run())
        self.assertIs(group_result, group)
        self.assertIs(channel_result, channel)
        self.assertIs(positive_result, channel)


if __name__ == "__main__":
    unittest.main()
