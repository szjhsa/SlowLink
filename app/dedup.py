import hashlib
import json
import os
import re
import sys
import time
import unicodedata
from typing import Any

import regex as _regex

_APP_DIR = os.path.dirname(os.path.abspath(__file__))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from redis_store import r, sha, format_time
from plugin_registry import builtin_section
from plugin_runtime import call_hook

try:
    from redis_store import add_lottery_collision as _add_collision
    from redis_store import is_collision_exempt as _is_collision_exempt
except (ImportError, AttributeError):
    def _add_collision(item: dict) -> None:
        try:
            collision_list = str(item.get("_collision_list") or "dedup:collisions")
            r.lpush(collision_list, json.dumps(item, ensure_ascii=False))
            r.ltrim(collision_list, 0, 199)
        except Exception:
            pass

    def _is_collision_exempt(identity: str, dedup_id: str) -> bool:
        try:
            prefix = "dedup:collision_exempt:"
            return bool(r.sismember(prefix + sha(identity), str(dedup_id)))
        except Exception:
            return False


EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002700-\U000027BF"
    "\U00002600-\U000026FF"
    "]+",
    flags=re.UNICODE,
)

RECENT_LIST = "dedup:recent"
META_PREFIX = "dedup:meta:"
_TTL_CACHE: dict[str, tuple[float, int]] = {}
_TTL_CACHE_TTL = 30.0
_CORRELATION_WINDOW_CACHE: dict[str, object] = {"ts": 0.0, "value": 600}

LOTTERY_KWS: list[str] = []
JOINT_LOTTERY_KWS: list[str] = []
LONG_TERM_KWS: list[str] = []
TTL_DEFAULTS: dict[str, int] = {"other": 20}


def clear_ttl_cache() -> None:
    _TTL_CACHE.clear()
    _CORRELATION_WINDOW_CACHE.update({"ts": 0.0, "value": 600})


def reload_builtins() -> None:
    global LOTTERY_KWS, JOINT_LOTTERY_KWS, LONG_TERM_KWS, TTL_DEFAULTS
    section = builtin_section("dedup", {}) or {}
    LOTTERY_KWS = list(section.get("lottery_keywords") or [])
    JOINT_LOTTERY_KWS = list(section.get("joint_lottery_keywords") or [])
    LONG_TERM_KWS = list(section.get("long_term_keywords") or [])
    ttl_defaults = section.get("ttl_defaults")
    if isinstance(ttl_defaults, dict) and ttl_defaults:
        TTL_DEFAULTS = {
            str(key): max(0, int(value))
            for key, value in ttl_defaults.items()
        }
    else:
        TTL_DEFAULTS = {"other": 20}
    window = section.get("correlation_window_seconds")
    try:
        window = max(60, int(window)) if window is not None else 600
    except Exception:
        window = 600
    _CORRELATION_WINDOW_CACHE.update({"ts": 0.0, "value": window})


def _now() -> int:
    return int(time.time())


def _ts() -> str:
    return format_time()


def _meta_key(dedup_id: str) -> str:
    return META_PREFIX + sha(dedup_id)


def _load_meta(dedup_id: str) -> dict[str, Any]:
    raw = r.get(_meta_key(dedup_id))
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _save_meta(meta: dict[str, Any], ttl_seconds: int) -> None:
    r.setex(
        _meta_key(meta["dedup_id"]),
        ttl_seconds,
        json.dumps(meta, ensure_ascii=False),
    )


def _release_new_keys(keys: list[str]) -> None:
    keys = [key for key in keys or [] if key]
    if keys:
        try:
            r.delete(*keys)
        except Exception:
            pass


def _push_recent(item: dict[str, Any], limit: int = 120) -> None:
    item = dict(item)
    item.setdefault("time", _ts())
    pipe = r.pipeline()
    pipe.lpush(RECENT_LIST, json.dumps(item, ensure_ascii=False))
    pipe.ltrim(RECENT_LIST, 0, limit - 1)
    pipe.execute()


def classify_activity(text: str) -> str:
    result = call_hook("classify_activity", {"text": text}, default=None)
    return result if isinstance(result, str) and result else "other"


