import re

from chat_ids import dialog_id_variants, ordered_dialog_id_variants
from telethon.tl.types import PeerChannel


def normalize_chat_value(value: str) -> str:
    return str(value or "").strip().lower()


def split_dialog_values(raw: str) -> list[str]:
    """Split comma/space separated dialog values.

    Supports: @username, username, -100123, 123, multiple targets separated by
    comma, Chinese comma, semicolon, newline or spaces.
    """
    raw = str(raw or "").strip()
    if not raw:
        return []
    parts = re.split(r"[,，;；\n\r\t ]+", raw)
    out: list[str] = []
    seen: set[str] = set()
    for p in parts:
        p = p.strip()
        if not p:
            continue
        key = p.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _entity_signature(entity) -> tuple[str, str, str] | None:
    if entity is None:
        return None
    entity_id = getattr(entity, "id", None)
    if entity_id is None:
        entity_id = getattr(entity, "channel_id", None)
    if entity_id is None:
        entity_id = getattr(entity, "chat_id", None)
    if entity_id is None:
        entity_id = getattr(entity, "user_id", None)
    access_hash = getattr(entity, "access_hash", None)
    type_name = type(entity).__name__.lower()
    if "channel" in type_name:
        kind = "channel"
    elif "chat" in type_name:
        kind = "chat"
    elif "user" in type_name:
        kind = "user"
    else:
        kind = type_name
    return (
        kind,
        "" if entity_id is None else str(entity_id),
        "" if access_hash is None else str(access_hash),
    )


def get_chat_keys(chat) -> set[str]:
    keys: set[str] = set()
    chat_id = getattr(chat, "id", None)
    username = getattr(chat, "username", None)
    if chat_id is not None:
        keys |= dialog_id_variants(chat_id)
    if username:
        keys.add(str(username).lower())
        keys.add(f"@{str(username).lower()}")
    return keys


def build_message_link(chat, message, public_domain: str = "t.me") -> str:
    msg_id = int(getattr(message, "id", 0) or 0)
    username = getattr(chat, "username", None)
    public_domain = "telegram.me" if str(public_domain or "").strip().lower() == "telegram.me" else "t.me"

    topic_id = None
    reply_to = getattr(message, "reply_to", None)
    if reply_to:
        topic_id = getattr(reply_to, "reply_to_top_id", None)
        if not topic_id:
            topic_id = getattr(reply_to, "reply_to_msg_id", None) if getattr(reply_to, "forum_topic", False) else None

    if username:
        if topic_id and int(topic_id) != msg_id:
            return f"https://{public_domain}/{username}/{int(topic_id)}/{msg_id}"
        return f"https://{public_domain}/{username}/{msg_id}"

    raw_id = str(getattr(chat, "id", "")).strip()
    # Telethon channel entity.id is normally 368..., so t.me/c needs that value.
    if raw_id.startswith("-100"):
        internal_id = raw_id[4:]
    else:
        internal_id = raw_id.lstrip("-")

    if topic_id and int(topic_id) != msg_id:
        return f"https://t.me/c/{internal_id}/{int(topic_id)}/{msg_id}"
    return f"https://t.me/c/{internal_id}/{msg_id}"


def build_entity_cache(dialogs) -> dict[str, object]:
    """Build a fast in-memory dialog cache.

    Sending to -100ID should not call get_dialogs() every time, because that can
    add several seconds of delay. The listener warms this cache on startup and
    reuses it for every send.
    """
    cache: dict[str, object] = {}
    ambiguous: set[str] = set()
    for dialog in dialogs or []:
        entity = getattr(dialog, "entity", None)
        if entity is None:
            continue
        keys = set()
        keys |= get_chat_keys(entity)
        did = getattr(dialog, "id", None)
        if did is not None:
            keys |= dialog_id_variants(did)
        signature = _entity_signature(entity)
        for k in keys:
            key = str(k or "").strip().lower()
            if not key or key in ambiguous:
                continue
            if key in cache:
                existing = cache.get(key)
                if existing is None:
                    continue
                if _entity_signature(existing) != signature:
                    cache[key] = None
                    ambiguous.add(key)
                continue
            cache[key] = entity
    return cache


