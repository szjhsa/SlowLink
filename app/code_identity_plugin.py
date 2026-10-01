"""Generic code-identity plugin support.

The core only knows how to reserve, commit, and release opaque identity
candidates. Code-specific mask semantics live in the active plugin.
"""

import json
import re
import time
from typing import Any

from plugin_registry import builtin_section
from redis_store import r, sha


FEATURE_PREFIX = "codeid:features:"
PENDING_PREFIX = "codeid:pending:"
CONFIG_CACHE_TTL = 30.0
_CONFIG_CACHE: dict[str, Any] = {"ts": 0.0, "config": {}}


def clear_cache() -> None:
    _CONFIG_CACHE.update({"ts": 0.0, "config": {}})


def _config() -> dict[str, Any]:
    now = time.time()
    if now - float(_CONFIG_CACHE.get("ts") or 0.0) <= CONFIG_CACHE_TTL:
        return dict(_CONFIG_CACHE.get("config") or {})

    raw = builtin_section("code_identity", {}) or {}
    config: dict[str, Any] = {}
    try:
        if bool(raw.get("enabled", True)):
            mask_char = str(raw.get("mask_char") or "*")
            scope_regex = re.compile(str(raw.get("scope_regex") or ""))
            if (
                len(mask_char) == 1
                and {"scope", "suffix"}.issubset(scope_regex.groupindex)
            ):
                config = {
                    "mask_char": mask_char,
                    "mask_mode": str(raw.get("mask_mode") or "any_length"),
                    "mask_width": max(1, int(raw.get("mask_width") or 1)),
                    "min_fixed_chars": max(1, int(raw.get("min_fixed_chars") or 1)),
                    "ttl_seconds": max(60, int(raw.get("ttl_minutes") or 20) * 60),
                    "pending_seconds": max(1, int(raw.get("pending_seconds") or 30)),
                    "max_candidates": max(10, int(raw.get("max_candidates") or 300)),
                    "scope_regex": scope_regex,
                }
    except Exception:
        config = {}

    _CONFIG_CACHE.update({"ts": now, "config": config})
    return dict(config)


def _code_value(identity: str) -> str:
    value = str(identity or "").strip()
    return value.split(":", 1)[1] if ":" in value else value


