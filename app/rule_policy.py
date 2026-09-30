import hashlib
import json
import time
from typing import Any

from rule_types import get_rule_type_config, is_type_available


RULE_POLICY_KEY = "rule_policies"
RULE_POLICY_CACHE_TTL = 60.0

RULE_TYPE_DEFAULTS: dict[str, dict[str, Any]] = {
    "code": {
        "label": "码子",
        "dedup_strategy": "code_identity",
        "ttl_minutes": 20,
        "lottery_template_mode": "off",
        "forward": True,
    },
    "lottery": {
        "label": "抽奖",
        "dedup_strategy": "lottery_identity",
        "ttl_minutes": 720,
        "lottery_template_mode": "global",
        "forward": True,
    },
    "keyword": {
        "label": "关键词",
        "dedup_strategy": "normalized_text",
        "ttl_minutes": 20,
        "lottery_template_mode": "off",
        "forward": True,
    },
    "exclude": {
        "label": "排除",
        "dedup_strategy": "none",
        "ttl_minutes": 0,
        "lottery_template_mode": "off",
        "forward": False,
    },
}

STRATEGY_POLICY_DEFAULTS: dict[str, dict[str, Any]] = {
    "code": {
        "dedup_strategy": "code_identity",
        "ttl_minutes": 20,
        "lottery_template_mode": "off",
        "forward": True,
    },
    "lottery": {
        "dedup_strategy": "lottery_identity",
        "ttl_minutes": 720,
        "lottery_template_mode": "global",
        "forward": True,
    },
    "line": {
        "dedup_strategy": "normalized_text",
        "ttl_minutes": 20,
        "lottery_template_mode": "off",
        "forward": True,
    },
}

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
    base = RULE_TYPE_DEFAULTS.get(key)
    if not base:
        config = get_rule_type_config(key)
        strategy = str(config.get("strategy") or "").strip().lower()
        strategy_defaults = STRATEGY_POLICY_DEFAULTS.get(strategy)
        if not strategy_defaults:
            raise ValueError("未知规则类型")
        base = dict(strategy_defaults)
        base["label"] = str(config.get("label") or key)
    merged = dict(base)
    merged.update(_plugin_defaults(key))
    merged["rule_type"] = key
    merged["label"] = str(merged.get("label") or base["label"])
    merged["dedup_strategy"] = str(
        merged.get("dedup_strategy") or base["dedup_strategy"]
    )
    mode = str(merged.get("lottery_template_mode") or base["lottery_template_mode"])
    merged["lottery_template_mode"] = (
        mode if mode in {"global", "id", "off"} else base["lottery_template_mode"]
    )
    merged["ttl_minutes"] = _normalize_ttl(
        merged.get("ttl_minutes"), base["ttl_minutes"]
    )
    merged["forward"] = bool(merged.get("forward", base["forward"]))
    return merged


def normalize_policy(value: dict[str, Any] | None, rule_type: str = "") -> dict[str, Any]:
    raw = dict(value or {})
    selected_type = str(raw.get("rule_type") or rule_type or "").strip().lower()
    try:
        base = default_policy(selected_type, require_available=False)
    except ValueError:
        return {}
    base.update(raw)
    base["rule_type"] = selected_type
    base["label"] = str(
        base.get("label")
        or RULE_TYPE_DEFAULTS.get(selected_type, {}).get("label")
        or selected_type
    )
    base["dedup_strategy"] = str(base.get("dedup_strategy") or "")
    mode = str(base.get("lottery_template_mode") or "off")
    base["lottery_template_mode"] = mode if mode in {"global", "id", "off"} else "off"
    base["ttl_minutes"] = _normalize_ttl(base.get("ttl_minutes"), 0)
    base["forward"] = bool(base.get("forward", True))
    return base


def should_run_code_dedup(policy: dict[str, Any] | None) -> bool:
    if not policy:
        return True
    return (
        str(policy.get("rule_type") or "") == "code"
        or str(policy.get("dedup_strategy") or "") == "code_identity"
    )


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
