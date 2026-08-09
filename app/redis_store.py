import hashlib
import json
import os
import sys
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any, Iterable

import redis
from config import REDIS_HOST, REDIS_PORT, LISTENER_WORKERS

r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True, socket_connect_timeout=5, socket_keepalive=True, retry_on_timeout=True, health_check_interval=30)
_TIMEZONE_CACHE = {"ts": None, "value": "Asia/Shanghai"}
_TIMEZONE_CACHE_TTL = 60.0
_BATCH_BUFFER: dict[str, list] = {
    "events": [],
    "hits": [],
    "fails": [],
    "perf_events": [],
    "daily": [],
    "counters": [],
}
_BATCH_LOCK = threading.Lock()
_BATCH_FLUSHING = False
_BATCH_THREAD_STARTED = False
_BATCH_MAX_BUFFER = 1000
_BATCH_FLUSH_INTERVAL = 0.5
STATS_HASH_KEYS = (
    "stats:rule_hits",
    "stats:rule_duplicates",
    "stats:source_hits",
    "stats:source_duplicates",
    "stats:lottery_collisions",
)
STATS_HASH_MAX_FIELDS = 500
STATS_HASH_PRUNE_WATERMARK = 400
STATS_HASH_PRUNE_INTERVAL_SECONDS = 300.0
DAILY_STATS_RETENTION_DAYS = 30
DAILY_STATS_PRUNE_INTERVAL_SECONDS = 3600.0
_STATS_PRUNE_LAST_TS = 0.0
_DAILY_STATS_PRUNE_LAST_TS = 0.0
_PRUNE_STATS_SCRIPT = """
local fields = redis.call('HGETALL', KEYS[1])
local count = #fields / 2
local max_fields = tonumber(ARGV[1])
if count <= max_fields then return 0 end
local entries = {}
for i = 1, #fields, 2 do
  entries[#entries + 1] = { fields[i], tonumber(fields[i + 1]) or 0 }
end
table.sort(entries, function(a, b)
  if a[2] ~= b[2] then return a[2] < b[2] end
  return a[1] < b[1]
end)
local remove = {}
local keep = tonumber(ARGV[2])
for i = 1, count - keep do
  remove[#remove + 1] = entries[i][1]
end
if #remove > 0 then
  redis.call('HDEL', KEYS[1], unpack(remove))
end
return #remove
"""

