import hashlib
import json
import time
from typing import Any

from rule_types import get_rule_type_config, is_type_available


RULE_POLICY_KEY = "rule_policies"
RULE_POLICY_CACHE_TTL = 60.0

_CACHE: dict[str, Any] = {"ts": 0.0, "items": {}}


def clear_cache() -> None:
    _CACHE.update({"ts": 0.0, "items": {}})


def policy_key(rule: str) -> str:
    return hashlib.sha256(str(rule or "").encode("utf-8")).hexdigest()


def _plugin_defaults(rule_type: str) -> dict[str, Any]:
    try:
        from plugin_registry import builtin_section

        section = builtin_section("rule_types", {}) or {}
        value = section.get(rule_type)
        return dict(value) if isinstance(value, dict) else {}
    except Exception:
        return {}


def _normalize_ttl(value: Any, default: int) -> int:
    try:
        return max(0, int(value))
    except Exception:
        return max(0, int(default))


def default_policy(rule_type: str, *, require_available: bool = True) -> dict[str, Any]:
    key = str(rule_type or "").strip().lower()
    if require_available and not is_type_available(key):
        raise ValueError("当前插件未提供该规则类型")
    try:
        config = get_rule_type_config(key)
    except ValueError:
        if require_available:
            raise
        config = {
            "id": key,
            "label": key,
            "strategy": "",
            "aliases": [],
            "dedup_strategy": "normalized_text",
            "ttl_minutes": 20,
            "forward": True,
        }
    merged = dict(config)
    merged.update(_plugin_defaults(key))
    strategy = str(merged.get("strategy") or config.get("strategy") or "").strip().lower()
    default_strategy = "normalized_text" if strategy == "line" else ""
    default_ttl = 20 if strategy == "line" else 0
    default_forward = True

    dedup_strategy = str(
        merged.get("dedup_strategy") or config.get("dedup_strategy") or default_strategy
    ).strip()
    if not dedup_strategy:
        raise ValueError("插件未提供该规则类型的去重策略")
    merged["rule_type"] = key
    merged["label"] = str(merged.get("label") or config.get("label") or key)
    merged["strategy"] = strategy
    merged["dedup_strategy"] = dedup_strategy
    merged["ttl_minutes"] = _normalize_ttl(
        merged.get("ttl_minutes", config.get("ttl_minutes")), default_ttl
    )
    merged["forward"] = bool(
        merged.get("forward", config.get("forward", default_forward))
    )
    return merged


def normalize_policy(value: dict[str, Any] | None, rule_type: str = "") -> dict[str, Any]:
    raw = dict(value or {})
    selected_type = str(raw.get("rule_type") or rule_type or "").strip().lower()
    if not selected_type:
        return {}
    try:
        base = default_policy(selected_type, require_available=False)
    except ValueError:
        return {}
    base.update(raw)
    base["rule_type"] = selected_type
    base["label"] = str(
        base.get("label")
        or selected_type
    )
    base["dedup_strategy"] = str(base.get("dedup_strategy") or "")
    base["ttl_minutes"] = _normalize_ttl(base.get("ttl_minutes"), 0)
    base["forward"] = bool(base.get("forward", True))
    return base


def should_run_identity_dedup(policy: dict[str, Any] | None) -> bool:
    if not policy:
        return True
    try:
        from redis_store import get_plugin_storage_config

        config = get_plugin_storage_config()
    except Exception:
        config = {}
    if not config:
        try:
            from plugin_runtime import call_hook

            value = call_hook("get_storage_config", {}, default={}) or {}
            config = dict(value) if isinstance(value, dict) else {}
        except Exception:
            config = {}
    field = str(config.get("identity_dedup_policy_field") or "identity_dedup")
    return bool(policy.get(field, config.get("identity_dedup_default", False)))


def policy_is_available(policy: dict[str, Any] | None) -> bool:
    if not policy:
        return True
    return is_type_available(str(policy.get("rule_type") or ""))


def _get_store(store):
    if store is not None:
        return store
    from redis_store import r

    return r


def _decode(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def all_rule_policies(store=None, *, use_cache: bool = True) -> dict[str, dict[str, Any]]:
    now = time.time()
    if store is None and use_cache:
        cached_ts = float(_CACHE.get("ts") or 0.0)
        if now - cached_ts <= RULE_POLICY_CACHE_TTL:
            return dict(_CACHE.get("items") or {})

    items: dict[str, dict[str, Any]] = {}
    try:
        raw_items = _get_store(store).hgetall(RULE_POLICY_KEY) or {}
    except Exception:
        raw_items = {}
    for key, raw in raw_items.items():
        policy = normalize_policy(_decode(raw))
        if policy:
            items[str(key)] = policy

    if store is None:
        _CACHE.update({"ts": now, "items": items})
    return dict(items)


def get_rule_policy(rule: str, store=None) -> dict[str, Any] | None:
    if not str(rule or ""):
        return None
    return all_rule_policies(store=store).get(policy_key(rule))


def save_rule_policy(
    rule: str,
    rule_type: str,
    store=None,
    overrides: dict[str, Any] | None = None,
    allow_unavailable: bool = False,
) -> dict[str, Any]:
    rule = str(rule or "")
    if not rule:
        raise ValueError("规则不能为空")
    policy = default_policy(rule_type, require_available=not allow_unavailable)
    if overrides:
        policy.update(overrides)
    policy = normalize_policy(policy, rule_type)
    if not policy:
        raise ValueError("规则策略无效")
    _get_store(store).hset(
        RULE_POLICY_KEY,
        policy_key(rule),
        json.dumps(policy, ensure_ascii=False, separators=(",", ":")),
    )
    clear_cache()
    return policy


def delete_rule_policy(rule: str, store=None) -> bool:
    if not str(rule or ""):
        return False
    removed = _get_store(store).hdel(RULE_POLICY_KEY, policy_key(rule))
    clear_cache()
    return bool(removed)