def _candidate(identity: str, config: dict[str, Any]) -> dict[str, Any] | None:
    value = _code_value(identity)
    if not value:
        return None
    match = config["scope_regex"].fullmatch(value)
    if not match:
        return None
    scope = match.group("scope")
    suffix = match.group("suffix")
    if not scope or not suffix:
        return None

    mask_char = config["mask_char"]
    if mask_char not in suffix:
        item = {"kind": "plain", "value": suffix}
    else:
        parts = []
        fixed_chars = 0
        for segment in re.split(f"({re.escape(mask_char)}+)", suffix):
            if not segment:
                continue
            if re.fullmatch(f"{re.escape(mask_char)}+", segment):
                if config.get("mask_mode") == "exact":
                    parts.append(".{%d}" % (len(segment) * int(config["mask_width"])))
                else:
                    parts.append(".+")
            else:
                fixed_chars += len(segment)
                parts.append(re.escape(segment))
        if fixed_chars < config["min_fixed_chars"]:
            return None
        item = {
            "kind": "mask",
            "value": suffix,
            "pattern": "^" + "".join(parts) + "$",
        }

    item["scope"] = scope
    item["scope_key"] = sha(scope)
    item["member"] = json.dumps(
        {"kind": item["kind"], "value": item["value"], "pattern": item.get("pattern", "")},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return item


def _feature_key(scope_key: str) -> str:
    return FEATURE_PREFIX + scope_key


def _pending_key(scope_key: str) -> str:
    return PENDING_PREFIX + scope_key


def _load_records(key: str, now: float, oldest: float) -> list[dict[str, Any]]:
    try:
        raw_items = r.zrangebyscore(key, oldest, "+inf") or []
    except Exception:
        return []
    records = []
    for raw in raw_items:
        try:
            item = json.loads(raw)
        except Exception:
            continue
        if isinstance(item, dict):
            records.append(item)
    return records


def _matches(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if left.get("kind") == "plain" and right.get("kind") == "plain":
        return left.get("value") == right.get("value")
    if left.get("kind") == "mask" and right.get("kind") == "plain":
        try:
            return bool(re.fullmatch(str(left.get("pattern") or ""), str(right.get("value") or "")))
        except re.error:
            return False
    if left.get("kind") == "plain" and right.get("kind") == "mask":
        try:
            return bool(re.fullmatch(str(right.get("pattern") or ""), str(left.get("value") or "")))
        except re.error:
            return False
    return left.get("kind") == "mask" and right.get("kind") == "mask" and left.get("value") == right.get("value")


def _remove_oldest(key: str, limit: int) -> None:
    try:
        count = int(r.zcard(key) or 0)
        if count > limit:
            r.zremrangebyrank(key, 0, count - limit - 1)
    except Exception:
        pass


def claim_code_identities(
    code_identities: list[str],
    *,
    ttl_minutes: int = 20,
) -> tuple[bool, str, dict[str, Any] | None]:
    config = _config()
    if not config or not code_identities:
        return False, "", None

    now = time.time()
    ttl_seconds = int(config["ttl_seconds"] or max(60, int(ttl_minutes or 0) * 60))
    pending_seconds = int(config["pending_seconds"])
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for identity in code_identities:
        item = _candidate(identity, config)
        if not item:
            continue
        dedup_key = (str(item["scope_key"]), str(item["member"]))
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        candidates.append(item)

    if not candidates:
        return False, "", None

    for item in candidates:
        scope_key = str(item["scope_key"])
        existing = (
            _load_records(_feature_key(scope_key), now, now - ttl_seconds)
            + _load_records(_pending_key(scope_key), now, now)
        )
        if any(_matches(item, previous) for previous in existing):
            return True, f"相同码特征重复（{max(1, ttl_seconds // 60)}分钟内）", None

    pending_items = []
    try:
        for item in candidates:
            scope_key = str(item["scope_key"])
            pending_key = _pending_key(scope_key)
            member = str(item["member"])
            r.zadd(pending_key, {member: now + pending_seconds})
            r.expire(pending_key, pending_seconds)
            pending_items.append((pending_key, member, scope_key))
    except Exception:
        for pending_key, member, _scope_key in pending_items:
            try:
                r.zrem(pending_key, member)
            except Exception:
                pass
        raise

    return False, "", {
        "pending": pending_items,
        "candidates": candidates,
        "ttl_seconds": ttl_seconds,
        "max_candidates": int(config["max_candidates"]),
    }


def commit_code_identities(claim: dict[str, Any] | None) -> None:
    if not claim:
        return
    now = time.time()
    ttl_seconds = int(claim.get("ttl_seconds") or 0)
    if ttl_seconds <= 0:
        return
    pipe = r.pipeline()
    for pending_key, member, _scope_key in claim.get("pending") or []:
        pipe.zrem(pending_key, member)
    for item in claim.get("candidates") or []:
        feature_key = _feature_key(str(item["scope_key"]))
        pipe.zadd(feature_key, {str(item["member"]): now})
        pipe.expire(feature_key, ttl_seconds)
    try:
        pipe.execute()
    except Exception:
        return
    for item in claim.get("candidates") or []:
        _remove_oldest(_feature_key(str(item["scope_key"])), int(claim.get("max_candidates") or 300))


def release_code_identities(claim: dict[str, Any] | None) -> None:
    if not claim:
        return
    pipe = r.pipeline()
    for pending_key, member, _scope_key in claim.get("pending") or []:
        pipe.zrem(pending_key, member)
    try:
        pipe.execute()
    except Exception:
        pass