LEGACY_PURE_CODE_TRIGGER_RULE = r"^(?!.*码使用)[^-]+-\d+-(?:Register|Renew)_.+$"
LEGACY_SAFE_PURE_CODE_TRIGGER_RULE = (
    r"^(?!.*码使用)(?:[^\s-]+-)+\d+(?:-[^\s-]+)*-"
    r"(?:Register|Renew)_[A-Za-z0-9_-]+$"
)
LEGACY_MASKED_PURE_CODE_TRIGGER_RULE = (
    r"^(?!.*码使用)(?:[^\s-]+-)+\d+(?:-[^\s-]+)*-"
    r"(?:Register|Renew)_(?:[A-Za-z0-9_-]|数字|字母)+$"
)
LEGACY_SYMBOL_PURE_CODE_TRIGGER_RULE = (
    r"^(?!.*码使用)(?:[^\s-]+-)+\d+(?:-[^\s-]+)*-"
    r"(?:Register|Renew)_(?:[^\s*`\u3400-\u9fff]|数字|字母)+$"
)
SAFE_PURE_CODE_TRIGGER_RULE = (
    r"^(?!.*码使用)(?:[^\s-]+-)+\d+(?:-[^\s-]+)*-"
    r"(?:Register|Renew)_[^\s*`]+$"
)
LEGACY_SAFE_WHITELIST_TRIGGER_RULE = (
    r"(?:^|(?<=[\s:：，,]))[^\s*`\-:：，,]+(?:-[^\s*`\-:：，,]+)*-Whitelist_"
    r"(?a:[A-Za-z0-9]{10})"
    r"(?=$|\s|[，。！？？；：、）】]|[,.;:)\]}>`~*](?![A-Za-z0-9_-]))"
)
SAFE_WHITELIST_TRIGGER_RULE = (
    r"(?:^|(?<=[\s:：，,]))[^\s*`\-:：，,]+(?:-[^\s*`\-:：，,]+)*-Whitelist_"
    r"(?=[^\s*`]*[\u3400-\u9fff])"
    r"(?=(?:[^A-Za-z0-9\s*`]*[A-Za-z0-9]){10}[^A-Za-z0-9\s*`]*(?=$|\s))"
    r"[^\s*`]+?"
    r"(?=$|\s|[，。！？？；：、）】]|[,.;:)\]}>`~*](?![A-Za-z0-9_-]))"
)
LEGACY_REGISTRATION_ANNOUNCEMENT_RULE = (
    r"((?:[🫧🎫🎟️🎭🤖⏳].*?(?:自由|定时)注册.*(?:\n[🫧🎫🎟️🎭🤖⏳].*\|\s*\d+.*)*\n?)+)"
    r"|((?:[🎉✨📱⏰].*?开放注册.*(?:\n[🎉✨📱⏰].*)*\n?)+)"
)
SAFE_REGISTRATION_ANNOUNCEMENT_RULE = (
    r"(?m)^(?:[🫧🎫🎟️🎭🤖⏳][^\n]*(?:自由|定时)注册"
    r"|[🎉✨📱⏰][^\n]*开放注册)[^\n]*$"
)
LEGACY_LOTTERY_ACTIVITY_RULE = r"\n\n🎁 抽奖活动已开始"
SAFE_LOTTERY_ACTIVITY_RULE = r"(?m)^抽奖活动已开始！?$"
KNOWN_REGEX_RULE_MIGRATIONS = {
    LEGACY_PURE_CODE_TRIGGER_RULE: SAFE_PURE_CODE_TRIGGER_RULE,
    LEGACY_SAFE_PURE_CODE_TRIGGER_RULE: SAFE_PURE_CODE_TRIGGER_RULE,
    LEGACY_MASKED_PURE_CODE_TRIGGER_RULE: SAFE_PURE_CODE_TRIGGER_RULE,
    LEGACY_SYMBOL_PURE_CODE_TRIGGER_RULE: SAFE_PURE_CODE_TRIGGER_RULE,
    LEGACY_SAFE_WHITELIST_TRIGGER_RULE: SAFE_WHITELIST_TRIGGER_RULE,
    LEGACY_REGISTRATION_ANNOUNCEMENT_RULE: SAFE_REGISTRATION_ANNOUNCEMENT_RULE,
    LEGACY_LOTTERY_ACTIVITY_RULE: SAFE_LOTTERY_ACTIVITY_RULE,
}


def log_line(level: str, message: str, extra: dict | None = None) -> None:
    """Write important runtime diagnostics to Docker stdout.

    The WebUI keeps records in Redis, but docker logs must also show what the
    listener is doing, otherwise delayed Telegram updates cannot be diagnosed.
    This helper is intentionally best-effort and never raises.
    """
    try:
        suffix = ""
        if extra:
            suffix = " " + json.dumps(extra, ensure_ascii=False, default=str)
        print(f"[{format_time()}] [{str(level).upper()}] {message}{suffix}", flush=True)
    except Exception:
        try:
            print(f"[SlowLink] {level}: {message}", flush=True)
        except Exception:
            pass


def now_ts() -> int:
    return int(time.time())


def format_time(ts: int | float | None = None) -> str:
    """Format UI/log time using configured display timezone.

    Default is Beijing time so VPS local timezone will not affect the page.
    """
    now = time.monotonic()
    tz_name = str(_TIMEZONE_CACHE.get("value") or "Asia/Shanghai")
    cached_ts = _TIMEZONE_CACHE.get("ts")
    if cached_ts is None or now - float(cached_ts) > _TIMEZONE_CACHE_TTL:
        try:
            tz_name = r.get("display_timezone") or "Asia/Shanghai"
        except Exception:
            pass
        _TIMEZONE_CACHE.update({"ts": now, "value": str(tz_name)})
    try:
        tz = ZoneInfo(str(tz_name))
    except Exception:
        tz = ZoneInfo("Asia/Shanghai")
    dt = datetime.fromtimestamp(float(ts if ts is not None else time.time()), tz)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def clear_timezone_cache() -> None:
    _TIMEZONE_CACHE.update({"ts": None, "value": "Asia/Shanghai"})


def get(key: str, default: str | None = None) -> str | None:
    value = r.get(key)
    return default if value is None else value


def set_value(key: str, value: Any) -> None:
    r.set(key, str(value))


def delete(*keys: str) -> None:
    if keys:
        r.delete(*keys)


def sadd(key: str, value: str) -> None:
    value = (value or "").strip()
    if value:
        r.sadd(key, value)


