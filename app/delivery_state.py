"""Per-target delivery state to avoid duplicate sends after partial failures."""

import hashlib
import json
import time
from urllib.parse import urlparse


DELIVERY_STATE_TTL_SECONDS = 24 * 60 * 60
DELIVERY_PENDING_STALE_SECONDS = 5 * 60
DELIVERY_KEY_PREFIX = "delivery:source:"


def build_delivery_key(
    link: str,
    text: str,
    source: str,
    message_ts: float | None = None,
) -> str:
    parsed = urlparse(str(link or "").strip())
    stable_link = parsed.path if parsed.scheme and parsed.netloc else str(link or "").strip()
    content_identity = stable_link or str(text or "")
    identity = "|".join(
        (
            str(source or "").strip(),
            str(message_ts or ""),
            content_identity,
        )
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return DELIVERY_KEY_PREFIX + digest


def read_target_state(redis_client, delivery_key: str, target: str) -> dict:
    if not delivery_key or not target:
        return {}
    try:
        raw = redis_client.hget(delivery_key, str(target))
        value = json.loads(raw) if raw else {}
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def should_send_target(
    redis_client,
    delivery_key: str,
    target: str,
    now: float | None = None,
) -> bool:
    state = read_target_state(redis_client, delivery_key, target)
    status = str(state.get("status") or "")
    if status == "sent":
        return False
    if status == "pending":
        try:
            started = float(state.get("ts") or 0)
        except (TypeError, ValueError):
            started = 0.0
        current = float(now if now is not None else time.time())
        return not started or current - started >= DELIVERY_PENDING_STALE_SECONDS
    return True


def mark_target_state(
    redis_client,
    delivery_key: str,
    target: str,
    status: str,
) -> None:
    if not delivery_key or not target or status not in {"pending", "sent", "failed"}:
        return
    value = json.dumps(
        {"status": status, "ts": time.time()},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    try:
        pipe = redis_client.pipeline()
        pipe.hset(delivery_key, str(target), value)
        pipe.expire(delivery_key, DELIVERY_STATE_TTL_SECONDS)
        pipe.execute()
        return
    except Exception:
        pass
    try:
        redis_client.hset(
            delivery_key,
            str(target),
            value,
        )
        redis_client.expire(delivery_key, DELIVERY_STATE_TTL_SECONDS)
    except Exception:
        pass