def _entity_index_payload(entity) -> dict:
    eid = getattr(entity, "id", None)
    if eid is None:
        return {}
    access_hash = getattr(entity, "access_hash", None)
    if access_hash is not None:
        if (
            getattr(entity, "broadcast", False)
            or getattr(entity, "megagroup", False)
        ):
            kind = "channel"
        elif getattr(entity, "first_name", None) is not None:
            kind = "user"
        elif getattr(entity, "title", None) is not None:
            kind = "chat"
        else:
            kind = "channel"
        return {"kind": kind, "id": int(eid), "access_hash": int(access_hash)}
    return {"kind": "chat", "id": int(eid)}


def build_entity_index(dialogs) -> dict[str, dict]:
    """Persist a compact, JSON-safe mapping for fast listener startup."""
    index: dict[str, dict] = {}
    ambiguous: set[str] = set()
    for dialog in dialogs or []:
        entity = getattr(dialog, "entity", None)
        if entity is None:
            continue
        payload = _entity_index_payload(entity)
        if not payload:
            continue
        keys = set()
        keys |= get_chat_keys(entity)
        did = getattr(dialog, "id", None)
        if did is not None:
            keys |= dialog_id_variants(did)
        for key in keys:
            normalized = str(key or "").strip().lower()
            if not normalized or normalized in ambiguous:
                continue
            if normalized in index:
                if index[normalized] != payload:
                    index.pop(normalized, None)
                    ambiguous.add(normalized)
                continue
            index[normalized] = payload
    return index


def build_entity_cache_from_index(index: dict | None) -> dict[str, object]:
    from telethon.tl.types import InputPeerChannel, InputPeerChat, InputPeerUser

    cache: dict[str, object] = {}
    for key, payload in (index or {}).items():
        try:
            kind = str(payload.get("kind") or "")
            eid = int(payload.get("id") or 0)
            if kind == "channel":
                entity: object = InputPeerChannel(eid, int(payload.get("access_hash") or 0))
            elif kind == "chat":
                entity = InputPeerChat(eid)
            elif kind == "user":
                entity = InputPeerUser(eid, int(payload.get("access_hash") or 0))
            else:
                continue
            cache[str(key).strip().lower()] = entity
        except (TypeError, ValueError):
            continue
    return cache


async def resolve_entity(client, value: str, cache: dict | None = None, refresh: bool = False):
    """Resolve @username, username, -100ID or plain channel id robustly.

    This intentionally mirrors the plugin idea: do not trust direct -100 lookup;
    refresh dialogs and match against every known dialog first. The account must
    already have access to the target/source.
    """
    value = str(value or "").strip()
    if not value:
        raise ValueError("会话为空")

    # First try warm cache. This makes normal forwarding fast.
    wanted: list[str] = []
    seen: set[str] = set()

    def add_wanted(item) -> None:
        key = str(item or "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            wanted.append(key)

    for item in ordered_dialog_id_variants(value):
        add_wanted(item)
    if value.startswith("@"):
        add_wanted(value.lower())
        add_wanted(value[1:].lower())
    elif not value.startswith("-") and not value.isdigit():
        add_wanted(value.lower())
        add_wanted("@" + value.lower())

    if cache:
        for k in wanted:
            if k in cache and cache[k] is not None:
                return cache[k]

    # Public username. This is stable and usually quick.
    if value.startswith("@") or (not value.startswith("-") and not value.isdigit()):
        return await client.get_entity(value)

    # Refresh dialogs only when explicitly requested, not on every send.
    if refresh:
        dialogs = await client.get_dialogs(limit=None)
        local_cache = build_entity_cache(dialogs)
        for k in wanted:
            if k in local_cache and local_cache[k] is not None:
                return local_cache[k]

    # Last fallback: try PeerChannel with the internal id. This only works when the
    # session cache already has access_hash; otherwise we show a clear error.
    inner = None
    if value.startswith("-100") and value[4:].isdigit():
        inner = int(value[4:])
    elif value.lstrip("-").isdigit():
        inner = int(value.lstrip("-"))
    if inner:
        try:
            return await client.get_entity(PeerChannel(inner))
        except Exception:
            pass
        try:
            return await client.get_input_entity(PeerChannel(inner))
        except Exception:
            pass

    raise ValueError(f"找不到会话：{value}。请确认账号已加入/有权限，并先用“测试监听源”获取可填 ID。")