def srem(key: str, value: str) -> None:
    r.srem(key, value)


def smembers(key: str) -> set[str]:
    return set(r.smembers(key))


def get_json(key: str, default: Any = None) -> Any:
    raw = r.get(key)
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def set_json(key: str, value: Any) -> None:
    r.set(key, json.dumps(value, ensure_ascii=False))


def _ensure_batch_thread() -> None:
    global _BATCH_THREAD_STARTED
    if _BATCH_THREAD_STARTED:
        return
    with _BATCH_LOCK:
        if _BATCH_THREAD_STARTED:
            return
        _BATCH_THREAD_STARTED = True
    threading.Thread(
        target=_batch_flush_loop,
        daemon=True,
        name="slowlink-redis-batch-flush",
    ).start()


def _batch_flush_loop() -> None:
    while True:
        time.sleep(_BATCH_FLUSH_INTERVAL)
        flush_batch_records()
        try:
            prune_stats_hashes()
            prune_daily_stats()
        except Exception:
            pass


def _daily_stat_key() -> str:
    try:
        tz_name = str(_TIMEZONE_CACHE.get("value") or "Asia/Shanghai")
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("Asia/Shanghai")
    return "daily_stats:" + datetime.now(tz).strftime("%Y-%m-%d")


def _hit_category(status: Any) -> str:
    text = str(status or "")
    if "重复跳过" in text:
        return "duplicate"
    if "命中但发送失败" in text:
        return "send_failed"
    if "已转发" in text:
        return "forwarded"
    if "排除来源" in text:
        return "excluded"
    return "hit"


def _enqueue_record(key: str, raw: str, limit: int) -> None:
    with _BATCH_LOCK:
        items = _BATCH_BUFFER.setdefault(key, [])
        if key == "daily":
            items.append(str(raw))
        else:
            items.append((raw, int(limit)))
        size = len(items)
        if len(items) > _BATCH_MAX_BUFFER:
            del items[: len(items) - _BATCH_MAX_BUFFER]
    _ensure_batch_thread()
    if size >= 50:
        flush_batch_records()


def _enqueue_counter(hash_key: str, field: str) -> None:
    with _BATCH_LOCK:
        items = _BATCH_BUFFER.setdefault("counters", [])
        items.append((str(hash_key), str(field)))
        size = len(items)
        if len(items) > _BATCH_MAX_BUFFER:
            del items[: len(items) - _BATCH_MAX_BUFFER]
    _ensure_batch_thread()
    if size >= 50:
        flush_batch_records()


def _write_records_now(key: str, records: list[tuple[str, int]]) -> None:
    if not records:
        return
    try:
        pipe = r.pipeline()
        for raw, limit in records:
            pipe.lpush(key, raw)
            pipe.ltrim(key, 0, limit - 1)
        pipe.execute()
    except Exception:
        pass


def flush_batch_records() -> None:
    global _BATCH_FLUSHING
    if _BATCH_FLUSHING:
        return
    _BATCH_FLUSHING = True
    pending: dict[str, list] = {}
    try:
        with _BATCH_LOCK:
            for key in list(_BATCH_BUFFER):
                pending[key] = list(_BATCH_BUFFER.get(key) or [])
                _BATCH_BUFFER[key] = []
        if not any(pending.values()):
            return
        try:
            pipe = r.pipeline()
            for key in ("events", "hits", "fails", "perf_events", "collisions"):
                for raw, limit in pending.get(key) or []:
                    pipe.lpush(key, raw)
                    pipe.ltrim(key, 0, limit - 1)
            daily_key = _daily_stat_key()
            for category in pending.get("daily") or []:
                pipe.hincrby(daily_key, str(category), 1)
            for hash_key, field in pending.get("counters") or []:
                pipe.hincrby(hash_key, str(field), 1)
            pipe.execute()
        except Exception:
            with _BATCH_LOCK:
                for key, items in pending.items():
                    combined = _BATCH_BUFFER.setdefault(key, []) + items
                    if len(combined) > _BATCH_MAX_BUFFER:
                        del combined[: len(combined) - _BATCH_MAX_BUFFER]
                    _BATCH_BUFFER[key] = combined
    finally:
        _BATCH_FLUSHING = False


def push_event(kind: str, message: str, extra: dict | None = None, limit: int = 300) -> None:
    item = {
        "time": format_time(),
        "kind": kind,
        "message": message,
        "extra": extra or {},
    }
    log_line(kind, message, extra or None)
    _enqueue_record("events", json.dumps(item, ensure_ascii=False), limit)