def ttl_policy_for_text(text: str) -> str:
    result = call_hook("dedup_ttl_policy", {"text": text}, default=None)
    return result if isinstance(result, str) and result else "normal"


def extract_lottery_identity(text: str) -> str:
    result = call_hook("extract_lottery_identity", {"text": text}, default=None)
    return result if isinstance(result, str) else ""


def extract_lottery_global_identity(text: str) -> str:
    result = call_hook(
        "extract_lottery_global_identity",
        {"text": text},
        default=None,
    )
    return result if isinstance(result, str) else ""


def extract_lottery_template_identity(
    text: str,
    message_link: str = "",
    source: str = "",
) -> str:
    result = call_hook(
        "extract_lottery_template_identity",
        {"text": text, "message_link": message_link, "source": source},
        default=None,
    )
    return result if isinstance(result, str) else ""


def _register_renew_code_fingerprints(text: str) -> list[str]:
    result = call_hook(
        "register_renew_fingerprints",
        {"text": text},
        default=None,
    )
    return [str(value) for value in result if str(value)] if isinstance(result, list) else []


def _invite_path_code_fingerprints(text: str) -> list[str]:
    result = call_hook(
        "invite_path_fingerprints",
        {"text": text},
        default=None,
    )
    return [str(value) for value in result if str(value)] if isinstance(result, list) else []


def _generic_normalize(text: str) -> str:
    raw = unicodedata.normalize("NFKC", text or "")
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    raw = re.sub(r"[\u200b\u200c\u200d\ufeff\u2060]", "", raw)
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    lines = [
        line for line in lines
        if not re.fullmatch(r"(?:https?://)?t\.me/\S+", line, flags=re.I)
    ]
    base = "\n".join(lines) if lines else raw
    base = (
        base.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&apos;", "'")
    )
    base = re.sub(r"\*\*|__|~~|`", " ", base)
    base = re.sub(r"https?://\S+", " [URL] ", base)
    base = re.sub(
        r"\b[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}\b",
        " [UUID] ",
        base,
        flags=re.I,
    )
    base = re.sub(r"\b[a-f0-9]{40,}\b", " [HASH] ", base, flags=re.I)
    base = re.sub(r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b", " [DATE] ", base)
    base = re.sub(r"\b\d{1,2}:\d{2}(?::\d{2})?\b", " [TIME] ", base)
    base = re.sub(r"\b\d+/\d+\b", " [FRAC] ", base)
    base = re.sub(r"\b\d+\.\d+%?\b", " [PCT] ", base)
    base = re.sub(r"\b\d+%\b", " [PCT] ", base)
    base = re.sub(r"\b\d{4,}\b", " [NUM] ", base)
    base = EMOJI_RE.sub(" ", base)
    base = re.sub(r"[\[\]【】（）(){}<>《》|:：;；,，。.!！?？#*_`~+=/\\\-@&%$]", " ", base)
    return re.sub(r"\s+", " ", base).strip().lower()


def normalize_for_text_dedup(text: str) -> str:
    result = call_hook("normalize_dedup_text", {"text": text}, default=None)
    if isinstance(result, str):
        return result
    return _generic_normalize(text)


def _correlation_window_seconds() -> int:
    now = time.time()
    cached_ts = float(_CORRELATION_WINDOW_CACHE.get("ts") or 0.0)
    if now - cached_ts <= _TTL_CACHE_TTL:
        try:
            return max(60, int(_CORRELATION_WINDOW_CACHE.get("value") or 600))
        except Exception:
            return 600
    try:
        value = max(60, int(r.get("dedup_correlation_window_seconds") or 600))
    except Exception:
        value = 600
    _CORRELATION_WINDOW_CACHE.update({"ts": now, "value": value})
    return value


def _configured_correlation_mode() -> str:
    try:
        value = str(r.get("dedup_lottery_template_mode") or "global")
    except Exception:
        value = "global"
    return value if value in {"global", "id", "off"} else "global"


def ttl_minutes_for_activity(activity: str, fallback: int | None = None) -> int:
    activity = str(activity or "other")
    key = (
        "dedup_other_minutes"
        if activity == "other"
        else f"dedup_{activity}_minutes"
    )
    default = int(TTL_DEFAULTS.get(activity, TTL_DEFAULTS.get("other", 20)))
    if fallback is not None:
        default = max(0, int(fallback))
    now = time.time()
    cached = _TTL_CACHE.get(key)
    if cached and now - cached[0] <= _TTL_CACHE_TTL:
        return cached[1]
    try:
        value = max(0, int(r.get(key) or default))
    except Exception:
        value = default
    _TTL_CACHE[key] = (now, value)
    return value


def ttl_minutes_for_profile(profile: dict[str, Any], fallback: int | None = None) -> int:
    result = call_hook(
        "ttl_minutes_for_profile",
        {"profile": profile, "fallback": fallback},
        default=None,
    )
    if isinstance(result, int):
        return max(0, result)
    if "ttl_minutes" in profile:
        try:
            return max(0, int(profile.get("ttl_minutes") or 0))
        except Exception:
            pass
    key = str(profile.get("ttl_key") or "")
    if key:
        try:
            return max(0, int(r.get(key) or ttl_minutes_for_activity("other", fallback)))
        except Exception:
            pass
    return ttl_minutes_for_activity(str(profile.get("activity") or "other"), fallback)


def build_profile(
    text: str,
    message_link: str = "",
    source: str = "",
    policy: dict[str, Any] | None = None,
    code_identities: list[str] | None = None,
) -> dict[str, Any]:
    result = call_hook(
        "build_dedup_profile",
        {
            "text": text,
            "message_link": message_link,
            "source": source,
            "policy": policy,
            "code_identities": code_identities or [],
            "correlation_mode": _configured_correlation_mode(),
        },
        default=None,
    )
    if isinstance(result, dict):
        return result

    normalized = normalize_for_text_dedup(text)
    text_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    if policy:
        rule_type = str(policy.get("rule_type") or "other")
        strategy = str(policy.get("dedup_strategy") or "normalized_text")
        ttl_minutes = max(0, int(policy.get("ttl_minutes") or 0))
        return {
            "activity": rule_type or "other",
            "ttl_policy": "rule_policy",
            "core": normalized[:300],
            "dedup_id": "text:" + text_hash,
            "message_link": message_link,
            "text_hash": text_hash,
            "dedup_strategy": strategy,
            "rule_type": rule_type,
            "rule_policy": dict(policy),
            "ttl_minutes": ttl_minutes,
            "correlation_keys": [],
            "correlation_mode": "off",
            "strict_identity_conflict": False,
            "reason_labels": {},
        }

    return {
        "activity": classify_activity(text),
        "ttl_policy": ttl_policy_for_text(text),
        "core": normalized[:300],
        "dedup_id": "text:" + text_hash,
        "message_link": message_link,
        "text_hash": text_hash,
        "dedup_strategy": "normalized_text",
        "correlation_keys": [],
        "correlation_mode": "off",
        "strict_identity_conflict": False,
        "reason_labels": {},
    }


def _reason(profile: dict[str, Any], key: str, fallback: str, ttl_minutes: int) -> str:
    labels = profile.get("reason_labels")
    value = labels.get(key) if isinstance(labels, dict) else ""
    text = str(value or fallback)
    try:
        return text.format(ttl=int(ttl_minutes))
    except Exception:
        return text


def _correlation_keys(profile: dict[str, Any]) -> list[str]:
    raw = profile.get("correlation_keys")
    if not isinstance(raw, list):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for value in raw:
        text = str(value or "")
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _correlation_prefix(profile: dict[str, Any]) -> str:
    return str(profile.get("correlation_key_prefix") or "dedup:correlation:")


def _add_correlation_collision(
    profile: dict[str, Any],
    identity: str,
    existing_id: str,
    source: str,
    message_link: str,
) -> None:
    item = {
        "identity": identity,
        "dedup_id": profile.get("dedup_id", ""),
        "first_dedup_id": existing_id,
        "source": source or "",
        "link": message_link or profile.get("message_link", ""),
        "_collision_list": profile.get("collision_list") or "dedup:collisions",
    }
    try:
        _add_collision(item)
    except Exception:
        pass


def check_and_mark(
    text: str,
    message_link: str,
    ttl_minutes: int | None = 20,
    mode: str = "strict",
    source: str = "",
    policy: dict[str, Any] | None = None,
    code_identities: list[str] | None = None,
) -> tuple[bool, str, dict]:
    profile = build_profile(
        text,
        message_link,
        source,
        policy=policy,
        code_identities=code_identities,
    )
    content_key = "dedup:" + profile["dedup_id"]
    link_key = "dedup:link:" + sha(message_link) if message_link else ""
    correlation_mode = str(
        (policy or {}).get("lottery_template_mode")
        or profile.get("correlation_mode")
        or "off"
    )
    correlation_keys = _correlation_keys(profile) if correlation_mode == "global" else []
    effective_correlation_keys = [
        identity
        for identity in correlation_keys
        if not _is_collision_exempt(identity, profile["dedup_id"])
    ]
    correlation_prefix = _correlation_prefix(profile)
    correlation_redis_keys = [
        correlation_prefix + sha(identity)
        for identity in effective_correlation_keys
    ]

    real_ttl_minutes = (
        ttl_minutes_for_profile(profile, ttl_minutes)
        if ttl_minutes is None
        else int(ttl_minutes)
    )
    if int(real_ttl_minutes) <= 0:
        return False, _reason(profile, "no_ttl", "该类型已设置为不去重", 0), profile

    ttl_seconds = max(60, int(real_ttl_minutes) * 60)
    correlation_ttl = _correlation_window_seconds()
    dedup_id = profile["dedup_id"]

    pipe = r.pipeline()
    pipe.set(content_key, dedup_id, ex=ttl_seconds, nx=True)
    if message_link:
        pipe.set(link_key, dedup_id, ex=ttl_seconds, nx=True)
    for key in correlation_redis_keys:
        pipe.set(key, dedup_id, ex=correlation_ttl, nx=True)
    results = pipe.execute()

    content_is_new = bool(results[0])
    result_index = 1
    link_is_new = bool(results[result_index]) if message_link else True
    if message_link:
        result_index += 1
    correlation_new = [
        bool(results[result_index + offset])
        for offset in range(len(correlation_redis_keys))
    ]

    new_keys: list[str] = []
    if content_is_new:
        new_keys.append(content_key)
    if message_link and link_is_new:
        new_keys.append(link_key)
    for key, is_new in zip(correlation_redis_keys, correlation_new):
        if is_new:
            new_keys.append(key)

    if not content_is_new:
        _release_new_keys(new_keys)
        reason_category = str(profile.get("reason_category") or "text")
        fallback_reasons = {
            "identity_fallback": "未识别到完整码，按相同文本内容重复（{ttl}分钟内）",
            "code": "相同完整码重复（{ttl}分钟内）",
            "lottery_id": "相同标识重复（{ttl}分钟内）",
            "text": "相同文本内容重复（{ttl}分钟内）",
        }
        reason = _reason(
            profile,
            reason_category,
            fallback_reasons.get(reason_category, fallback_reasons["text"]),
            real_ttl_minutes,
        )
        _record_duplicate(dedup_id, profile, reason, source, message_link, ttl_seconds)
        return True, reason, profile

    if not link_is_new:
        _release_new_keys(new_keys)
        reason = _reason(
            profile,
            "link",
            "同一条原消息链接重复（{ttl}分钟内）",
            real_ttl_minutes,
        )
        _record_duplicate(dedup_id, profile, reason, source, message_link, ttl_seconds)
        return True, reason, profile

    for index, key in enumerate(correlation_redis_keys):
        if correlation_new[index]:
            continue
        existing_id = r.get(key) or dedup_id
        conflict_prefix = str(profile.get("identity_conflict_prefix") or "")
        explicit_conflict = (
            bool(profile.get("strict_identity_conflict"))
            and bool(conflict_prefix)
            and str(existing_id).startswith(conflict_prefix)
            and str(existing_id) != str(dedup_id)
        )
        if explicit_conflict:
            continue
        new_correlation_keys = [
            candidate
            for candidate, is_new in zip(correlation_redis_keys, correlation_new)
            if is_new
        ]
        _release_new_keys(new_correlation_keys)
        reason = _reason(profile, "template", "同一内容的不同模板重复（10分钟内）", 10)
        _add_correlation_collision(
            profile,
            effective_correlation_keys[index],
            str(existing_id),
            source,
            message_link,
        )
        _record_duplicate(existing_id, profile, reason, source, message_link, ttl_seconds)
        return True, reason, profile

    redis_keys = [content_key]
    if link_key:
        redis_keys.append(link_key)
    redis_keys.extend(
        key for key, is_new in zip(correlation_redis_keys, correlation_new) if is_new
    )
    _register_new(profile, redis_keys, ttl_seconds, source)
    return False, "未重复", profile


def _register_new(
    profile: dict[str, Any],
    redis_keys: list[str],
    ttl_seconds: int,
    source: str,
) -> None:
    expire_at = _now() + ttl_seconds
    meta = {
        "dedup_id": profile["dedup_id"],
        "activity": profile.get("activity", ""),
        "core": profile.get("core", "")[:300],
        "message_link": profile.get("message_link", ""),
        "first_source": source or "",
        "duplicate_count": 0,
        "duplicate_sources": [],
        "first_seen": _ts(),
        "last_seen": _ts(),
        "expire_at": expire_at,
        "redis_keys": redis_keys,
    }
    _save_meta(meta, ttl_seconds)
    _push_recent({
        "action": "first",
        "dedup_id": profile["dedup_id"],
        "activity": profile.get("activity", ""),
        "source": source or "",
        "reason": "首次命中",
        "core": profile.get("core", "")[:180],
        "link": profile.get("message_link", ""),
        "expire_at": expire_at,
    })


def _record_duplicate(
    dedup_id: str,
    profile: dict[str, Any],
    reason: str,
    source: str,
    link: str,
    ttl_seconds: int,
) -> None:
    meta = _load_meta(dedup_id)
    if meta:
        sources = list(meta.get("duplicate_sources") or [])
        if source and source not in sources and source != meta.get("first_source"):
            sources.append(source)
        meta["duplicate_sources"] = sources[-30:]
        meta["duplicate_count"] = int(meta.get("duplicate_count") or 0) + 1
        meta["last_seen"] = _ts()
        remain = max(60, int(meta.get("expire_at", _now() + ttl_seconds)) - _now())
        _save_meta(meta, remain)
    _push_recent({
        "action": "duplicate",
        "dedup_id": dedup_id,
        "activity": profile.get("activity", ""),
        "source": source or "",
        "reason": reason,
        "core": profile.get("core", "")[:180],
        "link": link or profile.get("message_link", ""),
    })


def list_dedup_recent(limit: int = 30) -> list[dict[str, Any]]:
    out = []
    seen = set()
    now = _now()
    for raw in r.lrange(RECENT_LIST, 0, max(limit * 4, limit) - 1):
        try:
            item = json.loads(raw)
        except Exception:
            continue
        did = item.get("dedup_id") or ""
        if not did or did in seen:
            continue
        seen.add(did)
        meta = _load_meta(did)
        item["duplicate_count"] = (
            int(meta.get("duplicate_count") or 0)
            if meta
            else int(item.get("duplicate_count") or 0)
        )
        item["first_source"] = meta.get("first_source", "") if meta else ""
        item["duplicate_sources"] = meta.get("duplicate_sources", []) if meta else []
        expire_at = int(meta.get("expire_at") or item.get("expire_at") or 0) if (meta or item) else 0
        item["ttl_left"] = max(0, expire_at - now) if expire_at else 0
        item["active"] = bool(meta)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def release_dedup(dedup_id: str) -> bool:
    dedup_id = (dedup_id or "").strip()
    if not dedup_id:
        return False
    meta = _load_meta(dedup_id)
    keys = list(meta.get("redis_keys") or []) if meta else []
    if not meta:
        try:
            for k in r.scan_iter(match="dedup:*", count=200):
                try:
                    if str(r.type(k) or "") == "string" and r.get(k) == dedup_id:
                        keys.append(k)
                except Exception:
                    continue
        except Exception:
            pass
    keys.append(_meta_key(dedup_id))
    keys.append("dedup:" + dedup_id)
    deleted = r.delete(*[key for key in set(keys) if key])
    _push_recent({"action": "release", "dedup_id": dedup_id, "reason": "手动解除去重"})
    return bool(deleted)


reload_builtins()