def list_events(limit: int = 50) -> list[dict]:
    flush_batch_records()
    items = []
    for raw in r.lrange("events", 0, limit - 1):
        try:
            items.append(json.loads(raw))
        except Exception:
            pass
    return items


def add_perf_event(item: dict, limit: int = 120) -> None:
    item = dict(item)
    item.setdefault("time", format_time())
    _enqueue_record("perf_events", json.dumps(item, ensure_ascii=False, default=str), limit)


def list_perf_events(limit: int = 30) -> list[dict]:
    flush_batch_records()
    out = []
    for raw in r.lrange("perf_events", 0, limit - 1):
        try:
            out.append(json.loads(raw))
        except Exception:
            pass
    return out


def add_hit(item: dict, limit: int = 300) -> None:
    item = dict(item)
    item.setdefault("time", format_time())
    _enqueue_record("hits", json.dumps(item, ensure_ascii=False), limit)
    category = _hit_category(item.get("status"))
    _enqueue_record("daily", category, 0)
    rule = str(item.get("rule") or "").strip()
    source = str(item.get("source") or "").strip()
    if rule:
        _enqueue_counter("stats:rule_hits", rule[:200])
        if category == "duplicate":
            _enqueue_counter("stats:rule_duplicates", rule[:200])
    if source:
        _enqueue_counter("stats:source_hits", source[:120])
        if category == "duplicate":
            _enqueue_counter("stats:source_duplicates", source[:120])


def list_hits(limit: int = 50) -> list[dict]:
    flush_batch_records()
    out = []
    for raw in r.lrange("hits", 0, limit - 1):
        try:
            out.append(json.loads(raw))
        except Exception:
            pass
    return out


def add_fail(item: dict, limit: int = 200, *, emit_log: bool = True) -> None:
    item = dict(item)
    item.setdefault("time", format_time())
    if emit_log:
        try:
            log_line("error", f"{item.get('stage', 'fail')}：{item.get('error', item)}", {k: v for k, v in item.items() if k not in {'error'}})
        except Exception:
            pass
    raw = json.dumps(item, ensure_ascii=False)
    if emit_log:
        _enqueue_record("fails", raw, limit)
    else:
        _write_records_now("fails", [(raw, limit)])


def list_fails(limit: int = 50) -> list[dict]:
    flush_batch_records()
    out = []
    for raw in r.lrange("fails", 0, limit - 1):
        try:
            out.append(json.loads(raw))
        except Exception:
            pass
    return out


def daily_stats() -> dict[str, int]:
    try:
        raw = r.hgetall(_daily_stat_key())
        return {str(k): int(v or 0) for k, v in (raw or {}).items()}
    except Exception:
        return {}


def prune_stats_hashes(force: bool = False) -> int:
    global _STATS_PRUNE_LAST_TS
    now = time.monotonic()
    if not force and now - _STATS_PRUNE_LAST_TS < STATS_HASH_PRUNE_INTERVAL_SECONDS:
        return 0
    _STATS_PRUNE_LAST_TS = now
    removed = 0
    for key in STATS_HASH_KEYS:
        try:
            removed += int(r.eval(
                _PRUNE_STATS_SCRIPT,
                1,
                key,
                STATS_HASH_MAX_FIELDS,
                STATS_HASH_PRUNE_WATERMARK,
            ) or 0)
        except Exception:
            continue
    return removed


def prune_daily_stats(force: bool = False) -> int:
    global _DAILY_STATS_PRUNE_LAST_TS
    now = time.monotonic()
    if not force and now - _DAILY_STATS_PRUNE_LAST_TS < DAILY_STATS_PRUNE_INTERVAL_SECONDS:
        return 0
    _DAILY_STATS_PRUNE_LAST_TS = now
    removed = 0
    try:
        tz_name = str(_TIMEZONE_CACHE.get("value") or "Asia/Shanghai")
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("Asia/Shanghai")
    today = datetime.now(tz).date()
    try:
        for key in r.scan_iter(match="daily_stats:*", count=200):
            date_part = str(key).split(":", 1)[-1]
            try:
                day = datetime.strptime(date_part, "%Y-%m-%d").date()
            except Exception:
                continue
            if (today - day).days > DAILY_STATS_RETENTION_DAYS:
                r.delete(key)
                removed += 1
    except Exception:
        pass
    return removed


def _top_hash(key: str, limit: int) -> list[dict]:
    try:
        raw = r.hgetall(key)
        items = [{"key": str(k), "count": int(v or 0)} for k, v in (raw or {}).items()]
        return sorted(items, key=lambda item: item["count"], reverse=True)[:limit]
    except Exception:
        return []


def dedup_stats(limit: int = 20) -> dict:
    flush_batch_records()
    return {
        "rule_hits": _top_hash("stats:rule_hits", limit),
        "rule_duplicates": _top_hash("stats:rule_duplicates", limit),
        "source_hits": _top_hash("stats:source_hits", limit),
        "source_duplicates": _top_hash("stats:source_duplicates", limit),
        "lottery_collisions": _top_hash("stats:lottery_collisions", limit),
    }


COLLISION_LIST = "dedup:collisions"
_COLLISION_FILTER_SCRIPT = """
local items = redis.call('LRANGE', KEYS[1], 0, -1)
local kept = {}
for i, raw in ipairs(items) do
  local ok, item = pcall(cjson.decode, raw)
  if not ok or not (item['dedup_id'] == ARGV[1] and item['identity'] == ARGV[2]) then
    kept[#kept + 1] = raw
  end
end
redis.call('DEL', KEYS[1])
if #kept > 0 then
  redis.call('RPUSH', KEYS[1], unpack(kept))
end
return #kept
"""


def add_lottery_collision(item: dict, limit: int = 200) -> None:
    item = dict(item)
    item.setdefault("time", format_time())
    _enqueue_record("collisions", json.dumps(item, ensure_ascii=False), limit)
    _enqueue_counter(
        "stats:lottery_collisions",
        str(item.get("identity") or "")[:200],
    )


def list_collisions(limit: int = 30) -> list[dict]:
    flush_batch_records()
    out = []
    for raw in r.lrange(COLLISION_LIST, 0, limit - 1):
        try:
            out.append(json.loads(raw))
        except Exception:
            pass
    return out


def clear_collisions() -> None:
    try:
        r.delete(COLLISION_LIST)
    except Exception:
        pass


def is_collision_exempt(identity: str, dedup_id: str) -> bool:
    if not identity or not dedup_id:
        return False
    try:
        return bool(r.sismember("dedup:collision_exempt:" + sha(identity), str(dedup_id)))
    except Exception:
        return False


def mark_collision_distinct(identity: str, dedup_id: str) -> bool:
    if not identity or not dedup_id:
        return False
    key = "dedup:collision_exempt:" + sha(identity)
    try:
        r.sadd(key, str(dedup_id))
        r.expire(key, 7 * 24 * 60 * 60)
    except Exception:
        return False
    try:
        r.eval(_COLLISION_FILTER_SCRIPT, 1, COLLISION_LIST, str(dedup_id), str(identity))
    except Exception:
        try:
            items = r.lrange(COLLISION_LIST, 0, -1)
            kept = []
            for raw in items:
                try:
                    item = json.loads(raw)
                except Exception:
                    kept.append(raw)
                    continue
                if (
                    str(item.get("dedup_id") or "") == str(dedup_id)
                    and str(item.get("identity") or "") == str(identity)
                ):
                    continue
                kept.append(raw)
            r.delete(COLLISION_LIST)
            if kept:
                r.rpush(COLLISION_LIST, *kept)
        except Exception:
            pass
    return True


def sha(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def ensure_defaults() -> None:
    defaults = {
        "dedup_enabled": "1",
        "dedup_minutes": "20",
        "dedup_invite_minutes": "0",
        "dedup_code_minutes": "20",
        "dedup_mode": "strict",
        "display_timezone": "Asia/Shanghai",
        "bot_status": "stopped",
        "listener_desired_state": "stopped",
        "dedup_similarity_enabled": "0",
        "dedup_lottery_template_mode": "global",
        "worker_count": str(LISTENER_WORKERS),
    }
    for k, v in defaults.items():
        r.setnx(k, v)
    migrate_known_regex_rules()


def migrate_known_regex_rules() -> int:
    replacements = []
    try:
        for old, new in KNOWN_REGEX_RULE_MIGRATIONS.items():
            if r.sismember("regex_rules", old):
                replacements.append((old, new))
        if not replacements:
            return 0
        pipe = r.pipeline()
        for old, new in replacements:
            pipe.srem("regex_rules", old)
            pipe.sadd("regex_rules", new)
        pipe.execute()
        return len(replacements)
    except Exception:
        return 0


# ---- V1.25 cache / record management ----

def scan_keys(pattern: str, count: int = 500) -> list[str]:
    """Return Redis keys matching a pattern without blocking Redis like KEYS."""
    try:
        return [str(k) for k in r.scan_iter(match=pattern, count=count)]
    except Exception:
        return []


def delete_pattern(pattern: str) -> int:
    """Delete keys by pattern using SCAN. Returns deleted count."""
    keys = scan_keys(pattern)
    deleted = 0
    if not keys:
        return 0
    pipe = r.pipeline()
    for k in keys:
        pipe.delete(k)
    results = pipe.execute()
    for item in results:
        try:
            deleted += int(item or 0)
        except Exception:
            pass
    return deleted


def list_len(key: str) -> int:
    try:
        return int(r.llen(key) or 0)
    except Exception:
        return 0


def safe_dbsize() -> int:
    try:
        return int(r.dbsize() or 0)
    except Exception:
        return 0


def count_patterns(patterns: list[str]) -> int:
    if not patterns:
        return 0
    seen = set()
    try:
        for pattern in patterns:
            cursor = 0
            while True:
                cursor, keys = r.scan(cursor=cursor, match=pattern, count=500)
                for k in keys:
                    seen.add(str(k))
                if cursor == 0:
                    break
    except Exception:
        return 0
    return len(seen)


ACTIVE_DEDUP_PATTERNS = [
    "dedup:main:*",
    "dedup:core:*",
    "dedup:link:*",
    "dedup:code:*",
    "dedup:lottery:*",
    "dedup:lottery-template:*",
    "dedup:register_snapshot:*",
    "dedup:content_url:*",
    "dedup:text:*",
    "dedup:collision_exempt:*",
]

DEDUP_META_PATTERNS = ["dedup:meta:*"]


_CACHED_STATS = {"ts":0.0,"data":{}}

def clear_stats_cache():
    _CACHED_STATS["ts"] = 0.0


def cache_stats() -> dict:
    """Small, safe stats for the WebUI cache management panel.

    Important wording:
    - dedup_cache means active anti-duplicate TTL keys, not page records.
    - dedup_recent / dedup_records are display/history lists.
    """
    nt = time.time()
    if nt - _CACHED_STATS["ts"] < 120:
        return _CACHED_STATS["data"]
    active_dedup = count_patterns(ACTIVE_DEDUP_PATTERNS)
    dedup_meta = count_patterns(DEDUP_META_PATTERNS)
    result = {
        "redis_keys": safe_dbsize(),
        "record_logs": list_len("events") + list_len("hits") + list_len("fails") + list_len("dedup:recent") + list_len("perf_events") + list_len("dedup:collisions"),
        "events": list_len("events"),
        "hits": list_len("hits"),
        "fails": list_len("fails"),
        "perf_events": list_len("perf_events"),
        "dedup_recent": list_len("dedup:recent"),
        "dedup_cache": active_dedup,
        "dedup_meta": dedup_meta,
        "dedup_records": list_len("dedup:records"),
        "dialog_cache": len(get_json("dialog_cache", []) or []),
        "temp_cache": count_patterns(["tmp:*", "temp:*", "test:*", "runtime:*"]),
        "daily_stats": daily_stats(),
    }
    _CACHED_STATS["ts"] = nt
    _CACHED_STATS["data"] = result
    return result


def trim_runtime_lists() -> None:
    """Keep Redis lists bounded after upgrade, without touching config/login/session."""
    try:
        r.ltrim("events", 0, 299)
        r.ltrim("hits", 0, 299)
        r.ltrim("fails", 0, 199)
        r.ltrim("perf_events", 0, 119)
        r.ltrim("dedup:recent", 0, 299)
        r.ltrim("dedup:records", 0, 499)
    except Exception:
        pass


def cleanup_expired_dedup_keys() -> int:
    deleted = 0
    try:
        cursor = 0
        while True:
            cursor, keys = r.scan(cursor=cursor, match="dedup:*", count=200)
            if not keys:
                if cursor == 0:
                    break
                continue
            pipe = r.pipeline()
            for k in keys:
                pipe.ttl(k)
            ttls = pipe.execute()
            for k, ttl_val in zip(keys, ttls):
                if int(ttl_val or -1) < -1:
                    r.delete(k)
                    deleted += 1
            if cursor == 0:
                break
    except Exception:
        pass
    return deleted
